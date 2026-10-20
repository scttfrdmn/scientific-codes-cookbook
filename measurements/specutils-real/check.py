#!/usr/bin/env python3
"""specutils-real: assert the identities over every pixel real.py fitted, and summarise.

    python3 check.py <out_dir>        reads <out_dir>/pix/*.json, writes check.txt and results.json

The fixed point is the chi2 minimum of one least-squares problem (DESI emlinefit's model, pixels,
weights, continuum and bounds). The catalogue only defines it to its optimiser's stopping rule:
scipy least_squares stops when chi2 moves by < ftol = 1e-8 relative. Near a minimum
chi2(p) - chi2_min = (dp / sigma_p)^2, so a fit stopped there can sit up to sqrt(ftol chi2)
parameter-sigmas from the true minimum (1.4e-3 sigma at chi2 = 200), and two such fits up to twice
that apart. The tolerances are 10x that rule (K), per fit.
"""
import glob
import json
import math
import os
import sys

import numpy as np

OUT = sys.argv[1]
C_KMS = 299792.458
FTOL = 1e-8                   # scipy.optimize.least_squares' default ftol, which emlinefit's curve_fit uses
K = 10.0                      # margin on that stopping rule
CONT_REL = 1e-6               # CONT is a median of float32 fluxes: a few float32 ulps (2^-23 = 1.2e-7)
SN_MIN = 3.0
SIG_MIN = 0.34                # Angstrom: DESI's own resolution floor (emlinefit's min_sigma since 2026)
LINES = ["OII", "HDELTA", "HGAMMA", "HBETA", "OIII", "HALPHA"]

lines_out, fails = [], []


def say(*a):
    s = " ".join(str(x) for x in a)
    print(s, flush=True)
    lines_out.append(s)


def check(label, ok, observed):
    ok = bool(ok)                    # numpy bools: never `is False`
    say("%s  %-66s %s" % ("PASS" if ok else "FAIL", label, observed))
    if not ok:
        fails.append(label)


def fnum(x):
    return "%.3e" % x if np.isfinite(x) else str(x)


def col(rs, src, k):
    return np.array([r[src].get(k, np.nan) if src in r else np.nan for r in rs], dtype=float)


files = sorted(glob.glob(os.path.join(OUT, "pix", "*.json")))
chks, rows = [], []
for f in files:
    d = json.load(open(f))
    chks.append(d["chk"])
    rows.extend(d["rows"])
nspec, ntgt = sum(c["n_emline"] for c in chks), sum(c["n_tgt"] for c in chks)
say("pixels", len(files), "spectra", nspec, "of which targets", ntgt, "line slots", len(rows))
res = {"pixels": len(files), "spectra": int(nspec), "targets": int(ntgt), "slots": len(rows),
       "fibermap_ebv_maxabs_diff_tgt": max(c["fibermap_ebv_maxabs_diff_tgt"] for c in chks), "per_line": {}}
say("info  fibermap EBV vs the SFD lookup emlinefit uses, targets: max |diff| %s mag" % fnum(res["fibermap_ebv_maxabs_diff_tgt"]))
check("every pixel read", len(files) > 0 and all("seconds" in c for c in chks), "%d pixels" % len(files))
check("specutils DESI loader == raw HDUs (wave, flux, ivar, mask, TARGETID)",
      all(c["loader_identical"] for c in chks), "%d of %d pixels bit-identical" % (sum(c["loader_identical"] for c in chks), len(chks)))
check("TARGETIDs unique, same set in coadd and emline",
      all(c["tid_unique"] and c["tid_same_set"] and c["n_coadd"] == c["n_emline"] for c in chks),
      "%d spectra" % nspec)
check("every line slot of every spectrum accounted for", len(rows) == 6 * nspec, "%d slots" % len(rows))

