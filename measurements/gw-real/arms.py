#!/usr/bin/env python3
"""The two arms that separate the r1 quantile miss's candidate causes, each changing one thing.

    arms.py sampler <naccept> <data dir> <out dir> <npool> <nlive> [maxmcmc]
        pe.py, byte for byte, with only dynesty's naccept (60 in r1), the acceptance-walk cap
        maxmcmc (bilby's default 5000 in r1) or the live-point count nlive (500 in r1, pe.py's own
        argument) changed. Every walk bilby's tuner sets is written to
        walks.tsv, and a running cap-hit summary is printed, so it survives a kill in command.log.
    arms.py calibration <r1 result.hdf5> <r1 quantiles.tsv> <data dir> <out dir> <npool> <curves...>
        r1's own nested samples and posterior, importance-reweighted from its 250-curve calibration
        marginalisation to N fresh curves, then pe.py's quantile test on the reweighted run."""
import os
import runpy
import sys
import time

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
SEED = 20261019                       # pe.py's
MODE = sys.argv[1]

if MODE == "sampler":
    import bilby
    from bilby.core.sampler import dynesty3_utils
    NACCEPT = int(sys.argv[2])
    MAXMCMC = int(sys.argv[7]) if len(sys.argv) > 7 else None
    _run = bilby.run_sampler

    def run_sampler(*a, **kw):
        print("arms: naccept %s -> %d" % (kw.get("naccept"), NACCEPT), flush=True)
        print("arms: nlive %s" % kw.get("nlive"), flush=True)
        kw["naccept"] = NACCEPT
        if MAXMCMC is not None:
            print("arms: maxmcmc %s -> %d" % (kw.get("maxmcmc", "default 5000"), MAXMCMC), flush=True)
            kw["maxmcmc"] = MAXMCMC
        return _run(*a, **kw)

    # walks_probe.py's recorder: per tune call, the walk set and the returned chain's steps
    W = {"n": 0, "hit": 0, "win": [], "cap": set(), "maxset": 0}
    os.makedirs(sys.argv[4], exist_ok=True)
    WLOG = open(os.path.join(sys.argv[4], "walks.tsv"), "w")
    WLOG.write("tune_call\tnaccept\tmaxmcmc\twalks_set\taccept_returned\treject_returned\n")
    _tune = dynesty3_utils.EnsembleWalkSampler.tune

    def tune(self, info, update=True):
        _tune(self, info, update)
        steps = int(info["accept"]) + int(info["reject"])
        hit = int(steps >= self.maxmcmc)
        W["n"] += 1
        W["hit"] += hit
        W["win"].append(hit)
        W["cap"].add(int(self.maxmcmc))
        W["maxset"] = max(W["maxset"], int(self.sampler_kwargs["walks"]))
        WLOG.write("%d\t%d\t%d\t%d\t%d\t%d\n" % (W["n"], self.naccept, self.maxmcmc,
                                                  self.sampler_kwargs["walks"], info["accept"], info["reject"]))
        WLOG.flush()
        if W["n"] % 250 == 0:
            print("walks\ttune %d\tset %d\tcap %d\tcap-hit last 250 %.3f\tall %.3f"
                  % (W["n"], self.sampler_kwargs["walks"], self.maxmcmc, sum(W["win"]) / 250.0,
                     W["hit"] / float(W["n"])), flush=True)
            W["win"] = []

    dynesty3_utils.EnsembleWalkSampler.tune = tune
    bilby.run_sampler = run_sampler
    sys.argv = [os.path.join(HERE, "pe.py")] + sys.argv[3:7]
    try:
        runpy.run_path(sys.argv[0], run_name="__main__")
    finally:
        WLOG.close()
        with open(os.path.join(sys.argv[2], "score.tsv"), "a") as fh:
            fh.write("naccept\t%d\n" % NACCEPT)
            if MAXMCMC is not None:
                fh.write("maxmcmc\t%d\n" % MAXMCMC)
                fh.write("walk_cap_seen_by_tuner\t%s\n" % ",".join(str(c) for c in sorted(W["cap"])))
                fh.write("walks_tune_calls\t%d\n" % W["n"])
                fh.write("walks_hit_cap_fraction\t%.4f\n" % (W["hit"] / float(max(W["n"], 1))))
                fh.write("walks_longest_set\t%d\n" % W["maxset"])
        if MAXMCMC is not None and W["cap"] != {MAXMCMC}:
            print("FAIL  the walk tuner saw cap %s, not the requested %d" % (sorted(W["cap"]), MAXMCMC), flush=True)
            sys.exit(1)
    sys.exit(0)

# ---- calibration ---------------------------------------------------------------------------------
# r1 sampled with the release's PSD normalisation (pe.py sets this before its run; README, "Two things").
os.environ["BILBY_INCORRECT_PSD_NORMALIZATION"] = "TRUE"
import multiprocessing as mp  # noqa: E402

import bilby  # noqa: E402
import gwreal  # noqa: E402

