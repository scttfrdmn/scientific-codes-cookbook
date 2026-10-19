#!/usr/bin/env python3
"""cmdstan-real: every posteriordb 1.1.0 posterior that has a reference posterior, sampled by
CmdStan 2.40 and checked against the published reference draws.

    python3 -u real.py <pdb dir> <out dir> <workers>

Phase 1 compiles the 48 models. Phase 2 samples each of the 57 posteriors twice at one seed
(4 chains x 1000 warmup x 1000 draws, CmdStan's defaults, plus the reference's own adapt_delta /
max_treedepth where it set one), longest jobs first. The first run is compared with the reference
and evaluates the log density and gradient at 20 reference draws (compared across chips by
compare.py); the second only has to reproduce the first's draws byte for byte.

THE CHECK. MCMC is a path, so no draw is asserted. For every parameter and q in {0.05, 0.5, 0.95},
the fraction of our draws below the reference's q-quantile must be within Z_MAX standard errors of q:
    z_q = (F_ours(x_q_ref) - q) / sqrt(q(1-q) (1/ESS_ours + 1/ESS_ref))
ESS is min(ESS_bulk, ESS_tail): CmdStan's own for our draws, posteriordb's published one for the
reference. This is distribution-free -- it needs no finite variance, unlike a mean +- MCSE, which a
heavy-tailed parameter breaks (one_comp_mm_elim_abs K_m: reference mean 2.54, median 0.98, max 296).
Over ~1100 tests a two-sided 5-sigma bound (5.7e-7 each) keeps the chance of any false failure
near 6e-4 while still catching a shift of ~0.1 in quantile probability.

The claim asserted is "no silent miss": every posterior CmdStan reports as converged (R-hat < 1.01
on every reference parameter, no divergent transitions, `diagnose` clean) passes every quantile
check. A posterior CmdStan flags is reported with its z-scores, not asserted on.
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
SEED = 20261018
CHAINS, WARMUP, SAMPLING = 4, 1000, 1000
Z_MAX = 5.0
RHAT_MAX = 1.01
QS = (0.05, 0.5, 0.95)
N_LP = 20
WORK = "/tmp/work"
# Scheduling only: the slowest posteriors on the Graviton4 sizing run, started first so they do not
# set the wall clock alone at the end. Order does not change any result (each job has its own seed).
SLOW_FIRST = ["diamonds-diamonds", "mcycle_gp-accel_gp", "one_comp_mm_elim_abs-one_comp_mm_elim_abs",
              "earnings-logearn_logheight_male", "bball_drive_event_1-hmm_drive_1"]


def fail(msg):
    sys.exit("FAIL: %s" % msg)


def s(v):
    """Every reported value goes through str() -- a tuple-like never unpacks into a format."""
    return str(v)


def quiet():
    import logging
    import cmdstanpy
    cmdstanpy.utils.get_logger().setLevel(logging.WARNING)
    return cmdstanpy


def names_to_arrays(d):
    """{'b[1]': x, 'm[2,1]': y, 'sigma': z} -> {'b': list, 'm': 2-d list, 'sigma': z}."""
    groups = {}
    for k, v in d.items():
        m = re.match(r"^([^\[]+)\[([0-9, ]+)\]$", k)
        if m:
            idx = tuple(int(i) for i in m.group(2).split(","))
            groups.setdefault(m.group(1), {})[idx] = v
        else:
            groups[k] = v
    out = {}
    for k, v in groups.items():
        if isinstance(v, dict):
            shape = tuple(max(i[a] for i in v) for a in range(len(next(iter(v)))))
            arr = np.zeros(shape)
            for idx, x in v.items():
                arr[tuple(i - 1 for i in idx)] = x
            out[k] = arr.tolist()
        else:
            out[k] = v
    return out


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


def sample_job(pname, data_name, exe, rep):
    """rep 0: sample, compare with the reference, evaluate log_prob. rep 1: sample and hash only."""
    cmdstanpy = quiet()
    model = cmdstanpy.CmdStanModel(exe_file=exe)
    pd_dir = os.path.join(WORK, "post", pname)
    os.makedirs(pd_dir, exist_ok=True)
    data_file = os.path.join(pd_dir, "data.json")
    if rep == 0 or not os.path.exists(data_file):
        with zipfile.ZipFile(os.path.join(PDB, "data/data/%s.json.zip" % data_name)) as z:
            tmp = data_file + ".%d" % rep
            with open(tmp, "wb") as fh:
                fh.write(z.read(z.namelist()[0]))
            os.replace(tmp, data_file)
    info = info_of(pname)
    ctl = info["inference"]["method_arguments"].get("control") or {}
    kw = {}
    if "adapt_delta" in ctl:
        kw["adapt_delta"] = float(ctl["adapt_delta"])
    if "max_treedepth" in ctl:
        kw["max_treedepth"] = int(ctl["max_treedepth"])
    od = os.path.join(pd_dir, "fit%d" % rep)
    os.makedirs(od, exist_ok=True)
    t0 = time.time()
    fit = model.sample(data=data_file, chains=CHAINS, parallel_chains=1, seed=SEED,
                       iter_warmup=WARMUP, iter_sampling=SAMPLING, show_progress=False,
                       sig_figs=18, output_dir=od, **kw)
    r = {"posterior": pname, "rep": rep, "sample_s": time.time() - t0,
         "control": json.dumps(kw, sort_keys=True)}
    draws = np.asarray(fit.draws())
    r["draws_sha256"] = hashlib.sha256(np.ascontiguousarray(draws).tobytes()).hexdigest()
    if rep == 1:
        return r

    with zipfile.ZipFile(os.path.join(PDB, "reference_posteriors/draws/draws/%s.json.zip" % pname)) as z:
        ref = json.loads(z.read(z.namelist()[0]))
    ref_names = list(ref[0].keys())
    ref_mat = {k: np.concatenate([np.asarray(c[k], dtype=float) for c in ref]) for k in ref_names}
    diag = info["diagnostics"]
    # posteriordb 1.1.0 spells this key two ways: "diagnostic_information" in 46 info files and
    # "diagnostics_information" in 11. A one-parameter posterior stores its names and ESS as
    # scalars, not one-element lists (Rate_1, Rate_3, Rate_5).
    dnames = list(np.atleast_1d((diag.get("diagnostic_information") or diag.get("diagnostics_information"))["names"]))
    ess_b = dict(zip(dnames, np.atleast_1d(diag["effective_sample_size_bulk"]).tolist()))
    ess_t = dict(zip(dnames, np.atleast_1d(diag["effective_sample_size_tail"]).tolist()))
    summ = fit.summary(sig_figs=12)
    dpd = fit.draws_pd()
    mv = fit.method_variables()
    r["divergent"] = int(np.sum(np.asarray(mv["divergent__"])))
    r["max_treedepth_hits"] = int(np.sum(np.asarray(mv["treedepth__"]) >= kw.get("max_treedepth", 10)))
    r["diagnose_clean"] = "no problems detected" in fit.diagnose()
    rows = []
    for k in ref_names:
        if k not in summ.index or k not in dpd.columns:
            rows.append({"param": k, "missing": True})
            continue
        x = dpd[k].to_numpy(dtype=float)
        rx = ref_mat[k]
        rm, rsd = float(rx.mean()), float(rx.std(ddof=1))
        e_o = float(summ.loc[k, "ESS_bulk"])
        if "ESS_tail" in summ.columns:
            e_o = min(e_o, float(summ.loc[k, "ESS_tail"]))
        e_r = min(float(ess_b.get(k, len(rx))), float(ess_t.get(k, len(rx))))
        mcse = float(summ.loc[k, "MCSE"])
        se = (mcse ** 2 + rsd ** 2 / float(ess_b.get(k, len(rx)))) ** 0.5
        row = {"param": k, "ref_mean": rm, "ref_sd": rsd, "mean": float(x.mean()),
               "sd": float(x.std(ddof=1)), "mcse": mcse, "ess_ours": e_o, "ess_ref": e_r,
               "rhat": float(summ.loc[k, "R_hat"]),
               "z_mean": (float(x.mean()) - rm) / se if se > 0 else float("nan")}
        for q in QS:
            xq = float(np.quantile(rx, q))
            p = float(np.mean(x <= xq))
            sq = (q * (1 - q) * (1.0 / e_o + 1.0 / e_r)) ** 0.5 if e_o > 0 and e_r > 0 else float("nan")
            row["z_q%02d" % round(100 * q)] = (p - q) / sq
        rows.append(row)
    r["params"] = rows
    conv = [p for p in rows if "rhat" in p]
    r["max_rhat"] = max([p["rhat"] for p in conv] or [float("nan")])
    r["converged"] = bool(r["max_rhat"] < RHAT_MAX and r["divergent"] == 0 and r["diagnose_clean"])
    lps, t0 = [], time.time()
    for i in range(N_LP):
        pf = os.path.join(pd_dir, "point.json")
        with open(pf, "w") as fh:
            json.dump(names_to_arrays({k: ref[0][k][i] for k in ref_names}), fh)
        try:
            df = model.log_prob(pf, data=data_file, jacobian=True, sig_figs=18)
            row = df.iloc[0]
            lps.append([i, repr(float(row["lp__"]))] +
                       [repr(float(row[c])) for c in df.columns if c.startswith("g_")])
        except Exception as e:  # noqa: BLE001 -- reported per draw, compared across chips
            lps.append([i, "ERROR " + s(e).splitlines()[0][:120]])
    r["lp_s"] = time.time() - t0
    r["lp"] = lps
    zq = [abs(p[c]) for p in conv for c in p if c.startswith("z_q")]
    print("  %-50s sample %6.1fs  div %3d  R-hat %.4f  max|z_q| %.2f"
          % (pname, r["sample_s"], r["divergent"], r["max_rhat"], max(zq or [float("nan")])), flush=True)
    return r


def main():
    os.makedirs(OUT, exist_ok=True)
    cmdstanpy = quiet()
    log = open(os.path.join(OUT, "real.log"), "w")

    def rec(k, v):
        line = "%s\t%s" % (s(k), s(v))
        print(line, flush=True)
        log.write(line + "\n")
        log.flush()

    rec("cmdstanpy_version", getattr(cmdstanpy, "__version__", "unknown"))
    rec("cmdstan_version", ".".join(s(x) for x in cmdstanpy.cmdstan_version()))
    posts = {}
    for f in sorted(os.listdir(os.path.join(PDB, "posteriors"))):
        p = json.load(open(os.path.join(PDB, "posteriors", f)))
        posts[p["name"]] = (p["model_name"], p["data_name"])
    models = sorted({m for m, _ in posts.values()})
    rec("posteriors", len(posts))
    rec("models", len(models))
    if len(posts) != 57:
        fail("expected the 57 posteriors with a reference posterior in posteriordb 1.1.0, found %d" % len(posts))

    ctx = multiprocessing.get_context("fork")
    errors = []
    # The first model compiles alone, so anything CmdStan builds on first use is built once.
    t0 = time.time()
    exe, compile_s = {}, {}
    m, e, c = compile_model(models[0])
    exe[m], compile_s[m] = e, c
    with cf.ProcessPoolExecutor(max_workers=WORKERS, mp_context=ctx) as ex:
        futs = {ex.submit(compile_model, m): m for m in models[1:]}
        for fu in cf.as_completed(futs):
            try:
                m, e, c = fu.result()
                exe[m], compile_s[m] = e, c
            except Exception:  # noqa: BLE001 -- recorded, then failed below
                traceback.print_exc()
                errors.append("compile " + futs[fu])
    rec("compile_phase_s", "%.1f" % (time.time() - t0))

    t1 = time.time()
    order = sorted(posts, key=lambda p: (p not in SLOW_FIRST, SLOW_FIRST.index(p) if p in SLOW_FIRST else 0, p))
    jobs = [(p, rep) for p in order for rep in (0, 1) if posts[p][0] in exe]
    first, second = {}, {}
    with cf.ProcessPoolExecutor(max_workers=WORKERS, mp_context=ctx) as ex:
        futs = {ex.submit(sample_job, p, posts[p][1], exe[posts[p][0]], rep): (p, rep) for p, rep in jobs}
        for fu in cf.as_completed(futs):
            try:
                r = fu.result()
                (first if r["rep"] == 0 else second)[r["posterior"]] = r
            except Exception:  # noqa: BLE001 -- recorded, then failed below
                traceback.print_exc()
                errors.append("sample %s rep %d" % futs[fu])
    rec("sample_phase_s", "%.1f" % (time.time() - t1))
    rec("workload_wall_s", "%.1f" % (time.time() - t0))
    rec("max_child_rss_gib", "%.2f" % (resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss / 1048576))
    results = [first[p] for p in sorted(first)]
    for r in results:
        r["model"] = posts[r["posterior"]][0]
        r["compile_s"] = compile_s[r["model"]]
        r2 = second.get(r["posterior"])
        r["rerun_s"] = r2["sample_s"] if r2 else float("nan")
        r["same_seed_identical"] = bool(r2 and r2["draws_sha256"] == r["draws_sha256"])

    qcols = ["z_q%02d" % round(100 * q) for q in QS]
    with open(os.path.join(OUT, "params.tsv"), "w") as fh:
        cols = ["ref_mean", "ref_sd", "mean", "sd", "mcse", "ess_ours", "ess_ref", "rhat", "z_mean"] + qcols
        fh.write("posterior\tparam\t" + "\t".join(cols) + "\n")
        for r in results:
            for p in r["params"]:
                fh.write("%s\t%s\t" % (r["posterior"], p["param"]) +
                         ("MISSING" if p.get("missing") else "\t".join(repr(p[c]) for c in cols)) + "\n")
    with open(os.path.join(OUT, "lp.tsv"), "w") as fh:
        for r in results:
            for row in r["lp"]:
                fh.write(r["posterior"] + "\t" + "\t".join(s(x) for x in row) + "\n")
    with open(os.path.join(OUT, "posteriors.tsv"), "w") as fh:
        cols = ["model", "control", "compile_s", "sample_s", "rerun_s", "lp_s", "divergent",
                "max_treedepth_hits", "diagnose_clean", "max_rhat", "converged",
                "same_seed_identical", "draws_sha256"]
        fh.write("posterior\t" + "\t".join(cols) + "\n")
        for r in results:
            fh.write(r["posterior"] + "\t" + "\t".join(
                ("%.1f" % r[c]) if c.endswith("_s") else s(r[c]) for c in cols) + "\n")

    # ------------------------------------------------------------------ the assertions
    bad = list(errors)
    if len(results) != 57:
        bad.append("%d of 57 posteriors completed" % len(results))
    missing = [(r["posterior"], p["param"]) for r in results for p in r["params"] if p.get("missing")]
    if missing:
        bad.append("%d reference parameters absent from the fit: %s" % (len(missing), missing[:5]))
    allz = [(abs(p[c]), r["posterior"], p["param"], c, r["converged"])
            for r in results for p in r["params"] if not p.get("missing") for c in qcols]
    nonfinite = [x for x in allz if not bool(np.isfinite(x[0]))]
    if nonfinite:
        bad.append("%d quantile z-scores are not finite: %s" % (len(nonfinite), nonfinite[:5]))
    conv = [r for r in results if r["converged"]]
    flagged = [r for r in results if not r["converged"]]
    rec("parameters_compared", "%d in %d posteriors" % (sum(1 for r in results for p in r["params"]
                                                            if not p.get("missing")), len(results)))
    rec("quantile_tests", len(allz))
    rec("converged_posteriors", "%d of %d" % (len(conv), len(results)))
    for r in flagged:
        zf = max([x for x in allz if x[1] == r["posterior"]] or [(float("nan"),)])
        rec("flagged_by_cmdstan", "%s: max R-hat %.4f, %d divergent, diagnose %s; max |z_q| %.2f (reported)"
            % (r["posterior"], r["max_rhat"], r["divergent"], "clean" if r["diagnose_clean"] else "NOT clean",
               zf[0]))
    zc = [x for x in allz if x[4] and bool(np.isfinite(x[0]))]
    worst = max(zc) if zc else (float("nan"), "-", "-", "-", True)
    rec("max_abs_z_quantile_converged", "%.3f (%s %s %s)" % worst[:4])
    over = [x for x in zc if x[0] > Z_MAX]
    if over:
        bad.append("%d quantiles of converged posteriors more than %g SE from the reference: %s"
                   % (len(over), Z_MAX, over[:5]))
    z = np.array([x[0] for x in zc])
    rec("quantile_z_rms_converged", "%.3f" % float(np.sqrt(np.mean(z ** 2))))
    rec("frac_abs_z_gt_1.96_converged", "%.4f" % float(np.mean(z > 1.96)))
    zm = [(abs(p["z_mean"]), r["posterior"], p["param"]) for r in conv for p in r["params"] if "z_mean" in p]
    rec("max_abs_z_mean_converged_reported", "%.3f (%s %s)" % max(zm))
    nd = [r["posterior"] for r in results if not r["same_seed_identical"]]
    rec("same_seed_identical", "%d of %d" % (len(results) - len(nd), len(results)))
    if nd:
        bad.append("same seed, different draws: %s" % nd)
    rec("divergent_total", "%d in %d posteriors" % (sum(r["divergent"] for r in results),
                                                   sum(1 for r in results if r["divergent"])))
    rec("treedepth_hits_total", "%d in %d posteriors" % (sum(r["max_treedepth_hits"] for r in results),
                                                        sum(1 for r in results if r["max_treedepth_hits"])))
    lperr = sorted({r["posterior"] for r in results for row in r["lp"] if s(row[1]).startswith("ERROR")})
    rec("log_prob_points", "%d evaluated; reference lacks the parameters for: %s"
        % (sum(1 for r in results for row in r["lp"] if not s(row[1]).startswith("ERROR")), " ".join(lperr)))
    rec("sum_compile_s", "%.1f" % sum(compile_s.values()))
    for k in ("sample_s", "rerun_s", "lp_s"):
        rec("sum_" + k, "%.1f" % sum(r[k] for r in results))
    if bad:
        for b in bad:
            rec("FAIL", b)
        fail("; ".join(bad))
    rec("result", "CMDSTAN REAL OK")
    log.close()


if __name__ == "__main__":
    main()