# --- 1. DESI's own function, on the arrays this run built, reproduces the catalogue -----------
# NDOF >= 0 means emlinefit's fit "succeeded" (covariance positive, flux off its 1e9 bound). For a
# sub-pixel fit (sigma < SIG_MIN) that flag is not a fixed point: F runs to +-1e8..1e9 with an
# ivar of 1e20+, and whether it lands 1% inside the bound is rounding. Those slots are exempt and
# counted; every other slot must agree exactly.
nd_cat, nd_ref = col(rows, "cat", "NDOF"), col(rows, "ref", "NDOF")
sg = np.fmin(np.nan_to_num(col(rows, "cat", "SIGMA"), nan=np.inf), np.nan_to_num(col(rows, "ref", "SIGMA"), nan=np.inf))
mis = nd_cat != nd_ref
check("ref NDOF == catalogue NDOF, every slot (sub-pixel fits exempt)", not np.any(mis & (sg >= SIG_MIN)),
      "%d slots, %d fitted by DESI; %d differ, all sigma < %g A" % (len(rows), int((nd_cat >= 0).sum()), mis.sum(), SIG_MIN))
res["ndof_subpixel_flips"] = int(mis.sum())
fit = [r for r in rows if r["cat"]["NDOF"] >= 0 and r["ref"]["NDOF"] >= 0]
res["fitted"] = len(fit)
cc, cr = col(fit, "cat", "CONT"), col(fit, "ref", "CONT")
rel = np.abs(cr.astype(np.float32).astype(float) - cc) / np.abs(cc)
exact = int((cr.astype(np.float32) == cc.astype(np.float32)).sum())
check("ref CONT == catalogue CONT, <= %g relative" % CONT_REL, np.nanmax(rel) <= CONT_REL,
      "%d of %d bit-identical, max rel %s" % (exact, len(fit), fnum(np.nanmax(rel))))
ew, fl, z = col(fit, "cat", "EW"), col(fit, "cat", "FLUX"), np.array([r["z"] for r in fit])
ok = np.isfinite(ew) & np.isfinite(fl) & (cc != 0) & (fl != 0)
ewrel = np.abs(ew[ok] - fl[ok] / ((1 + z[ok]) * cc[ok])) / np.abs(ew[ok])
check("catalogue EW == FLUX / ((1+z) CONT) (the rest-frame definition)", np.nanmax(ewrel) <= 4 * 2.0 ** -23,
      "max rel %s on %d" % (fnum(np.nanmax(ewrel)), ok.sum()))


def wellposed(r):
    c, f = r["cat"], r["ref"]
    if not (np.isfinite(c["FLUX"]) and c["FLUX_IVAR"] > 0 and "su" in r and np.isfinite(r["su"].get("VAR_ABS", np.nan))):
        return False
    if not (SIG_MIN <= f["SIGMA"] < 9.99):
        return False
    if r["line"] == "OII" and not (0.101 < f["SHARE"] < 0.99):
        return False
    return c["FLUX"] * math.sqrt(c["FLUX_IVAR"]) >= SN_MIN


wp = [r for r in fit if wellposed(r)]
res["wellposed"] = len(wp)
x2r = col(wp, "su", "CHI2_AT_REF")
sabs = np.sqrt(col(wp, "su", "VAR_ABS"))
# two fits, each stopped within K ftol chi2 of the minimum: up to 2 sqrt(K ftol chi2) apart
tolF = 2 * np.sqrt(K * FTOL * x2r)       # parameter-sigmas
dref = np.abs(col(wp, "ref", "FLUX") - col(wp, "cat", "FLUX")) / sabs
# the catalogue is float32: allow its own rounding on top
dref32 = np.maximum(dref - np.abs(col(wp, "cat", "FLUX")) * 2.0 ** -23 / sabs, 0)
check("ref FLUX == catalogue FLUX on well-posed fits, <= 2 sqrt(%g ftol chi2) sigma" % K, np.all(dref32 <= tolF),
      "%d fits; max %s sigma (tol %s-%s)" % (len(wp), fnum(np.max(dref32)), fnum(tolF.min()), fnum(tolF.max())))