RES, QTSV, D, OUT, NP = sys.argv[2], sys.argv[3], sys.argv[4], sys.argv[5], int(sys.argv[6])
CURVES = [int(c) for c in sys.argv[7:]]
REL = os.path.join(D, "IGWN-GWTC2p1-v2-GW150914_095045_PEDataRelease_mixed_nocosmo.h5")
FILES = {"H1": os.path.join(D, "H-H1_LOSC_16_V1-1126256640-4096.hdf5"),
         "L1": os.path.join(D, "L-L1_LOSC_16_V1-1126256640-4096.hdf5")}
Q = np.array([0.05, 0.5, 0.95])
INFL = np.sqrt(1 + 500 / 8000)        # pe.py's: the release's error, scaled to its 4 x 2000 live points
FAIL, LINES = [], []
os.makedirs(OUT, exist_ok=True)
os.makedirs("/tmp/work", exist_ok=True)


def rec(k, v):
    LINES.append("%s\t%s" % (k, v))
    print("%s\t%s" % (k, v), flush=True)


def check(ok, msg):
    if not bool(ok):
        FAIL.append(msg)
    print(("ok    " if bool(ok) else "FAIL  ") + msg, flush=True)


r1 = bilby.core.result.read_in_result(RES)
keys = list(r1.search_parameter_keys)
ns, ps = r1.nested_samples, r1.posterior
rec("r1_nested_samples", len(ns))
rec("r1_posterior_samples", len(ps))

# Build the likelihoods in pe.py's order: the release likelihood first (it writes the distance lookup
# table), then the seeds, then the 250-curve calibration likelihood, which therefore draws r1's curves.
pri, psds, post = gwreal.release(REL)
gwreal.likelihood(gwreal.interferometers(psds, FILES), gwreal.priors(pri))
bilby.core.utils.random.seed(SEED)
np.random.seed(SEED)


def calmarg(n, tag):
    ifos = gwreal.interferometers(psds, FILES)
    prior = gwreal.priors(pri)
    prior.pop("time_jitter", None)
    like = bilby.gw.likelihood.GravitationalWaveTransient(
        interferometers=ifos, waveform_generator=gwreal._wfg(ifos), priors=prior,
        phase_marginalization=False, distance_marginalization=True,
        distance_marginalization_lookup_table="/tmp/work/TD.npz", time_marginalization=False,
        calibration_marginalization=True, number_of_response_curves=n,
        calibration_lookup_table={d: "/tmp/work/%s_calibration_%s.h5" % (d, tag) for d in ("H1", "L1")},
        reference_frame="H1L1", time_reference="geocent")
    # pe.py's sampled point: the free parameters from the sample, everything the likelihood fixed
    # (distance, which it marginalises, becomes a float in the prior) at its fixed value
    fixed = {k: float(v.peak) if isinstance(v, bilby.core.prior.DeltaFunction) else float(v)
             for k, v in prior.items() if isinstance(v, (bilby.core.prior.DeltaFunction, int, float))}
    return like, fixed


LIKES = {250: calmarg(250, "r1")}
for n in CURVES:                      # independent draws, one seed per count
    bilby.core.utils.random.seed(SEED + n)
    np.random.seed(SEED + n)
    LIKES[n] = calmarg(n, "n%d" % n)
gwreal._FLAGS["v"] = gwreal.XPHM_2021
ROWS = None


def one(args):
    n, i = args
    like, fixed = LIKES[n]
    par = dict(fixed)
    par.update({k: float(ROWS[i, j]) for j, k in enumerate(keys)})
    return like.log_likelihood_ratio(parameters=par)


def evaluate(df, label):
    global ROWS
    ROWS = df[keys].to_numpy()
    out = {}
    for n in [250] + CURVES:
        t = time.time()
        with mp.get_context("fork").Pool(NP) as pool:
            out[n] = np.array(pool.map(one, [(n, i) for i in range(len(ROWS))], chunksize=16))
        rec("eval_%s_curves_%d_s" % (label, n), "%.1f" % (time.time() - t))
    return out


L_ns = evaluate(ns, "nested")
L_ps = evaluate(ps, "posterior")
np.savez_compressed(os.path.join(OUT, "lnL.npz"), r1_nested=ns["log_likelihood"].to_numpy(),
                    r1_posterior=ps["log_likelihood"].to_numpy(), weights=ns["weights"].to_numpy(),
                    **{"nested_%d" % n: v for n, v in L_ns.items()}, **{"posterior_%d" % n: v for n, v in L_ps.items()})
for lab, df, L in (("nested", ns, L_ns), ("posterior", ps, L_ps)):
    d = np.abs(L[250] - df["log_likelihood"].to_numpy())
    rec("lnL_250_recomputed_minus_r1_%s" % lab, "max|.| %.3e" % d.max())
    check(d.max() < 1e-6, "%s: r1's 250-curve lnL reproduced at all %d samples (max |diff| %.1e < 1e-6), "
          "so the same curves are being reweighted" % (lab, len(d), d.max()))


