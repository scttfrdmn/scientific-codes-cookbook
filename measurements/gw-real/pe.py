#!/usr/bin/env python3
"""Bilby full parameter estimation of GW150914 on GWOSC strain, as GWTC-2.1 analysed it (gwreal.py:
the release's config, PSDs, priors including 40 calibration parameters), checked against the
published GWTC-2.1 posterior at fixed points and in distribution.
    pe.py <data dir> <out dir> <npool> <nlive>"""
import json
import os
import sys
import time

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import gwreal  # noqa: E402

import bilby  # noqa: E402

D, OUT, NP, NLIVE = sys.argv[1], sys.argv[2], int(sys.argv[3]), int(sys.argv[4])
SEED = 20261019
REL = os.path.join(D, "IGWN-GWTC2p1-v2-GW150914_095045_PEDataRelease_mixed_nocosmo.h5")
FILES = {"H1": os.path.join(D, "H-H1_LOSC_16_V1-1126256640-4096.hdf5"),
         "L1": os.path.join(D, "L-L1_LOSC_16_V1-1126256640-4096.hdf5")}
PUB_LN_BF, PUB_LN_BF_ERR, PUB_LN_NOISE = 303.45, 0.14, -7288.12   # release meta_data/sampler
PUB_NLIVE_TOTAL = 4 * 2000                                           # n-parallel 4, nlive 2000
FAIL, SCORE = [], []
PHASE = {}


def rec(k, v):
    v = v if isinstance(v, str) else str(v)
    SCORE.append((k, v))
    print("%s\t%s" % (k, v), flush=True)


def check(ok, msg):
    if not bool(ok):
        FAIL.append(msg)
    print(("ok    " if bool(ok) else "FAIL  ") + msg, flush=True)


os.makedirs(OUT, exist_ok=True)
os.makedirs("/tmp/work", exist_ok=True)
for m in ("bilby", "dynesty", "lal", "lalsimulation", "pycbc", "numpy", "gwpy"):
    try:
        rec(m + "_version", getattr(__import__(m), "__version__", "unknown"))
    except Exception as e:
        rec(m + "_version", "unimportable: %s" % e)

t = time.time()
pri, psds, post = gwreal.release(REL)
ifos = gwreal.interferometers(psds, FILES)
prior = gwreal.priors(pri)
like = gwreal.likelihood(ifos, prior)
PHASE["setup_s"] = time.time() - t
names = post.dtype.names

# ---- fixed points: the published likelihood, noise evidence and SNRs on the same bytes --------
# bilby 1.0.4 (the release's) scaled the PSD by the Tukey window's power loss; bilby 2.x calls that
# incorrect and drops it, keeping the old behaviour behind BILBY_INCORRECT_PSD_NORMALIZATION. The
# release is reproduced with it set (run.sh); here the corrected normalisation is measured too.
_ = ifos[0].frequency_domain_strain          # the window factor is set when the data are FFT'd
wf = ifos[0].strain_data.window_factor
rec("tukey_window_factor", "%.6f" % wf)
os.environ["BILBY_INCORRECT_PSD_NORMALIZATION"] = "FALSE"
noise2 = like._calculate_noise_log_likelihood()
rec("ln_noise_evidence_bilby2_normalisation", "%.4f" % noise2)
os.environ["BILBY_INCORRECT_PSD_NORMALIZATION"] = "TRUE"
noise = like._calculate_noise_log_likelihood()
rec("noise_ratio_bilby2_over_release", "%.6f (window factor %.6f)" % (noise2 / noise, wf))
rec("ln_noise_evidence_ours", "%.4f" % noise)
check(abs(noise - PUB_LN_NOISE) < 0.006,
      "noise log-likelihood %.4f == published ln_noise_evidence %.2f (to its 2 printed decimals)" % (noise, PUB_LN_NOISE))

imax = int(np.argmax(post["log_likelihood"]))
rng = np.random.default_rng(SEED)
idx = np.concatenate([[imax], rng.choice(len(post), 499, replace=False)])
# The sampled point, as the sampler sees it: every free prior from the sample, and the marginalised
# time and distance at the fixed values the likelihood put into the prior (geocent_time is the
# segment start, the reference for the time-marginalisation FFT -- not the physical arrival time).
fixed = {k: float(v.peak) if isinstance(v, bilby.core.prior.DeltaFunction) else float(v)
         for k, v in prior.items() if isinstance(v, (bilby.core.prior.DeltaFunction, int, float))}
