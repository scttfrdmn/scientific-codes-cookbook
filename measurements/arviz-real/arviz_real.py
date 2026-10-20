#!/usr/bin/env python3
"""arviz-real: ArviZ 1.3's convergence diagnostics on every posteriordb 1.1.0 reference posterior,
checked against two independent implementations on the same draws.

    python3 -u arviz_real.py <pdb dir> <out dir> <workers>

Part R (reference draws). posteriordb ships 57 reference posteriors (10 chains x 1000 thinned
draws) with the ESS and R-hat its authors computed in R with the `posterior` package. ArviZ reads
the same doubles, so its ess_bulk, ess_tail and r_hat are compared with R's, value by value.

Part F (fresh fits). CmdStan samples all 57 (4 chains x 1000, sig_figs=18). Each fit's CSVs are
read by ArviZ (az.from_cmdstanpy) and by Stan's own `stansummary` (cmdstanpy's fit.summary), and
every scalar's mean, sd, quantiles, MCSE, ESS_bulk, ESS_tail and R-hat is compared (see
ASSERTED_CONVERGED for which, and where). The sampler is not under
test here; it only produces real draws to diagnose.

THE TOLERANCE. Each estimator is a deterministic function of the same doubles, so two correct
implementations of the same definition differ only by rounding in sums and FFTs over <= 10,000
values (~1e-13 relative). REL_TOL = 1e-9 is four orders above that and three below the smallest
*definitional* difference measured (the folded R-hat of posterior 0.0.x, ~1e-6). So a value inside
it is the same definition computed correctly; a value outside it is a different definition.

Also asserted: ESS_bulk, ESS_tail and the bulk half of R-hat are bit-identical under two strictly
monotone maps of the real draws (3.7x + 112.5 and x^3) wherever the map preserves every rank in
floating point; ArviZ flags exactly the posteriors stansummary flags (any R-hat > 1.01). A scalar
with a chain that never moves (a discrete generated quantity, say) makes ESS and R-hat 0/0, so it
is compared and reported, not asserted; so is an integer-valued scalar (ties dominate its ranks).
"""
import concurrent.futures as cf
import hashlib
import json
import multiprocessing
import os
import re
import resource
import sys
import time
import traceback
import zipfile

import numpy as np

PDB, OUT, WORKERS = sys.argv[1], sys.argv[2], int(sys.argv[3])
SEED = 20261020
CHAINS, WARMUP, SAMPLING = 4, 1000, 1000
REL_TOL = 1e-9
RHAT_FLAG = 1.01
WORK = "/tmp/work"
SLOW_FIRST = ["diamonds-diamonds", "mcycle_gp-accel_gp", "one_comp_mm_elim_abs-one_comp_mm_elim_abs",
              "earnings-logearn_logheight_male", "bball_drive_event_1-hmm_drive_1"]
FAILS = []


def s(v):
    """Every reported value goes through str() -- a tuple-like never unpacks into a format."""
    return str(v)


def check(ok, msg):
    if not bool(ok):
        FAILS.append(msg)
        print("CHECK FAILED: " + msg, flush=True)


def rel(a, b):
    """Relative difference; NaN only when exactly one side is NaN (both NaN agree, e.g. constant)."""
    a, b = float(a), float(b)
    if np.isnan(a) and np.isnan(b):
        return 0.0
    if np.isnan(a) or np.isnan(b):
        return float("inf")
    if a == b:
        return 0.0
    return abs(a - b) / max(abs(a), abs(b))


def quiet():
    import logging
    import cmdstanpy
    cmdstanpy.utils.get_logger().setLevel(logging.WARNING)
    return cmdstanpy


def compile_model(model_name):
    cmdstanpy = quiet()
    wd = os.path.join(WORK, "models", model_name)
    os.makedirs(wd, exist_ok=True)
    stan = os.path.join(wd, model_name + ".stan")
    with open(os.path.join(PDB, "models/stan/%s.stan" % model_name)) as src, open(stan, "w") as dst:
        dst.write(src.read())
    t0 = time.time()
    model = cmdstanpy.CmdStanModel(stan_file=stan)
    return model_name, model.exe_file, time.time() - t0