def derived(s):
    """pe.py's comparison columns from the sampled parameters."""
    c = {k: s[k].to_numpy() for k in ("chirp_mass", "mass_ratio", "a_1", "a_2", "tilt_1", "tilt_2",
                                      "theta_jn", "azimuth", "zenith", "geocent_time")}
    q, mc = c["mass_ratio"], c["chirp_mass"]
    c["mass_1"] = mc * (1 + q) ** 0.2 / q ** 0.6
    c["mass_2"] = c["mass_1"] * q
    c["chi_eff"] = (c["a_1"] * np.cos(c["tilt_1"]) + q * c["a_2"] * np.cos(c["tilt_2"])) / (1 + q)
    c["chi_p"] = np.maximum(c["a_1"] * np.sin(c["tilt_1"]),
                            (4 * q + 3) / (4 + 3 * q) * q * c["a_2"] * np.sin(c["tilt_2"]))
    c["cos_tilt_1"], c["cos_tilt_2"] = np.cos(c["tilt_1"]), np.cos(c["tilt_2"])
    return c


SAMPLED = ["chirp_mass", "mass_ratio", "mass_1", "mass_2", "chi_eff", "chi_p", "a_1", "a_2",
           "cos_tilt_1", "cos_tilt_2", "theta_jn", "azimuth", "zenith", "geocent_time"]
RECON = ["luminosity_distance", "ra", "dec"]
ref_q = {p: np.quantile(post[p], Q) for p in SAMPLED + RECON}
r1q = {r[0]: (np.array(r[4:7], float), np.array(r[7:10], float))
       for r in (l.rstrip("\n").split("\t") for l in list(open(QTSV))[1:])}
infl = float(dict(l.rstrip("\n").split("\t", 1) for l in open(os.path.join(os.path.dirname(QTSV), "score.tsv"))
                  if "\t" in l)["bootstrap_se_over_kish_binomial_median"])
cs, cp = derived(ns), {p: ps[p].to_numpy() for p in RECON}
w0 = ns["weights"].to_numpy() / ns["weights"].sum()


def kish(w):
    return 1.0 / np.sum(w ** 2)


def test(wn, wp):
    """pe.py's test with its error model: F at each published quantile; SE = (r1's measured
    bootstrap/Kish inflation) x the binomial SE at this weighting's Kish size."""
    rows = []
    for p in SAMPLED + RECON:
        x, w = (cs[p], wn) if p in SAMPLED else (cp[p], wp)
        F = np.array([np.sum(w * (x < v)) for v in ref_q[p]])
        se = infl * np.sqrt(Q * (1 - Q) / kish(w))
        rows.append((p, F, (F - Q) / (se * INFL)))
    return rows


wp0 = np.full(len(ps), 1.0 / len(ps))
base = test(w0, wp0)
dF = max(float(np.max(np.abs(F - r1q[p][0]))) for p, F, _ in base)
check(dF < 1e-4, "F at the published quantiles from r1's stored weights == r1's quantiles.tsv (max |diff| %.1e)" % dF)
results = {"r1 (250 curves), bootstrap SE": [(p, r1q[p][0], r1q[p][1]) for p in SAMPLED + RECON],
           "r1 (250 curves), Kish SE": base}
for n in CURVES:
    dn = L_ns[n] - L_ns[250]
    dp = L_ps[n] - L_ps[250]
    wn = w0 * np.exp(dn - dn.max())
    wn /= wn.sum()
    wp = np.exp(dp - dp.max())
    wp /= wp.sum()
    post_mean = np.sum(w0 * dn)
    rec("dlnL_%d_minus_250_over_r1_posterior" % n, "mean %.4f sd %.4f min %.4f max %.4f"
        % (post_mean, np.sqrt(np.sum(w0 * (dn - post_mean) ** 2)), dn[w0 > 0].min(), dn[w0 > 0].max()))
    rec("reweight_efficiency_%d" % n, "nested %.4f posterior %.4f (Kish ESS / unweighted)"
        % (kish(wn) / kish(w0), kish(wp) / len(ps)))
    results["reweighted to %d curves, Kish SE" % n] = test(wn, wp)

with open(os.path.join(OUT, "quantiles_reweighted.tsv"), "w") as fh:
    fh.write("weighting\tparameter\tF_at_q05\tF_at_q50\tF_at_q95\tz_q05\tz_q50\tz_q95\n")
    for lab, rows in results.items():
        zmax = 0.0
        for p, F, z in rows:
            fh.write("%s\t%s\t%s\t%s\n" % (lab, p, "\t".join("%.4f" % v for v in F), "\t".join("%.2f" % v for v in z)))
            zmax = max(zmax, float(np.max(np.abs(z))))
        rec("max_abs_z [%s]" % lab, "%.2f" % zmax)
import resource  # noqa: E402
rec("maxrss_gib_largest_worker", "%.2f" % (resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss / 2 ** 20))
with open(os.path.join(OUT, "score.tsv"), "w") as fh:
    fh.write("\n".join(LINES) + "\n")
if FAIL:
    sys.exit("ARMS FAILED: %d checks" % len(FAIL))
print("ARMS OK")