free = [k for k, v in prior.items() if isinstance(v, bilby.core.prior.Prior)
        and not isinstance(v, (bilby.core.prior.DeltaFunction, bilby.core.prior.Constraint))]
check(fixed.get("geocent_time") == float(ifos.start_time), "time-marginalised reference time is the segment start")
diffs = []
t = time.time()
for i in idx:
    row = post[i]
    par = dict(fixed)
    par.update({k: float(row[k]) for k in free})
    diffs.append(like.log_likelihood_ratio(parameters=par) - float(row["log_likelihood"]))
ms_per_call = (time.time() - t) / len(idx) * 1e3
diffs = np.array(diffs)
# the same max-L point with lalsimulation 6.1.0's own XPHM defaults instead of the release's
par = dict(fixed)
par.update({k: float(post[imax][k]) for k in free})
gwreal._FLAGS["v"] = None
rec("lnL_maxL_with_lalsim_6.1.0_defaults_minus_published", "%.4f" % (like.log_likelihood_ratio(parameters=par) - float(post[imax]["log_likelihood"])))
gwreal._FLAGS["v"] = gwreal.XPHM_2021
rec("lnL_ours_minus_published_500_samples", "median %.3e max|.| %.3e" % (np.median(diffs), np.abs(diffs).max()))
rec("lnL_ms_per_call_serial", "%.2f" % ms_per_call)
rec("free_parameters", len(free))
LNL_TOL = float(os.environ.get("LNL_TOL", "0.05"))
check(np.all(np.isfinite(diffs)) and np.abs(diffs).max() < LNL_TOL,
      "marginalised lnL at 500 published samples (incl. the max-L one) == published, max |diff| %.2e < %g"
      % (np.abs(diffs).max(), LNL_TOL))

# bilby vs PyCBC matched-filter SNR at the published max-L sample, on bilby's own data and PSD
import pycbc.filter  # noqa: E402
from pycbc.types import FrequencySeries  # noqa: E402
row = post[imax]
full = {k: float(row[k]) for k in names if np.issubdtype(post.dtype[k], np.floating)}
wfg = like.waveform_generator
pol = wfg.frequency_domain_strain(full)
for ifo in ifos:
    h = ifo.get_detector_response(pol, full)
    m = ifo.frequency_mask
    z_b = complex(ifo.matched_filter_snr(h))
    df = 1.0 / gwreal.DURATION
    tmpl = np.where(m, h, 0)
    psd = np.where(m, ifo.power_spectral_density_array, np.inf)
    z_p = pycbc.filter.matched_filter(FrequencySeries(tmpl, delta_f=df), FrequencySeries(ifo.frequency_domain_strain, delta_f=df),
                                      psd=FrequencySeries(psd, delta_f=df))[0]
    # both are 4 df sum(conj(h) d / S) / sqrt(<h|h>): PyCBC at lag 0, bilby directly
    z_p = complex(z_p)
    # the release stores the real part as *_matched_filter_snr and the complex value as abs + angle
    z_pub = float(row["%s_matched_filter_abs_snr" % ifo.name]) * np.exp(1j * float(row["%s_matched_filter_snr_angle" % ifo.name]))
    rec("%s_mf_snr_published_real_column" % ifo.name, "%.6f" % float(np.real(row["%s_matched_filter_snr" % ifo.name])))
    rec("%s_mf_snr_bilby" % ifo.name, "%.6f%+.6fj (|.| %.6f)" % (z_b.real, z_b.imag, abs(z_b)))
    rec("%s_mf_snr_pycbc" % ifo.name, "%.6f%+.6fj" % (z_p.real, z_p.imag))
    rec("%s_mf_snr_published" % ifo.name, "%.6f%+.6fj (|.| %.6f)" % (z_pub.real, z_pub.imag, abs(z_pub)))
    check(abs(z_p - z_b) / abs(z_b) < 1e-10, "%s: PyCBC matched-filter SNR == bilby's at the published max-L sample, rel %.1e"
          % (ifo.name, abs(z_p - z_b) / abs(z_b)))
    check(abs(z_b - z_pub) / abs(z_pub) < 1e-3, "%s: bilby's SNR == the published one at that sample, rel %.1e < 1e-3"
          % (ifo.name, abs(z_b - z_pub) / abs(z_pub)))
    os.environ["BILBY_INCORRECT_PSD_NORMALIZATION"] = "FALSE"
    z_c = complex(ifo.matched_filter_snr(h))
    os.environ["BILBY_INCORRECT_PSD_NORMALIZATION"] = "TRUE"
    rec("%s_mf_snr_bilby2_normalisation" % ifo.name, "|.| %.6f (ratio %.6f, sqrt(window factor) %.6f)"
        % (abs(z_c), abs(z_c) / abs(z_b), np.sqrt(wf)))