def info_of(pname):
    return json.load(open(os.path.join(PDB, "reference_posteriors/draws/info/%s.info.json" % pname)))


def sample_job(pname, data_name, exe):
    """Sample one posterior; stansummary runs here too (it is part of producing the reference)."""
    cmdstanpy = quiet()
    model = cmdstanpy.CmdStanModel(exe_file=exe)
    pd_dir = os.path.join(WORK, "post", pname)
    os.makedirs(pd_dir, exist_ok=True)
    data_file = os.path.join(pd_dir, "data.json")
    with zipfile.ZipFile(os.path.join(PDB, "data/data/%s.json.zip" % data_name)) as z:
        with open(data_file, "wb") as fh:
            fh.write(z.read(z.namelist()[0]))
    ctl = info_of(pname)["inference"]["method_arguments"].get("control") or {}
    kw = {}
    if "adapt_delta" in ctl:
        kw["adapt_delta"] = float(ctl["adapt_delta"])
    if "max_treedepth" in ctl:
        kw["max_treedepth"] = int(ctl["max_treedepth"])
    od = os.path.join(pd_dir, "fit")
    os.makedirs(od, exist_ok=True)
    t0 = time.time()
    fit = model.sample(data=data_file, chains=CHAINS, parallel_chains=1, seed=SEED,
                       iter_warmup=WARMUP, iter_sampling=SAMPLING, show_progress=False,
                       sig_figs=18, output_dir=od, **kw)
    sample_s = time.time() - t0
    t0 = time.time()
    summ = fit.summary(sig_figs=18)   # bin/stansummary on the CSVs
    stansummary_s = time.time() - t0
    summ.to_csv(os.path.join(pd_dir, "stansummary.tsv"), sep="\t")
    draws = np.ascontiguousarray(np.asarray(fit.draws()))
    return {"posterior": pname, "csv": list(fit.runset.csv_files), "sample_s": sample_s,
            "stansummary_s": stansummary_s,
            "draws_sha256": hashlib.sha256(draws.tobytes()).hexdigest()}


def flat(ds):
    """xarray Dataset (chain, draw, *dims) -> {stan_name: (chain, draw) float array}."""
    out = {}
    for v in ds.data_vars:
        a = np.asarray(ds[v].values, dtype=float)
        extra = a.shape[2:]
        if not extra:
            out[v] = a
            continue
        for idx in np.ndindex(*extra):
            out["%s[%s]" % (v, ",".join(str(i + 1) for i in idx))] = a[(slice(None), slice(None)) + idx]
    return out


def az_diag(x):
    """ArviZ's diagnostics on one (chain, draw) array, at full precision."""
    import arviz as az
    d = az.convert_to_dataset({"v": x})
    sd = float(np.std(x, ddof=1))
    e_mean = float(az.ess(d, method="mean")["v"])
    e_id = float(az.ess(d, method="identity")["v"])
    e_bulk = float(az.ess(d, method="bulk")["v"])
    return {"ess_bulk": e_bulk,
            "ess_tail95": float(az.ess(d, method="tail", prob=0.95)["v"]),
            "ess_tail_default": float(az.ess(d, method="tail")["v"]),
            "r_hat": float(az.rhat(d)["v"]),
            "rhat_bulk": float(az.rhat(d, method="z_scale")["v"]),
            "rhat_folded": float(az.rhat(d, method="folded")["v"]),
            "mcse_mean": float(az.mcse(d, method="mean")["v"]),
            # two other definitions of the same quantity, to identify stansummary's
            "sd_over_sqrt_ess_identity": sd / np.sqrt(e_id) if e_id > 0 else float("nan"),
            "sd_over_sqrt_ess_bulk": sd / np.sqrt(e_bulk) if e_bulk > 0 else float("nan"),
            "ess_mean": e_mean}


def ranks_kept(x, y):
    from scipy.stats import rankdata
    return bool(np.array_equal(rankdata(x, method="average"), rankdata(y, method="average")))


