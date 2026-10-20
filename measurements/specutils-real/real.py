#!/usr/bin/env python3
"""specutils-real: re-measure DESI DR1's own emission-line catalogue with specutils.

For every spectrum in every staged healpix coadd (DESI DR1 / iron, main survey, dark program),
this reads the coadd with specutils' own `DESI coadd` loader and fits the six lines DESI's
emission-line afterburner fits (desispec.emlinefit at the iron commit), with
`specutils.fitting.fit_lines`, on the same pixels, from the same starting point, under the same
model. It then compares the result with the `emline-*.fits` file DESI published next to the coadd.

Two references are computed so a disagreement can be placed:
  ref  DESI's own function (desispec emlinefit.py @ 952a9a29d0, staged and pinned), run here on the
       arrays this script reconstructs. ref == catalogue proves the inputs are DESI's inputs.
  su   specutils fit_lines. su == ref (on the well-posed fits) is the claim this run is about.

    python3 real.py <in_dir> <out_dir> <nproc> [pix,pix,...]
"""
import json
import math
import os
import sys
import time
import resource
import warnings
from multiprocessing import Pool

import numpy as np

IN, OUT, NPROC = sys.argv[1], sys.argv[2], int(sys.argv[3])
PIXSEL = sys.argv[4].split(",") if len(sys.argv) > 4 and sys.argv[4] else None
sys.path.insert(0, os.environ.get("DESI_SRC", "/tmp/desi"))   # the pinned desispec/desiutil sources plus a log stub

LINES = ["OII", "HDELTA", "HGAMMA", "HBETA", "OIII", "HALPHA"]
RV = 3.1
SQ2PI = math.sqrt(2.0 * math.pi)
# the iron emlinefit defaults (emlinefit.py @ 952a9a29d0, emlines_gaussfit signature)
P0_SIGMA, P0_FLUX, P0_SHARE = 2.5, 10.0, 0.58
MIN_SIGMA, MAX_SIGMA = 1e-5, 10.0
OIII_SH = 0.744
FIELDS = ["FLUX", "FLUX_IVAR", "SIGMA", "SIGMA_IVAR", "CONT", "EW", "CHI2", "NDOF", "SHARE"]


def rec(fh, *a):
    s = " ".join(str(x) for x in a)
    print(s, flush=True)
    fh.write(s + "\n")
    fh.flush()


def versions():
    import astropy, scipy, specutils
    v = {"specutils": specutils.__version__, "astropy": astropy.__version__,
         "numpy": np.__version__, "scipy": scipy.__version__,
         "python": sys.version.split()[0]}
    try:
        from threadpoolctl import threadpool_info
        v["blas"] = ";".join("%s:%s:%s" % (d.get("internal_api"), d.get("version"), d.get("architecture"))
                             for d in threadpool_info())
    except Exception as e:                       # descriptive only; must never fail the run
        v["blas"] = "unknown (%s)" % type(e).__name__
    return v


def _oii(x, flux=P0_FLUX, sigma=P0_SIGMA, share=P0_SHARE, w0=3727.092, w1=3729.874, cont=0.0):
    g = lambda F, w: F * np.exp(-(x - w) ** 2 / (2 * sigma ** 2)) / (sigma * SQ2PI)
    return cont + g((1 - share) * flux, w0) + g(share * flux, w1)


_OII = None


def build_model(name, obs, cont):
    """The emlinefit model, in astropy.modeling terms: a FIXED continuum plus Gaussians at the
    FIXED redshifted centres, same bounds, same starting point.

    Balmer: Const1D + Gaussian1D. emlinefit parametrises a line by (sigma, integrated flux F0),
    Gaussian1D by (amplitude, stddev), amplitude = F0 / (sigma sqrt(2 pi)): same family.
    [OIII]: two Gaussian1D, 4960 amplitude and stddev tied to 5007's at the fixed 0.744 share.
    [OII]: the doublet ratio is FREE but bounded (share in [0.1, 1]), which a sum of Gaussian1D
    cannot express, so it is emlinefit's own (flux, sigma, share) parametrisation as an astropy
    custom_model -- fitted by the same fit_lines call."""
    global _OII
    from astropy.modeling import models, custom_model
    c = models.Const1D(cont, fixed={"amplitude": True})
    a0 = P0_FLUX / (P0_SIGMA * SQ2PI)
    sb = {"stddev": (MIN_SIGMA, MAX_SIGMA)}
    if name == "OII":
        if _OII is None:
            _OII = custom_model(_oii)
        return _OII(flux=P0_FLUX, sigma=P0_SIGMA, share=P0_SHARE, w0=obs[0], w1=obs[1], cont=cont,
                    fixed={"w0": True, "w1": True, "cont": True},
                    bounds={"sigma": (MIN_SIGMA, MAX_SIGMA), "share": (0.1, 1.0), "flux": (-1e9, 1e9)})
    if name == "OIII":                # fixed 4960:5007 = (1-0.744):0.744, tied sigma
        g2 = models.Gaussian1D(OIII_SH * a0, obs[1], P0_SIGMA, fixed={"mean": True}, bounds=sb)
        g1 = models.Gaussian1D((1 - OIII_SH) * a0, obs[0], P0_SIGMA, fixed={"mean": True}, bounds=sb,
                               tied={"amplitude": lambda m: m.amplitude_2 * (1 - OIII_SH) / OIII_SH,
                                     "stddev": lambda m: m.stddev_2})
        return c + g1 + g2
    return c + models.Gaussian1D(a0, obs[0], P0_SIGMA, fixed={"mean": True}, bounds=sb)