if NLIVE == 0:
    with open(os.path.join(OUT, "score.tsv"), "w") as fh:
        fh.writelines("%s\t%s\n" % kv for kv in SCORE)
    sys.exit("PE FAILED: %d checks" % len(FAIL) if FAIL else 0)
# ---- the run ---------------------------------------------------------------------------------
# Sampled as current bilby_pipe does it: calibration marginalised numerically over 1000 response
# curves drawn from the release's own spline priors (Payne et al. 2020), distance marginalised,
# time sampled. Same posterior as sampling the 40 calibration parameters, in 14 dimensions not 53.
bilby.core.utils.random.seed(SEED)
np.random.seed(SEED)
ifos_s = gwreal.interferometers(psds, FILES)
prior_s = gwreal.priors(pri)
prior_s.pop("time_jitter", None)
CURVES = int(os.environ.get("CAL_CURVES", "250"))
rec("calibration_response_curves", CURVES)
rec("nlive", NLIVE)
rec("npool", NP)
like_s = gwreal.likelihood_calmarg(ifos_s, prior_s, curves=CURVES)
rec("sampled_parameters", sum(1 for v in prior_s.values() if not isinstance(
    v, (bilby.core.prior.DeltaFunction, bilby.core.prior.Constraint))))
t = time.time()
result = bilby.run_sampler(
    likelihood=like_s, priors=prior_s, sampler="dynesty", nlive=NLIVE, sample="acceptance-walk", naccept=60,
    npool=NP, rstate=np.random.default_rng(SEED), check_point=False, outdir="/tmp/work", label="GW150914",
    conversion_function=bilby.gw.conversion.generate_all_bbh_parameters,
    result_class=bilby.gw.result.CBCResult, save="hdf5", print_method="interval-60")
PHASE["sampler_and_conversion_s"] = time.time() - t
PHASE["sampling_s"] = float(result.sampling_time.total_seconds()) if hasattr(result.sampling_time, "total_seconds") else float(result.sampling_time)
rec("ln_bayes_factor", "%.3f +- %.3f" % (result.log_bayes_factor, result.log_evidence_err))
rec("posterior_samples", len(result.posterior))
rec("n_likelihood_evaluations", getattr(result, "num_likelihood_evaluations", "unknown"))
zbf = (result.log_bayes_factor - PUB_LN_BF) / np.hypot(result.log_evidence_err, PUB_LN_BF_ERR)
check(abs(zbf) < 5, "ln BF %.2f vs published %.2f: %.2f combined sigma (< 5)" % (result.log_bayes_factor, PUB_LN_BF, zbf))

# ---- the posterior against the published one -------------------------------------------------
import dill  # noqa: E402
import dynesty.utils as dyfunc  # noqa: E402
with open("/tmp/work/GW150914_dynesty.pickle", "rb") as fh:
    dres = dill.load(fh)
cols = list(result.search_parameter_keys)