# --- 2. specutils, on the same pixels, at the same chi2 minimum --------------------------------
npx, npar, nfit = col(fit, "su", "NPIX_STRICT"), col(fit, "su", "NPAR"), col(fit, "su", "NPIX_FIT")
has = np.isfinite(npx)
check("a specutils fit for every slot both DESI runs fitted", has.all(), "%d of %d" % (has.sum(), len(fit)))
check("fit_lines window pixels - free params == catalogue NDOF", np.array_equal(npx[has] - npar[has], col(fit, "cat", "NDOF")[has]),
      "every fitted slot")
check("fit_lines' inclusive window == emlinefit's strict window", np.array_equal(npx[has], nfit[has]), "pixel counts equal")
errs = [r.get("su_err") for r in fit if "su_err" in r]
say("info  specutils fit exceptions: %d %s" % (len(errs), sorted(set(e.split(":")[0] for e in errs))[:5]))

x2s = col(wp, "su", "CHI2")
dX = (x2s - x2r) / x2r
dF = np.abs(col(wp, "su", "FLUX") - col(wp, "ref", "FLUX")) / sabs
same = np.abs(dX) <= K * FTOL
lower = dX < -K * FTOL
higher = dX > K * FTOL
# The problem is not convex: from one starting point, two optimisers taking different steps can
# settle in different local minima, and neither is wrong. Asserted: they almost always share one
# (a broken fitter would not), and wherever they do, they agree.
check("same chi2 minimum on >= 99.9%% of well-posed fits (|dchi2|/chi2 <= %g)" % (K * FTOL), same.mean() >= 0.999,
      "%d of %d; specutils lower in %d, DESI lower in %d" % (same.sum(), len(wp), lower.sum(), higher.sum()))
check("at the same minimum, specutils F == DESI F, <= 2 sqrt(%g ftol chi2) sigma" % K, np.all(dF[same] <= tolF[same]),
      "max %s sigma; max dF/tol %s" % (fnum(np.max(dF[same]) if same.any() else 0),
                                       fnum(np.max(dF[same] / tolF[same]) if same.any() else 0)))
res["same_minimum"] = int(same.sum())
res["specutils_lower"] = int(lower.sum())
res["desi_lower"] = int(higher.sum())
for r, x in zip([r for r, h in zip(wp, lower | higher) if h], dX[lower | higher]):
    say("info  different minimum: pix %s row %d %s z=%.4f  sigma DESI %.3f specutils %.3f  F DESI %.4g specutils %.4g  dchi2/chi2 %+.3e" % (
        r["pix"], r["j"], r["line"], r["z"], r["ref"]["SIGMA"], r["su"]["SIGMA"], r["ref"]["FLUX"], r["su"]["FLUX"], x))
ewsu, ewc = col(wp, "su", "EW"), col(wp, "cat", "EW")
dEW = np.abs(ewsu - ewc) / np.abs(ewc)
tolEW = tolF * sabs / np.abs(col(wp, "ref", "FLUX")) + 4 * 2.0 ** -23
check("at the same minimum, specutils EW == catalogue EW (relative, from the F tolerance)", np.all(dEW[same] <= tolEW[same]),
      "max rel %s" % fnum(np.max(dEW[same]) if same.any() else 0))
ivs, ivc = col(wp, "su", "FLUX_IVAR"), col(wp, "cat", "FLUX_IVAR")
divr = np.abs(ivs / ivc - 1)
say("info  FLUX_IVAR, specutils (absolute-sigma covariance x chi2/dof) vs catalogue, same minimum: median %s, p99 %s, max %s" % (
    fnum(np.median(divr[same])), fnum(np.percentile(divr[same], 99)), fnum(np.max(divr[same]))))
res["ivar_rel_median"] = float(np.median(divr[same]))
res["ivar_rel_p99"] = float(np.percentile(divr[same], 99))