def read_su(fm, name):
    """(F, sigma, share, var(F)) from a fitted model, var by the delta method on cov_matrix."""
    if name == "HALPHA" or name.startswith("H"):
        a, s = fm.amplitude_1.value, fm.stddev_1.value
        F, sh = a * s * SQ2PI, np.nan
        J = {"amplitude_1": s * SQ2PI, "stddev_1": a * SQ2PI}
    elif name == "OII":
        F, s, sh = float(fm.flux.value), float(fm.sigma.value), float(fm.share.value)
        J = {"flux": 1.0}
    else:                             # OIII: F0 = amplitude_2 sigma sqrt(2 pi) / 0.744
        a, s = fm.amplitude_2.value, fm.stddev_2.value
        F, sh = a * s * SQ2PI / OIII_SH, OIII_SH
        J = {"amplitude_2": s * SQ2PI / OIII_SH, "stddev_2": a * SQ2PI / OIII_SH}
    var = var_s = np.nan
    cm = getattr(fm, "cov_matrix", None)
    if cm is not None:
        names = list(cm.param_names)
        C = np.asarray(cm.cov_matrix)
        j = np.array([J.get(n, 0.0) for n in names])
        var = float(j @ C @ j)
        sname = {"OIII": "stddev_2", "OII": "sigma"}.get(name, "stddev_1")
        var_s = float(C[names.index(sname), names.index(sname)])
    return F, s, sh, var, var_s