def derived(s):
    """Columns of the comparison from sampled-space arrays (chirp mass, q, spins at f_ref)."""
    c = {k: s[:, cols.index(k)] for k in ("chirp_mass", "mass_ratio", "a_1", "a_2", "tilt_1", "tilt_2",
                                          "theta_jn", "azimuth", "zenith", "psi", "phase", "phi_12", "phi_jl", "geocent_time")}
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
Q = (0.05, 0.5, 0.95)
ref_q = {p: np.quantile(post[p], Q) for p in SAMPLED + RECON}
w0 = np.exp(dres.logwt - dres.logz[-1])
w0 /= w0.sum()
base = derived(dres.samples)
F = {p: np.array([np.sum(w0 * (base[p] < x)) for x in ref_q[p]]) for p in SAMPLED}
boot = {p: [] for p in SAMPLED}
brng = np.random.default_rng(SEED + 1)
B = 200
for _ in range(B):
    r = dyfunc.resample_run(dres, rstate=brng)
    w = np.exp(r.logwt - r.logz[-1])
    w /= w.sum()
    dv = derived(r.samples)
    for p in SAMPLED:
        boot[p].append([np.sum(w * (dv[p] < x)) for x in ref_q[p]])
kish = 1.0 / np.sum(w0 ** 2)
rec("kish_ess", "%.0f" % kish)
# the sampler's own error: SD of F over bootstrap realisations of the run (Higson et al. 2018);
# the published run's, scaled by its live points: 4 runs x 2000 against our NLIVE
ref_scale = np.sqrt(NLIVE / PUB_NLIVE_TOTAL)
table, infl = [], []
for p in SAMPLED:
    se = np.std(np.array(boot[p]), axis=0)
    infl.extend(se / np.sqrt(np.array(Q) * (1 - np.array(Q)) / kish))
    z = (F[p] - np.array(Q)) / (se * np.sqrt(1 + ref_scale ** 2))
    table.append((p, F[p], se, z))
inflation = float(np.median(infl))
rec("bootstrap_se_over_kish_binomial_median", "%.3f" % inflation)
ps = result.posterior
n_post = len(ps)
for p in RECON:
    x = ps[p].to_numpy()
    Fp = np.array([np.mean(x < v) for v in ref_q[p]])
    se = inflation * np.sqrt(np.array(Q) * (1 - np.array(Q)) / n_post)
    table.append((p, Fp, se, (Fp - np.array(Q)) / (se * np.sqrt(1 + ref_scale ** 2))))
zmax = 0.0
with open(os.path.join(OUT, "quantiles.tsv"), "w") as fh:
    fh.write("parameter\tref_q05\tref_q50\tref_q95\tF_at_q05\tF_at_q50\tF_at_q95\tz_q05\tz_q50\tz_q95\n")
    for p, Fp, se, z in table:
        fh.write("%s\t%s\t%s\t%s\n" % (p, "\t".join("%.6g" % v for v in ref_q[p]),
                                     "\t".join("%.4f" % v for v in Fp), "\t".join("%.2f" % v for v in z)))
        zmax = max(zmax, float(np.max(np.abs(z))))
        print("  %-20s F(ref q05,q50,q95) = %s   z = %s" % (p, np.array2string(Fp, precision=4),
                                                           np.array2string(z, precision=2)), flush=True)
check(np.isfinite(zmax) and zmax < 5, "every published quantile (%d tests) within 5 SE of ours: max |z| %.2f"
      % (3 * len(table), zmax))
for p in ("chirp_mass", "mass_ratio", "chi_eff", "luminosity_distance", "theta_jn"):
    rec("median_%s" % p, "ours %.4f published %.4f" % (np.median(ps[p]), np.median(post[p])))

import resource  # noqa: E402
rec("maxrss_gib_main", "%.2f" % (resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 2 ** 20))
rec("maxrss_gib_largest_worker", "%.2f" % (resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss / 2 ** 20))
with open(os.path.join(OUT, "score.tsv"), "w") as fh:
    for k, v in SCORE:
        fh.write("%s\t%s\n" % (k, v))
    for k, v in PHASE.items():
        fh.write("phase_%s\t%.1f\n" % (k, v))
with open(os.path.join(OUT, "phases.json"), "w") as fh:
    json.dump(PHASE, fh)
os.system("cp /tmp/work/GW150914_result.hdf5 %s/ 2>/dev/null" % OUT)
if FAIL:
    sys.exit("PE FAILED: %d checks" % len(FAIL))
print("PE OK")