for name in LINES:
    m = np.array([r["line"] == name for r in wp])
    nall = sum(1 for r in fit if r["line"] == name)
    rec = {"fitted": nall, "wellposed": int(m.sum())}
    if m.any():
        sn = np.array([r["cat"]["FLUX"] * math.sqrt(r["cat"]["FLUX_IVAR"]) for r in wp])[m]
        rec.update({"same_minimum": int(same[m].sum()), "specutils_lower": int(lower[m].sum()),
                    "dF_max_same": float(np.max(dF[m & same])) if (m & same).any() else None,
                    "sn_median": float(np.median(sn))})
        say("info  %-6s DESI-fitted %6d  well-posed %6d  same minimum %6d  specutils lower %4d  |dF|/sigma max %s" % (
            name, nall, m.sum(), rec["same_minimum"], rec["specutils_lower"], fnum(rec["dF_max_same"] or 0)))
        if name == "OIII":
            sub = [r for r in wp if r["line"] == name]
            aF = np.abs(col(sub, "su_analytic", "FLUX") - col(sub, "ref", "FLUX")) / sabs[m]
            aX = (col(sub, "su_analytic", "CHI2") - x2r[m]) / x2r[m]
            aR = np.abs(col(sub, "su_analytic", "FLUX") / col(sub, "ref", "FLUX") - 1)
            rec.update({"analytic_off": int((aF > tolF[m]).sum()), "analytic_chi2_above": int((aX > K * FTOL).sum()),
                        "analytic_dF_median_sigma": float(np.nanmedian(aF)), "analytic_rel_flux_median": float(np.nanmedian(aR)),
                        "analytic_rel_flux_p90": float(np.nanpercentile(aR, 90))})
            say("info  OIII   fit_lines' default (analytic Jacobian, ties ignored): off the minimum in %d of %d, chi2 above DESI's in %d; "
                "|F/F_DESI - 1| median %s, p90 %s; |dF| median %s sigma" % (
                    rec["analytic_off"], m.sum(), rec["analytic_chi2_above"], fnum(rec["analytic_rel_flux_median"]),
                    fnum(rec["analytic_rel_flux_p90"]), fnum(rec["analytic_dF_median_sigma"])))
        sub = [r for r in wp if r["line"] == name]
        hi = sn >= 20
        if hi.sum() >= 10:
            b3, bm = col(sub, "su", "BOX3")[hi], col(sub, "su", "BOX3_MODEL")[hi]
            rec["n_sn20"] = int(hi.sum())
            rec["box3_over_gauss_median_sn20"] = float(np.nanmedian(b3 / bm))
            cen, ex = col(sub, "su", "CENTROID")[hi], col(sub, "su", "CENTRE_EXPECTED")[hi]
            dv = C_KMS * (cen / ex - 1)
            if np.isfinite(dv).sum() >= 10:
                rec["centroid_dv_median_kms_sn20"] = float(np.nanmedian(dv))
                rec["centroid_dv_mad_kms_sn20"] = float(np.nanmedian(np.abs(dv - np.nanmedian(dv))))
            say("info  %-6s S/N>=20 (%d): line_flux over +-3 sigma / fitted Gaussian flux, median %.4f (a Gaussian holds 0.9973); "
                "centroid vs redrock z %s km/s (MAD %s)" % (name, hi.sum(), rec["box3_over_gauss_median_sn20"],
                                                        fnum(rec.get("centroid_dv_median_kms_sn20", np.nan)),
                                                        fnum(rec.get("centroid_dv_mad_kms_sn20", np.nan))))
    res["per_line"][name] = rec

res["fails"] = fails
json.dump(res, open(os.path.join(OUT, "results.json"), "w"), indent=1, allow_nan=True)
if not fails:
    say("SPECUTILS REAL OK")
open(os.path.join(OUT, "check.txt"), "w").write("\n".join(lines_out) + "\n")
sys.exit(1 if fails else 0)