def part_r(rec):
    """ArviZ vs R `posterior` on posteriordb's own reference draws."""
    rows = []
    t0 = time.time()
    names = sorted(f[:-9] for f in os.listdir(os.path.join(PDB, "reference_posteriors/draws/draws"))
                   if f.endswith(".json.zip"))
    for pname in names:
        info = info_of(pname)
        sess = (info.get("versions") or {}).get("r_session") or ""
        m = re.search(r"posterior_([0-9.]+)", sess)
        pver = m.group(1) if m else "unknown"
        with zipfile.ZipFile(os.path.join(PDB, "reference_posteriors/draws/draws/%s.json.zip" % pname)) as z:
            ref = json.loads(z.read(z.namelist()[0]))
        dg = info["diagnostics"]
        # posteriordb spells this key two ways, and a one-parameter posterior stores scalars.
        dn = list(np.atleast_1d((dg.get("diagnostic_information") or dg.get("diagnostics_information"))["names"]))
        pub = {k: np.atleast_1d(dg[k]).tolist() for k in
               ("effective_sample_size_bulk", "effective_sample_size_tail", "r_hat")}
        for i, k in enumerate(dn):
            x = np.array([c[k] for c in ref], dtype=float)
            d = az_diag(x)
            rows.append({"posterior": pname, "param": k, "posterior_pkg": pver, "chains": x.shape[0],
                         "draws": x.shape[1],
                         "az_ess_bulk": d["ess_bulk"], "R_ess_bulk": pub["effective_sample_size_bulk"][i],
                         "az_ess_tail95": d["ess_tail95"], "az_ess_tail_default": d["ess_tail_default"],
                         "R_ess_tail": pub["effective_sample_size_tail"][i],
                         "az_r_hat": d["r_hat"], "az_rhat_bulk": d["rhat_bulk"],
                         "az_rhat_folded": d["rhat_folded"], "R_r_hat": pub["r_hat"][i]})
    rec("partR_arviz_s", "%.2f" % (time.time() - t0))
    keys = list(rows[0].keys())
    with open(os.path.join(OUT, "reference_diag.tsv"), "w") as fh:
        fh.write("\t".join(keys) + "\n")
        for r in rows:
            fh.write("\t".join(repr(r[k]) if isinstance(r[k], float) else s(r[k]) for k in keys) + "\n")
    rec("partR_posteriors", len(names))
    rec("partR_params", len(rows))
    eb = max(rel(r["az_ess_bulk"], r["R_ess_bulk"]) for r in rows)
    et = max(rel(r["az_ess_tail95"], r["R_ess_tail"]) for r in rows)
    etd = max(rel(r["az_ess_tail_default"], r["R_ess_tail"]) for r in rows)
    rec("partR_max_rel_ess_bulk_vs_R", "%.3e" % eb)
    rec("partR_max_rel_ess_tail95_vs_R", "%.3e" % et)
    rec("partR_max_rel_ess_tail_DEFAULT_vs_R", "%.3e" % etd)
    check(len(names) == 57, "expected 57 reference posteriors, found %d" % len(names))
    check(eb <= REL_TOL, "ess_bulk differs from R posterior by %.3e" % eb)
    check(et <= REL_TOL, "ess_tail(prob=0.95) differs from R posterior by %.3e" % et)
    for v in sorted({r["posterior_pkg"] for r in rows}):
        sub = [r for r in rows if r["posterior_pkg"] == v]
        dr = [abs(r["az_r_hat"] - r["R_r_hat"]) for r in sub]
        # where R's value differs, is it because R took the bulk half and ArviZ the folded half?
        bulk_eq = sum(1 for r in sub if abs(r["az_r_hat"] - r["R_r_hat"]) > REL_TOL
                      and abs(r["az_rhat_bulk"] - r["R_r_hat"]) <= REL_TOL)
        fold_side = sum(1 for r in sub if abs(r["az_r_hat"] - r["R_r_hat"]) > REL_TOL
                        and r["az_rhat_folded"] > r["az_rhat_bulk"])
        rec("partR_r_hat_posterior_%s" % v,
            "posteriors %d params %d max_abs_diff %.3e within_tol %d differ %d differ_where_folded_is_max %d "
            "differ_where_R_equals_bulk %d"
            % (len({r["posterior"] for r in sub}), len(sub), max(dr), sum(1 for x in dr if x <= REL_TOL),
               sum(1 for x in dr if x > REL_TOL), fold_side, bulk_eq))
        if v == "1.6.1":
            check(max(dr) <= REL_TOL, "r_hat differs from R posterior 1.6.1 by %.3e" % max(dr))
    return rows