def one_pixel(pix):
    warnings.simplefilter("ignore")
    import astropy.units as u
    from astropy.io import fits
    from astropy.nddata import InverseVariance
    from specutils import Spectrum, SpectrumList
    from specutils.fitting import fit_lines
    from specutils.manipulation import extract_region
    from specutils.analysis import line_flux, centroid
    from specutils import SpectralRegion
    from desispec_iron_emlinefit import emlines_gaussfit, get_rf_em_waves
    from desiutil_dust import ext_odonnell, ebv as dust_ebv

    t0 = time.time()
    cpath = os.path.join(IN, "coadd-main-dark-%s.fits" % pix)
    epath = os.path.join(IN, "emline-main-dark-%s.fits" % pix)
    chk = {}

    # --- the loader, against the raw HDUs ---------------------------------------------------
    sl = SpectrumList.read(cpath, format="DESI coadd")
    with fits.open(cpath, memmap=False) as h:
        raw = {b: (h[b + "_WAVELENGTH"].data.astype("f8"), np.array(h[b + "_FLUX"].data),
                   np.array(h[b + "_IVAR"].data), np.array(h[b + "_MASK"].data))
               for b in ("B", "R", "Z")}
        fmap = np.array(h["FIBERMAP"].data)
        bunit = h["B_FLUX"].header["BUNIT"]
    bands = [s.meta["band"] for s in sl]
    loader_ok = bands == ["b", "r", "z"]
    for s, b in zip(sl, ("B", "R", "Z")):
        w, f, iv, m = raw[b]
        loader_ok &= np.array_equal(s.spectral_axis.value, w)
        loader_ok &= np.array_equal(np.asarray(s.flux.value, dtype=f.dtype), f) and s.flux.shape == f.shape
        loader_ok &= np.array_equal(np.asarray(s.uncertainty.array, dtype=iv.dtype), iv)
        loader_ok &= np.array_equal(s.mask, m != 0)
    fm_tab = sl[0].meta["fibermap"]
    loader_ok &= np.array_equal(np.asarray(fm_tab["TARGETID"]), fmap["TARGETID"])
    chk["loader_identical"] = bool(loader_ok)
    chk["flux_unit"] = str(sl[0].flux.unit)
    chk["bunit"] = bunit

    em = fits.getdata(epath, "EMLINEFIT")
    tid_c, tid_e = fmap["TARGETID"], em["TARGETID"]
    chk["n_coadd"], chk["n_emline"] = int(tid_c.size), int(tid_e.size)
    chk["tid_unique"] = bool(np.unique(tid_c).size == tid_c.size and np.unique(tid_e).size == tid_e.size)
    chk["tid_same_set"] = bool(np.array_equal(np.sort(tid_c), np.sort(tid_e)))
    idx = {t: i for i, t in enumerate(tid_c)}

    # --- per spectrum: DESI's input arrays, exactly as read_emlines_inputs builds them ---------
    exts = {b: ext_odonnell(raw[b][0], Rv=RV) for b in raw}
    rows = []
    # E(B-V) exactly as emlinefit gets it: desiutil.dust.ebv at (TARGET_RA, TARGET_DEC) on the SFD
    # maps. The fibermap's EBV column is NOT a substitute: it is 0 for sky fibres (which emlinefit
    # fits too) and differs from this lookup by up to ~1e-6 mag on targets, enough to move CONT.
    ebvs = np.asarray(dust_ebv(np.asarray(fmap["TARGET_RA"], dtype="f8"), np.asarray(fmap["TARGET_DEC"], dtype="f8"),
                               mapdir=os.environ.get("SFD_DIR", "/work/sfd")), dtype="f8")
    objtype = np.char.strip(np.asarray(fmap["OBJTYPE"]).astype(str))
    tgt = objtype == "TGT"
    chk["n_tgt"] = int(tgt.sum())
    chk["fibermap_ebv_maxabs_diff_tgt"] = float(np.max(np.abs(fmap["EBV"][tgt].astype("f8") - ebvs[tgt]))) if tgt.any() else 0.0
    for j in range(tid_e.size):
        i = idx[tid_e[j]]
        z = float(em["Z"][j])
        ebv = float(ebvs[i])
        waves = np.zeros(0)
        fl = np.zeros(0)
        iv = np.zeros(0)
        for b in ("B", "R", "Z"):
            tw = raw[b][0]
            tf = raw[b][1][i].copy()             # float32, in place, as desispec does
            ti = raw[b][2][i].copy()
            tr = 10 ** (-0.4 * ebv * RV * exts[b])
            tf /= tr
            ti *= tr ** 2
            waves, fl, iv = np.append(waves, tw), np.append(fl, tf), np.append(iv, ti)
        order = np.argsort(waves, kind="stable")
        good = np.isfinite(fl) & (iv > 0)
        spec = Spectrum(spectral_axis=waves[order] * u.AA, flux=fl[order] * u.Unit(bunit),
                        uncertainty=InverseVariance(iv[order] * u.Unit(bunit) ** -2),
                        mask=~good[order])
        for name in LINES:
            cat = {k: em[name + "_" + k][j] for k in FIELDS}
            ref, ok = emlines_gaussfit(name, z, waves, fl, iv)
            r = {"pix": pix, "j": j, "line": name, "z": z, "tgt": bool(tgt[i]),
                 "cat": {k: float(cat[k]) for k in FIELDS},
                 "ref": {k: float(ref[k]) for k in FIELDS}}
            if ref["NDOF"] >= 0 and np.isfinite(ref["CONT"]):
                rest = get_rf_em_waves(name)
                obs = (1 + z) * rest
                hw = (1 + z) * 40.0
                lo, hi = obs.min() - hw, obs.max() + hw
                inwin = (waves > lo) & (waves < hi) & good
                tied = name == "OIII"
                sel = (spec.spectral_axis.value >= lo) & (spec.spectral_axis.value <= hi) & good[order]
                x, y, wt = spec.spectral_axis.value[sel], spec.flux.value[sel], iv[order][sel]
                # chi2 at DESI's (ref's) parameters, on the same pixels
                g = lambda ws, sg, F0, w0: F0 * np.exp(-(ws - w0) ** 2 / (2 * sg ** 2)) / (sg * SQ2PI)
                if name == "OII":
                    mref = ref["CONT"] + g(x, ref["SIGMA"], (1 - ref["SHARE"]) * ref["FLUX"], obs[0]) + g(x, ref["SIGMA"], ref["SHARE"] * ref["FLUX"], obs[1])
                elif name == "OIII":
                    mref = ref["CONT"] + g(x, ref["SIGMA"], (1 - OIII_SH) * ref["FLUX"], obs[0]) + g(x, ref["SIGMA"], OIII_SH * ref["FLUX"], obs[1])
                else:
                    mref = ref["CONT"] + g(x, ref["SIGMA"], ref["FLUX"], obs[0])
                chi2_ref = float(np.sum((y - mref) ** 2 * wt))
                # A tied model is fitted twice: with estimate_jacobian=True ("su"), and with
                # fit_lines' default fitter as is ("su_analytic"). The compound model's analytic
                # derivative ignores the ties, so the default run optimises with a wrong Jacobian.
                for key, ej in (("su", True), ("su_analytic", False)) if tied else (("su", False),):
                    try:
                        model = build_model(name, obs, ref["CONT"])
                        fmod = fit_lines(spec, model, window=(lo * u.AA, hi * u.AA), weights="unc",
                                         maxiter=100000, acc=1e-12, estimate_jacobian=ej)
                        F, s, sh, var, var_s = read_su(fmod, name)
                        chi2_su = float(np.sum((y - np.asarray(fmod(x * u.AA).value)) ** 2 * wt))
                        npar = len(model.parameters) - sum(model.fixed.values()) - sum(bool(t) for t in model.tied.values())
                        dof = int(sel.sum()) - npar
                        su = {"FLUX": F, "SIGMA": s, "SHARE": sh, "VAR_ABS": var,
                              # curve_fit (absolute_sigma=False) scales the covariance by chi2/dof;
                              # astropy treats weights as absolute. Same matrix otherwise.
                              "FLUX_IVAR": 1.0 / (var * chi2_su / dof) if var > 0 and dof > 0 else np.nan,
                              "SIGMA_IVAR": 1.0 / (var_s * chi2_su / dof) if var_s > 0 and dof > 0 else np.nan,
                              "EW": F / ((1 + z) * ref["CONT"]),
                              "NPIX_STRICT": int(inwin.sum()), "NPIX_FIT": int(sel.sum()), "NPAR": int(npar),
                              "CHI2": chi2_su, "CHI2_AT_REF": chi2_ref}
                        if key == "su" and s > 0.3 and np.isfinite(F):
                            # specutils' boxcar flux and centroid over +-3 sigma of the fitted line
                            if name == "OII":
                                reg, part, c0 = SpectralRegion((obs[0] - 3 * s) * u.AA, (obs[1] + 3 * s) * u.AA), 1.0, None
                            elif name == "OIII":
                                reg, part, c0 = SpectralRegion((obs[1] - 3 * s) * u.AA, (obs[1] + 3 * s) * u.AA), OIII_SH, obs[1]
                            else:
                                reg, part, c0 = SpectralRegion((obs[0] - 3 * s) * u.AA, (obs[0] + 3 * s) * u.AA), 1.0, obs[0]
                            sub = Spectrum(spectral_axis=spec.spectral_axis,
                                           flux=spec.flux - ref["CONT"] * spec.flux.unit, mask=spec.mask)
                            try:
                                su["BOX3"] = float(line_flux(sub, regions=reg).value)
                                su["BOX3_MODEL"] = part * F
                                if c0 is not None:
                                    su["CENTROID"] = float(centroid(sub, regions=reg).value)
                                    su["CENTRE_EXPECTED"] = float(c0)
                            except Exception as e:
                                su["BOX_ERR"] = type(e).__name__
                        r[key] = su
                    except Exception as e:
                        r[key + "_err"] = "%s: %s" % (type(e).__name__, str(e)[:200])
            rows.append(r)
    del sl
    chk["seconds"] = round(time.time() - t0, 2)
    chk["maxrss_kib"] = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    with open(os.path.join(OUT, "pix", "%s.json" % pix), "w") as fh:
        json.dump({"chk": chk, "rows": rows}, fh, allow_nan=True)
    return pix, chk


def main():
    os.makedirs(os.path.join(OUT, "pix"), exist_ok=True)
    pixs = sorted(f.split("-")[-1][:-5] for f in os.listdir(IN) if f.startswith("coadd-"))
    if PIXSEL:
        pixs = [p for p in pixs if p in PIXSEL]
    log = open(os.path.join(OUT, "progress.txt"), "w")
    rec(log, "versions", json.dumps(versions()))
    rec(log, "pixels", len(pixs), "workers", NPROC)
    t = time.time()
    with Pool(NPROC, maxtasksperchild=4) as p:
        for pix, chk in p.imap_unordered(one_pixel, pixs):
            rec(log, "done", pix, json.dumps(chk))
    rec(log, "fit wall_s", round(time.time() - t, 1))
    rec(log, "parent maxrss_kib", resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
        "children maxrss_kib", resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss)
    rec(log, "FITS DONE")


if __name__ == "__main__":
    main()
