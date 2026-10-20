#!/usr/bin/env python3
"""Compare the three chips' outputs (results/<chip>/): fixed points, the search, and the sampler's
path, including the run-to-run spread of the posterior quantiles against the bootstrap error.
    collect.py <results dir>     (writes <results dir>/crosschip.txt)"""
import json
import os
import sys

import numpy as np

R = sys.argv[1]
CHIPS = ["arm64", "x86_64", "amd"]
out = []


def say(s=""):
    out.append(s)
    print(s)


def score(c):
    d = {}
    for line in open(os.path.join(R, c, "score.tsv")):
        k, _, v = line.rstrip("\n").partition("\t")
        d[k] = v
    return d


def quant(c):
    rows = [l.rstrip("\n").split("\t") for l in open(os.path.join(R, c, "quantiles.tsv"))][1:]
    return {r[0]: (np.array(r[4:7], float), np.array(r[7:10], float)) for r in rows}


S = {c: score(c) for c in CHIPS}
Q = {c: quant(c) for c in CHIPS}
J = {c: json.load(open(os.path.join(R, c, "search.json"))) for c in CHIPS}

say("== fixed points (same bytes, same parameters) ==")
for k in ("ln_noise_evidence_ours", "ln_noise_evidence_bilby2_normalisation", "lnL_ours_minus_published_500_samples",
          "lnL_maxL_with_lalsim_6.1.0_defaults_minus_published", "H1_mf_snr_bilby", "L1_mf_snr_bilby",
          "H1_mf_snr_pycbc", "L1_mf_snr_pycbc"):
    vals = [S[c].get(k, "-") for c in CHIPS]
    say("%-52s %s" % (k, " | ".join(vals)))
    if len(set(vals)) == 1:
        say("%-52s identical to the printed digits on all three" % "")

say("\n== search (PyCBC over the hour) ==")
for det in ("H1", "L1"):
    for c in CHIPS:
        o = J[c]["detectors"][det]["on_source"]
        f = J[c]["detectors"][det]["loudest_off_source_newsnr"]
        say("%s %-7s on-source t=%.6f snr=%.6f rchisq=%.6f newsnr=%.6f %s+%s | loudest off-source newsnr %.4f"
            % (det, c, o["t"], o["snr"], o["rchisq"], o["newsnr"], o["m1"], o["m2"], f["newsnr"]))
    a = J["arm64"]["detectors"][det]
    for c in ("x86_64", "amd"):
        b = J[c]["detectors"][det]
        same_t = all(abs(x["t"] - y["t"]) < 1e-9 for x, y in zip(a["triggers"], b["triggers"]))
        d_snr = max(abs(x["snr"] - y["snr"]) / x["snr"] for x, y in zip(a["triggers"], b["triggers"]))
        say("%s %s vs arm64: %d triggers, same times and templates: %s; max rel SNR difference %.1e" % (
            det, c, len(a["triggers"]), same_t and all((x["m1"], x["m2"]) == (y["m1"], y["m2"]) for x, y in zip(a["triggers"], b["triggers"])), d_snr))

say("\n== the sampler (a path) ==")
for k in ("ln_bayes_factor", "posterior_samples", "n_likelihood_evaluations", "kish_ess",
          "bootstrap_se_over_kish_binomial_median"):
    say("%-40s %s" % (k, " | ".join(S[c].get(k, "-") for c in CHIPS)))
for k in ("median_chirp_mass", "median_mass_ratio", "median_chi_eff", "median_luminosity_distance", "median_theta_jn"):
    say("%-40s %s" % (k, " | ".join(S[c].get(k, "-").split(" published")[0] for c in CHIPS)))

say("\n== F(published quantile) per chip, and the run-to-run spread against the bootstrap SE ==")
say("x86_64 and amd run one build; their quantiles are %s, so the independent paths are arm64 and x86."
    % ("identical" if all(np.array_equal(Q["x86_64"][p][0], Q["amd"][p][0]) for p in Q["arm64"]) else "NOT identical"))
say("%-20s %-27s %-27s %-27s %-24s %s" % ("parameter", "arm64 F(q05,q50,q95)", "x86_64", "amd",
                                         "|dF| arm64-x86 / sqrt2", "max|z| arm64, x86"))
q = np.array([0.05, 0.5, 0.95])
INFL = np.sqrt(1 + 500 / 8000)     # pe.py adds the release's error as ours x sqrt(nlive / (4 x 2000))
ratios, zres = [], []
for p in Q["arm64"]:
    Fa, za = Q["arm64"][p]
    Fx, zx = Q["x86_64"][p]
    pair = np.abs(Fa - Fx) / np.sqrt(2)          # per-run SD estimated from the two independent paths
    with np.errstate(divide="ignore", invalid="ignore"):
        se = 0.5 * (np.abs((Fa - q) / za) + np.abs((Fx - q) / zx)) / INFL   # the bootstrap SE, ours alone
    ok = np.isfinite(se) & (se > 0)
    ratios.extend(list(pair[ok] / se[ok]))
    big = np.maximum(se, pair) * INFL
    zres.append(np.max(np.abs(np.concatenate([(Fa - q) / big, (Fx - q) / big]))))
    say("%-20s %-27s %-27s %-27s %-24s %.2f, %.2f" % (p, *[np.array2string(Q[c][p][0], precision=4) for c in CHIPS],
                                                     np.array2string(pair, precision=4), np.abs(za).max(), np.abs(zx).max()))
say("\nmedian over the %d tests of (per-run SD from the pair) / (bootstrap SE): %.2f" % (len(ratios), np.median(ratios)))
say("max |z| with each test's SE replaced by the larger of the bootstrap SE and the pair's spread: %.2f" % max(zres))
with open(os.path.join(R, "crosschip.txt"), "w") as fh:
    fh.write("\n".join(out) + "\n")