SS_COL = {"mean": "Mean", "sd": "StdDev", "mcse": "MCSE", "ess_bulk": "ESS_bulk",
          "ess_tail": "ESS_tail", "r_hat": "R_hat", "q05": "5%", "q50": "50%", "q95": "95%"}
# Asserted on every scalar that both tools call converged (R-hat < 1.01): an ESS of chains that
# disagree is not interpretable, and both tools agree which those are. R-hat itself is asserted on
# every non-degenerate scalar. stansummary's MCSE is sd/sqrt(ESS of the UNSPLIT chains), so it is
# asserted against that; az.mcse (split chains) is a different definition and is reported.
ASSERTED_CONVERGED = ("mean", "sd", "q05", "q50", "q95", "ess_bulk", "ess_tail", "mcse_sd_over_sqrt_ess_identity")
ASSERTED_ALL = ("r_hat",)
INV_MAPS = (("affine", lambda x: 3.7 * x + 112.5), ("cube", lambda x: x ** 3))
INV_METRICS = ("ess_bulk", "ess_tail95", "rhat_bulk", "rhat_folded", "r_hat")


def one_fit(pname, f, rows, inv, flags_az, flags_ss, timer):
    import arviz as az
    import pandas as pd
    cmdstanpy = quiet()
    fit = cmdstanpy.from_csv(f["csv"])
    t0 = time.time()
    idata = az.from_cmdstanpy(fit)
    groups = [g for g in ("posterior", "log_likelihood", "posterior_predictive") if g in idata.children]
    xs = {}
    for g in groups:
        xs.update(flat(idata[g].to_dataset()))
    xs["lp__"] = np.asarray(idata["sample_stats"]["lp"].values, dtype=float)
    diag = {k: az_diag(x) for k, x in xs.items()}
    summ_default = az.summary(idata, kind="diagnostics", round_to="none")
    timer[0] += time.time() - t0
    # az.summary (posterior group) must carry the same values as az.rhat / az.ess(prob=None):
    # compared as sorted multisets, so no assumption about its row labels.
    pk = list(flat(idata["posterior"].to_dataset()))
    for col, key in (("r_hat", "r_hat"), ("ess_tail", "ess_tail_default"), ("ess_bulk", "ess_bulk")):
        a = np.sort(np.asarray(summ_default[col], dtype=float))
        b = np.sort(np.array([diag[k][key] for k in pk]))
        check(a.shape == b.shape and bool(np.all((a == b) | (np.isnan(a) & np.isnan(b)))),
              "%s: az.summary %s is not az.%s" % (pname, col, key))
    ss = pd.read_csv(os.path.join(WORK, "post", pname, "stansummary.tsv"), sep="\t", index_col=0)
    for k, x in xs.items():
        if k not in ss.index:
            rows.append({"posterior": pname, "param": k, "missing_in_stansummary": True})
            continue
        d, r = diag[k], ss.loc[k]
        az_v = {"mean": float(x.mean()), "sd": float(x.std(ddof=1)), "mcse": d["mcse_mean"],
                "ess_bulk": d["ess_bulk"], "ess_tail": d["ess_tail95"], "r_hat": d["r_hat"],
                "q05": float(np.quantile(x, 0.05)), "q50": float(np.quantile(x, 0.5)),
                "q95": float(np.quantile(x, 0.95))}
        row = {"posterior": pname, "param": k,
               # a chain that never moves makes ESS and R-hat 0/0; implementations choose differently
               "degenerate": bool(np.any(np.ptp(x, axis=1) == 0)),
               # integer-valued draws (a posterior predictive y_rep, an HMM state): ranks and
               # quantile indicators are dominated by ties, where the two tools' tie rules differ
               "discrete": bool(np.all(x == np.round(x)))}
        for st, col in SS_COL.items():
            row["az_" + st] = az_v[st]
            row["ss_" + st] = float(r[col])
            row["rel_" + st] = rel(az_v[st], r[col])
        row["az_ess_tail_default"] = d["ess_tail_default"]
        row["rel_ess_tail_default"] = rel(d["ess_tail_default"], r["ESS_tail"])
        row["rel_mcse_sd_over_sqrt_ess_identity"] = rel(d["sd_over_sqrt_ess_identity"], r["MCSE"])
        row["rel_mcse_sd_over_sqrt_ess_bulk"] = rel(d["sd_over_sqrt_ess_bulk"], r["MCSE"])
        rows.append(row)
        if k != "lp__" and not row["discrete"] and d["r_hat"] > RHAT_FLAG:
            flags_az.add(pname)
        if k != "lp__" and not row["discrete"] and float(r["R_hat"]) > RHAT_FLAG:
            flags_ss.add(pname)
        # Invariance under strictly monotone maps, where floating point keeps every rank.
        if np.isfinite(x).all() and not row["degenerate"] and not row["discrete"]:
            for mname, fn in INV_MAPS:
                fx = fn(x)
                if not ranks_kept(x, fx):
                    inv["ties_skipped"] += 1
                    continue
                e = az_diag(fx)
                inv["checked_" + mname] = inv.get("checked_" + mname, 0) + 1
                for m in INV_METRICS:
                    dd = abs(e[m] - d[m])
                    key = "%s_%s" % (mname, m)
                    inv.setdefault(key, [0, 0.0])
                    if dd != 0 or np.isnan(dd):
                        inv[key][0] += 1
                        inv[key][1] = max(inv[key][1], float(dd))


def part_f(fits, rec):
    """ArviZ vs stansummary on fresh CmdStan fits, plus the monotone-map invariance."""
    rows, inv = [], {"ties_skipped": 0}
    flags_az, flags_ss = set(), set()
    timer = [0.0]
    if fits:
        p0 = sorted(fits)[0]
        rec("stansummary_columns", ",".join(open(os.path.join(WORK, "post", p0, "stansummary.tsv")).readline().split()))
    for pname in sorted(fits):
        try:
            one_fit(pname, fits[pname], rows, inv, flags_az, flags_ss, timer)
        except Exception:  # noqa: BLE001 -- recorded as a failed check; the other fits still run
            traceback.print_exc()
            check(False, "part F crashed on " + pname)
    rec("partF_arviz_s", "%.2f" % timer[0])
    ok = [r for r in rows if "degenerate" in r]
    missing = [r for r in rows if "degenerate" not in r]
    if ok:
        keys = list(ok[0].keys())
        with open(os.path.join(OUT, "fit_vs_stansummary.tsv"), "w") as fh:
            fh.write("\t".join(keys) + "\n")
            for r in ok:
                fh.write("\t".join(repr(r[k]) if isinstance(r[k], float) else s(r[k]) for k in keys) + "\n")
    good = [r for r in ok if not r["degenerate"] and not r["discrete"]]
    degen = [r for r in ok if r["degenerate"]]
    disc = [r for r in ok if r["discrete"] and not r["degenerate"]]
    rec("partF_posteriors", len(fits))
    rec("partF_scalars", len(ok))
    rec("partF_scalars_continuous", len(good))
    rec("partF_scalars_discrete", "%d in %d posteriors" % (len(disc), len({r["posterior"] for r in disc})))
    rec("partF_scalars_degenerate", "%d in %d posteriors" % (len(degen), len({r["posterior"] for r in degen})))
    rec("partF_missing_in_stansummary", len(missing))
    check(len(fits) == 57 or os.environ.get("ARVIZ_REAL_ONLY"), "expected 57 fits, got %d" % len(fits))
    check(not missing, "%d ArviZ variables have no stansummary row" % len(missing))
    conv = [r for r in good if r["az_r_hat"] < RHAT_FLAG and r["ss_r_hat"] < RHAT_FLAG]
    unconv = [r for r in good if not (r["az_r_hat"] < RHAT_FLAG and r["ss_r_hat"] < RHAT_FLAG)]
    rec("partF_scalars_converged", len(conv))
    rec("partF_scalars_unconverged", "%d in %d posteriors" % (len(unconv), len({r["posterior"] for r in unconv})))
    stats = list(SS_COL) + ["ess_tail_default", "mcse_sd_over_sqrt_ess_identity", "mcse_sd_over_sqrt_ess_bulk"]
    for label, sub in (("converged", conv), ("unconverged", unconv), ("discrete", disc)):
        for st in stats:
            if not sub:
                break
            arg = max(sub, key=lambda r: r["rel_" + st])
            n_out = sum(1 for r in sub if r["rel_" + st] > REL_TOL)
            rec("partF_%s_max_rel_%s" % (label, st), "%.3e (%s %s) beyond_tol %d of %d"
                % (arg["rel_" + st], arg["posterior"], arg["param"], n_out, len(sub)))
            if (label == "converged" and st in ASSERTED_CONVERGED) or (label != "discrete" and st in ASSERTED_ALL):
                check(arg["rel_" + st] <= REL_TOL,
                      "ArviZ %s differs from stansummary by %.3e on a %s scalar" % (st, arg["rel_" + st], label))
    for st in SS_COL:
        n = sum(1 for r in degen if r["rel_" + st] > REL_TOL)
        both_nan = sum(1 for r in degen if np.isnan(r["az_" + st]) and np.isnan(r["ss_" + st]))
        one_nan = sum(1 for r in degen if np.isnan(r["az_" + st]) != np.isnan(r["ss_" + st]))
        rec("partF_degenerate_%s" % st, "differ %d both_nan %d one_side_nan %d" % (n, both_nan, one_nan))
    # A chain that never moved: every scalar, lp__ included, is constant in that chain.
    stuck = sorted({r["posterior"] for r in ok if r["param"] == "lp__" and r["degenerate"]})
    for p in stuck:
        lp = [r for r in ok if r["posterior"] == p and r["param"] == "lp__"][0]
        rec("partF_stuck_chain", "%s ArviZ R-hat(lp__) %s stansummary R-hat(lp__) %s"
            % (p, s(lp["az_r_hat"]), s(lp["ss_r_hat"])))
    ss_nan = sorted({r["posterior"] for r in ok if not r["discrete"] and np.isnan(r["ss_r_hat"])})
    rec("partF_flagged_rhat_gt_1.01_arviz", ",".join(sorted(flags_az)) or "none")
    rec("partF_flagged_rhat_gt_1.01_stansummary", ",".join(sorted(flags_ss)) or "none")
    rec("partF_stansummary_rhat_nan_on_a_continuous_scalar", ",".join(ss_nan) or "none")
    # No silent miss: ArviZ flags every posterior stansummary flags, and flags more only where
    # stansummary's R-hat is NaN -- which is not > 1.01, so a threshold check would pass it.
    check(flags_ss <= flags_az, "stansummary flags a posterior ArviZ does not: %s" % sorted(flags_ss - flags_az))
    check((flags_az - flags_ss) <= set(ss_nan),
          "ArviZ flags a posterior stansummary gives a finite R-hat <= 1.01: %s" % sorted(flags_az - flags_ss - set(ss_nan)))
    rec("invariance_ties_skipped", inv["ties_skipped"])
    for mname, _ in INV_MAPS:
        rec("invariance_checked_%s" % mname, inv.get("checked_" + mname, 0))
        check(inv.get("checked_" + mname, 0) > 0, "no %s invariance case could be checked" % mname)
        for m in INV_METRICS:
            n, mx = inv.get("%s_%s" % (mname, m), [0, 0.0])
            rec("invariance_%s_%s" % (mname, m), "moved %d max_abs %.3e" % (n, mx))
            # The rank-based quantities must not move at all. The folded half of R-hat folds about
            # the median, |x - med|, whose ORDER a non-affine map changes, so it is reported only.
            if m in ("ess_bulk", "ess_tail95", "rhat_bulk"):
                check(n == 0, "%s moved under the %s map in %d scalars (max %.3e)" % (m, mname, n, mx))


def main():
    os.makedirs(OUT, exist_ok=True)
    cmdstanpy = quiet()
    import arviz as az
    log = open(os.path.join(OUT, "arviz_real.log"), "w")

    def rec(k, v):
        line = "%s\t%s" % (s(k), s(v))
        print(line, flush=True)
        log.write(line + "\n")
        log.flush()

    for mod in ("arviz", "arviz_base", "arviz_stats", "numpy", "scipy", "xarray", "cmdstanpy"):
        try:
            rec(mod + "_version", getattr(__import__(mod), "__version__", "unknown"))
        except Exception as e:  # noqa: BLE001 -- describing the run must not fail it
            rec(mod + "_version", "unavailable " + s(e)[:60])
    rec("cmdstan_version", ".".join(s(x) for x in cmdstanpy.cmdstan_version()))
    try:
        import numpy.linalg  # noqa: F401
        import ctypes
        from threadpoolctl import threadpool_info
        rec("blas", ";".join("%s %s %s" % (i.get("internal_api"), i.get("version"), i.get("architecture"))
                             for i in threadpool_info()))
    except Exception as e:  # noqa: BLE001
        rec("blas", "unavailable " + s(e)[:60])

    part_r(rec)

    posts = {}
    for f in sorted(os.listdir(os.path.join(PDB, "posteriors"))):
        p = json.load(open(os.path.join(PDB, "posteriors", f)))
        posts[p["name"]] = (p["model_name"], p["data_name"])
    only = [p for p in os.environ.get("ARVIZ_REAL_ONLY", "").split(",") if p]
    if only:
        posts = {p: posts[p] for p in only}
        rec("probe_only", ",".join(only))
    models = sorted({m for m, _ in posts.values()})
    ctx = multiprocessing.get_context("fork")
    t0 = time.time()
    exe = {}
    m, e, _ = compile_model(models[0])
    exe[m] = e
    with cf.ProcessPoolExecutor(max_workers=WORKERS, mp_context=ctx) as ex:
        futs = {ex.submit(compile_model, m): m for m in models[1:]}
        for fu in cf.as_completed(futs):
            try:
                m, e, _ = fu.result()
                exe[m] = e
            except Exception:  # noqa: BLE001
                traceback.print_exc()
                check(False, "compile " + futs[fu])
    rec("compile_phase_s", "%.1f" % (time.time() - t0))
    t1 = time.time()
    order = sorted(posts, key=lambda p: (p not in SLOW_FIRST, SLOW_FIRST.index(p) if p in SLOW_FIRST else 0, p))
    fits = {}
    with cf.ProcessPoolExecutor(max_workers=WORKERS, mp_context=ctx) as ex:
        futs = {ex.submit(sample_job, p, posts[p][1], exe[posts[p][0]]): p for p in order if posts[p][0] in exe}
        for fu in cf.as_completed(futs):
            try:
                r = fu.result()
                fits[r["posterior"]] = r
                print("  sampled %-50s %7.1fs  stansummary %.2fs" % (r["posterior"], r["sample_s"], r["stansummary_s"]),
                      flush=True)
            except Exception:  # noqa: BLE001
                traceback.print_exc()
                check(False, "sample " + futs[fu])
    rec("sample_phase_s", "%.1f" % (time.time() - t1))
    rec("stansummary_s_total", "%.2f" % sum(r["stansummary_s"] for r in fits.values()))
    with open(os.path.join(OUT, "draws.tsv"), "w") as fh:
        fh.write("posterior\tsample_s\tdraws_sha256\n")
        for p in sorted(fits):
            fh.write("%s\t%.1f\t%s\n" % (p, fits[p]["sample_s"], fits[p]["draws_sha256"]))

    part_f(fits, rec)

    rec("ru_maxrss_self_gib", "%.3f" % (resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1048576))
    rec("ru_maxrss_children_gib", "%.3f" % (resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss / 1048576))
    rec("failed_checks", len(FAILS))
    if FAILS:
        for f in FAILS[:50]:
            rec("FAIL", f)
        sys.exit(1)
    rec("sentinel", "ARVIZ REAL OK")


if __name__ == "__main__":
    main()
