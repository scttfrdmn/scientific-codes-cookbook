"""photutils on a whole Pan-STARRS1 projection cell: 100 r-band deep stacks (rings.v3 skycell
1749.000-099), each 6274 x 6258 px, against IPP's own catalogue of the same images (.cmf).

Per skycell, the ordinary photutils pipeline: Background2D -> convolve -> detect_sources ->
deblend_sources -> SourceCatalog, plus aperture photometry at every IPP detection. Each step is
checked by an identity it must satisfy (below), and the result is compared with IPP's
independent psphot catalogue of the same pixels.

    python3 -u real.py <in_dir> <out_dir> <n_proc>
"""
import hashlib
import json
import math
import os
import resource
import sys
import time
from multiprocessing import Pool

import numpy as np

IN, OUT, NPROC = sys.argv[1], sys.argv[2], int(sys.argv[3])
EPS = np.finfo(np.float64).eps
CELLS = ["%03d" % i for i in range(100)] if len(sys.argv) < 5 else sys.argv[4].split(",")


def ver(mod):
    return str(getattr(mod, "__version__", "unknown"))


def decode(fn):
    """PS1 stack pixels: astropy undoes RICE + BZERO/BSCALE; IPP's asinh layer is undone here
    (PS1 DR2 docs: LINEAR = BOFFSET + 2 BSOFTEN sinh(TRUE / a), a = 2.5 log10(e))."""
    from astropy.io import fits
    with fits.open(fn) as f:
        h = f[1].header
        x = np.asarray(f[1].data, dtype=np.float64)
    if "BSOFTEN" in h:
        x = h["BOFFSET"] + h["BSOFTEN"] * 2.0 * np.sinh(x / (2.5 / np.log(10.0)))
    return x


def h(a):
    return hashlib.sha256(np.ascontiguousarray(a).tobytes()).hexdigest()[:16]


def cell(c):
    from astropy.io import fits
    from astropy.convolution import convolve
    from scipy.spatial import cKDTree
    from photutils.aperture import CircularAperture, CircularAnnulus, aperture_photometry
    from photutils.background import Background2D, SExtractorBackground
    from photutils.centroids import centroid_com, centroid_sources
    from photutils.segmentation import (SourceCatalog, deblend_sources, detect_sources,
                                        make_2dgaussian_kernel)
    T = {}
    t = time.time()
    stem = "%s/ps1/rings.v3.skycell.1749.%s.stk.r.unconv" % (IN, c)
    img = decode(stem + ".fits")
    var = decode(stem + ".wt.fits")
    with fits.open(stem + ".mask.fits") as f:
        msk = np.asarray(f[1].data)
    with fits.open("%s/cmf/rings.v3.skycell.1749.%s.stk.r.cmf" % (IN, c)) as f:
        ipp = f["SkyChip.psf"].data
        xrad = f["SkyChip.xrad"].data
        rmax = [float(f["SkyChip.xrad"].header["RMAX_%02d" % j]) for j in range(4)]
        fwhm = float(f["SkyChip.hdr"].header["FWHM_MAJ"])
    bad = ~np.isfinite(img) | ~np.isfinite(var) | (msk != 0)
    ny, nx = img.shape
    T["decode"] = time.time() - t
    R = {"cell": c, "shape": [ny, nx], "n_bad": int(bad.sum())}
    chk = {}

    # 1. background, and its affine equivariance: B(2x + 1024) == 2 B(x) + 1024 -- sigma
    # clipping, the SExtractor mode estimator, the median filter and the interpolation are all
    # equivariant, so only rounding separates the two (a clipping flip would be O(1e-3)).
    t = time.time()
    kw = dict(filter_size=3, coverage_mask=bad, bkg_estimator=SExtractorBackground())
    bkg = Background2D(img, (128, 128), **kw)
    B, S = bkg.background, bkg.background_rms
    T["background"] = time.time() - t
    b2 = Background2D(2.0 * img + 1024.0, (128, 128), **kw)
    ok = ~bad
    eqv = float(np.max(np.abs(b2.background[ok] - (2.0 * B[ok] + 1024.0)) / (2.0 * np.abs(B[ok]) + 1024.0)))
    eqs = float(np.max(np.abs(b2.background_rms[ok] - 2.0 * S[ok]) / (2.0 * S[ok])))
    chk["bkg_affine"] = (max(eqv, eqs) < 1e-10, "%.2e / %.2e" % (eqv, eqs))
    del b2
    R["bkg_median"], R["rms_median"] = float(bkg.background_median), float(bkg.background_rms_median)

    # 2. detect + deblend on the PSF-matched (convolved) image, 1.5 sigma, 10 connected pixels
    t = time.time()
    sub = np.where(bad, 0.0, img - B)
    kern = make_2dgaussian_kernel(fwhm, size=2 * int(2 * fwhm) + 1)
    cv = convolve(sub, kern, normalize_kernel=True, mask=bad)
    cv[bad] = 0.0      # masked regions wider than the kernel come back NaN; they are masked below anyway
    T["convolve"] = time.time() - t
    t = time.time()
    seg = detect_sources(cv, 1.5 * S, n_pixels=10, mask=bad)
    T["detect"] = time.time() - t
    # detect_sources' definition, re-implemented with scipy: 8-connected regions of unmasked
    # pixels strictly above threshold, kept if they have >= 10 pixels -- the same pixel set, exactly
    from scipy import ndimage
    lab0, n0 = ndimage.label((cv > 1.5 * S) & ~bad, structure=np.ones((3, 3), int))
    sz = np.bincount(lab0.ravel())
    keep = sz >= 10
    keep[0] = False
    ref_support = keep[lab0]
    chk["detect_definition"] = (bool(np.array_equal(ref_support, seg.data > 0)) and int(keep.sum()) == seg.n_labels,
                                "%d regions >= 10 px (scipy) vs %d segments, pixel sets %s" % (
                                    int(keep.sum()), seg.n_labels, "identical" if np.array_equal(ref_support, seg.data > 0) else "DIFFER"))
    R["bkg_p99"] = float(np.percentile(B[~bad], 99))
    del lab0, ref_support
    t = time.time()
    dbl = deblend_sources(cv, seg, n_pixels=10, progress_bar=False)
    T["deblend"] = time.time() - t
    P, L = seg.data, dbl.data
    R["n_parent"], R["n_source"] = int(seg.n_labels), int(dbl.n_labels)

    # deblending is a partition of each parent: every child sits in exactly one parent, the pixel
    # sets are unchanged, and each parent's flux is the sum of its children's (exact areas,
    # rounding on flux: bound 4 n eps sum|x| per parent)
    lab = L > 0
    chk["deblend_support"] = (bool(np.array_equal(lab, P > 0)), "%d labelled px" % int(lab.sum()))
    pc = np.zeros(dbl.max_label + 1, np.int64)
    pc[L[lab]] = P[lab]
    one_parent = np.bincount(L[lab], minlength=dbl.max_label + 1)
    mixed = np.bincount(L[lab], weights=(P[lab] != pc[L[lab]]).astype(float), minlength=dbl.max_label + 1)
    chk["deblend_one_parent"] = (int(mixed.sum()) == 0, "%d children straddle a parent" % int(mixed.sum()))
    fp = np.bincount(P[lab], weights=sub[lab], minlength=seg.max_label + 1)
    ap_ = np.bincount(P[lab], weights=np.abs(sub[lab]), minlength=seg.max_label + 1)
    np_ = np.bincount(P[lab], minlength=seg.max_label + 1)
    fc = np.bincount(L[lab], weights=sub[lab], minlength=dbl.max_label + 1)
    fsum_par = np.bincount(pc, weights=fc, minlength=seg.max_label + 1)
    worst = float(np.max(np.abs(fsum_par - fp) / np.maximum(4 * np_ * EPS * ap_, 1e-300)))
    chk["deblend_flux"] = (worst <= 1.0, "max |sum children - parent| / (4 n eps sum|x|) = %.3f" % worst)
    asum = np.bincount(pc, weights=one_parent.astype(float), minlength=seg.max_label + 1)
    chk["deblend_area"] = (bool(np.array_equal(asum.astype(np.int64), np_)), "areas exact")

    # 3. SourceCatalog vs an independent bincount over the same labels and pixels
    t = time.time()
    cat = SourceCatalog(sub, dbl, convolved_data=cv, mask=bad)
    sflux = np.asarray(cat.segment_flux, float)
    area = np.asarray(cat.area.value, float)
    xc, yc = np.asarray(cat.x_centroid, float), np.asarray(cat.y_centroid, float)
    T["catalog"] = time.time() - t
    labs = np.asarray(cat.labels)
    fc_l, nc_l = fc[labs], one_parent[labs]
    ac_l = np.bincount(L[lab], weights=np.abs(sub[lab]), minlength=dbl.max_label + 1)[labs]
    wc = float(np.max(np.abs(sflux - fc_l) / np.maximum(4 * nc_l * EPS * ac_l, 1e-300)))
    chk["catalog_flux"] = (wc <= 1.0, "max |segment_flux - bincount| / (4 n eps sum|x|) = %.3f" % wc)
    chk["catalog_area"] = (bool(np.array_equal(area.astype(np.int64), nc_l)), "%d sources" % len(labs))
    # conservation over the whole image: labelled + unlabelled == total
    tot, lsum, usum = math.fsum(sub[ok]), math.fsum(sflux), math.fsum(sub[ok & ~lab])
    tb = 4 * ok.sum() * EPS * float(np.abs(sub[ok]).sum())
    chk["image_conservation"] = (abs(lsum + usum - tot) <= tb, "|lab + unlab - total| = %.3e (bound %.1e)" % (abs(lsum + usum - tot), tb))
    R["frac_flux_in_sources"] = lsum / tot if tot else float("nan")

    # 4. aperture photometry at every IPP detection, IPP pixel centres at +0.5 (measured below)
    t = time.time()
    X = np.asarray(ipp["X_PSF"], float) - 0.5
    Y = np.asarray(ipp["Y_PSF"], float) - 0.5
    rm = rmax[3] + 2
    inside = np.isfinite(X) & np.isfinite(Y) & (X > rm) & (Y > rm) & (X < nx - 1 - rm) & (Y < ny - 1 - rm)
    pos = np.c_[X[inside], Y[inside]]
    circ = [CircularAperture(pos, r=r) for r in rmax]
    rings = [CircularAperture(pos, r=rmax[0])] + [CircularAnnulus(pos, r_in=rmax[j - 1], r_out=rmax[j]) for j in (1, 2, 3)]
    def phot(ap, data, m=None):
        return np.asarray(aperture_photometry(data, ap, method="exact", mask=m)["aperture_sum"], float)
    fC = [phot(a, sub, bad) for a in circ]
    fR = [phot(a, sub, bad) for a in rings]
    absC = phot(circ[3], np.abs(sub), bad)
    T["apertures"] = time.time() - t
    # annulus additivity: circle(r3) == circle(r0) + 3 annuli, elementwise (exact overlap weights
    # are computed independently per shape, so only rounding separates them)
    add = np.abs(fC[3] - (fR[0] + fR[1] + fR[2] + fR[3])) / np.maximum(absC, 1e-300)
    chk["annulus_additivity"] = (float(add.max()) < 1e-12, "max rel %.2e over %d IPP positions" % (float(add.max()), len(pos)))
    # the exact overlap weights integrate the circle: on an image of ones, each aperture == pi r^2
    ones = np.ones_like(sub)
    ar = max(float(np.max(np.abs(phot(a, ones) / (math.pi * a.r ** 2) - 1))) for a in circ)
    chk["aperture_area"] = (ar < 1e-12, "max |sum w / (pi r^2) - 1| = %.2e" % ar)
    # error propagation is a sum of variances
    e = np.asarray(aperture_photometry(sub, circ[2], method="exact", mask=bad, error=np.sqrt(np.where(bad, 0, var)))["aperture_sum_err"], float)
    v = phot(circ[2], np.where(bad, 0, var), bad)
    hv = v > 0         # an aperture wholly inside a masked region has no variance to propagate
    ev = float(np.max(np.abs(e[hv] ** 2 / v[hv] - 1)))
    chk["error_is_sqrt_var"] = (ev < 1e-12 and np.all(e[~hv] == 0), "max |err^2 / sum var - 1| = %.2e over %d apertures" % (ev, int(hv.sum())))

    # 5. against IPP (psphot, an independent code): isolated bright stars only
    mag = np.asarray(ipp["PSF_INST_MAG"], float)
    fin = np.isfinite(X) & np.isfinite(Y)
    tree = cKDTree(np.c_[X[fin], Y[fin]])
    nn = np.full(len(X), 0.0)
    nn[fin] = tree.query(np.c_[X[fin], Y[fin]], k=2)[0][:, 1]
    sel = np.where(inside & (nn > 25) & (mag < -12) & (mag > -15.5))[0]
    ix, iy = np.rint(X[sel]).astype(int), np.rint(Y[sel]).astype(int)
    clean = np.array([not bad[y - 6:y + 7, x - 6:x + 7].any() for x, y in zip(ix, iy)], bool)
    sel, ix, iy = sel[clean], ix[clean], iy[clean]
    cx, cy = centroid_sources(sub, X[sel], Y[sel], box_size=11, centroid_func=centroid_com)
    R["iso"] = {"n": int(len(sel)), "dx": (cx - X[sel]).tolist(), "dy": (cy - Y[sel]).tolist()}
    R["iso"]["detected"] = int((L[iy, ix] > 0).sum())
    try:     # a description of the misses must never be able to fail the run
        allmag, fpos = mag[fin], np.cumsum(fin) - 1
        miss = []
        for k in np.where(L[iy, ix] == 0)[0]:
            i, x, y = int(sel[k]), ix[k], iy[k]
            near = tree.query_ball_point([X[i], Y[i]], 150.0)
            bright = min([float(allmag[j]) for j in near if j != fpos[i]] or [99.0])
            dd = np.hypot(xc - X[i], yc - Y[i])
            miss.append({"cell": c, "x": float(X[i]), "y": float(Y[i]), "mag": float(mag[i]),
                         "flags": "0x%x" % int(ipp["FLAGS"][i]), "flags2": "0x%x" % int(ipp["FLAGS2"][i]),
                         "psf_qf": float(ipp["PSF_QF"][i]), "chisq": float(ipp["PSF_CHISQ"][i]),
                         "ext_nsig": float(ipp["EXT_NSIGMA"][i]), "cr_nsig": float(ipp["CR_NSIGMA"][i]),
                         "n_frames": int(ipp["N_FRAMES"][i]), "peak_sub_5x5": float(sub[y - 2:y + 3, x - 2:x + 3].max()),
                         "cv": float(cv[y, x]), "thresh": float(1.5 * S[y, x]), "bkg": float(B[y, x]),
                         "nearest_src_px": float(dd.min()) if len(dd) else -1.0, "brightest_ipp_within_150px": bright})
    except Exception as ex:
        miss = [{"cell": c, "diagnostic_error": repr(ex)}]
    R["iso"]["missed"] = miss
    # IPP's r = 12 px radial aperture on the unconvolved image (PSF_FWHM row != 6, 8)
    xr_id = np.asarray(xrad["IPP_IDET"]).astype(np.int64)
    unc = ~np.isin(np.asarray(xrad["PSF_FWHM"], float), (6.0, 8.0))
    idmap = {int(k): i for i, k in enumerate(np.asarray(xr_id[unc]))}
    ipp_id = np.asarray(ipp["IPP_IDET"]).astype(np.int64)
    rows = np.array([idmap.get(int(k), -1) for k in ipp_id[sel]])
    af = np.asarray(xrad["APER_FLUX"], float)[unc][:, 2]
    pidx = {int(i): k for k, i in enumerate(np.where(inside)[0])}
    mine = np.array([fC[2][pidx[int(i)]] for i in sel])
    raw = phot(CircularAperture(np.c_[X[sel], Y[sel]], r=rmax[2]), np.where(bad, 0.0, img), bad)
    ref = np.where(rows >= 0, af[rows], np.nan)
    R["iso"]["ratio_r12"] = (mine / ref).tolist()
    R["iso"]["ratio_r12_raw"] = (raw / ref).tolist()
    R["n_ipp"] = int(len(ipp))

    R["checks"] = {k: [bool(v[0]), v[1]] for k, v in chk.items()}
    R["time"] = T
    R["hash"] = {"background": h(B), "rms": h(S), "seg": h(P), "deblend": h(L), "segment_flux": h(sflux),
                 "centroid": h(np.c_[xc, yc]), "aper": h(np.c_[fC[0], fC[1], fC[2], fC[3]])}
    np.savez_compressed("%s/cells/%s.npz" % (OUT, c), x=xc, y=yc, flux=sflux, area=area,
                        aper=np.c_[fC[0], fC[1], fC[2], fC[3]], bkg_med=np.array([R["bkg_median"]]))
    R["maxrss_gib"] = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 2 ** 20
    print("cell %s: %d parents -> %d sources, %d IPP rows, %s s, %s" % (
        c, R["n_parent"], R["n_source"], R["n_ipp"], round(sum(T.values()), 1),
        " ".join("%s=%s" % (k, "ok" if v[0] else "FAIL") for k, v in chk.items())), flush=True)
    return R


def main():
    import astropy, photutils, scipy
    os.makedirs(OUT + "/cells", exist_ok=True)
    print("photutils %s astropy %s numpy %s scipy %s" % (ver(photutils), ver(astropy), ver(np), ver(scipy)), flush=True)
    t0 = time.time()
    pool = Pool(NPROC, maxtasksperchild=4)
    res = pool.map(cell, CELLS, chunksize=1)
    pool.close()
    pool.join()      # reap the workers, so RUSAGE_CHILDREN covers them
    wall = time.time() - t0
    lines, bad = [], []
    def rec(name, ok, detail):
        lines.append("%-22s %-4s %s" % (str(name), "ok" if ok else "FAIL", str(detail)))
        if not bool(ok):
            bad.append(str(name))
    # every per-cell identity, on every cell
    for k in res[0]["checks"]:
        fails = [r["cell"] for r in res if not r["checks"][k][0]]
        rec(k, not fails, "%d/%d cells; e.g. %s%s" % (len(res) - len(fails), len(res), res[0]["checks"][k][1],
                                                     ("; FAIL " + ",".join(fails[:5])) if fails else ""))
    # IPP cross-code, pooled over the projection cell
    dx = np.concatenate([r["iso"]["dx"] for r in res]); dy = np.concatenate([r["iso"]["dy"] for r in res])
    n = int(sum(r["iso"]["n"] for r in res)); det = int(sum(r["iso"]["detected"] for r in res))
    mdx, mdy = float(np.nanmedian(dx)), float(np.nanmedian(dy))
    rec("ipp_centroid", abs(mdx) < 0.05 and abs(mdy) < 0.05,
        "median photutils - IPP over %d isolated stars: dx %+.4f dy %+.4f px (a half-pixel convention error is 0.5); MAD %.4f/%.4f"
        % (n, mdx, mdy, float(np.nanmedian(np.abs(dx - mdx))), float(np.nanmedian(np.abs(dy - mdy)))))
    summary_missed = [m for r in res for m in r["iso"]["missed"]]
    mc = sorted(set(m["cell"] for m in summary_missed))
    mb = [m["bkg"] for m in summary_missed if "bkg" in m]
    lines.append("%-22s %-4s %s" % ("ipp_detected", "obs", "%d of %d isolated IPP stars lie in a photutils segment; the %d misses are in cells %s, local background %s counts (all-cell median %.1f)" % (
        det, n, n - det, ",".join(mc) or "-", ("%.0f-%.0f" % (min(mb), max(mb))) if mb else "-", float(np.median([r["bkg_median"] for r in res])))))
    rr = np.concatenate([r["iso"]["ratio_r12"] for r in res]); rr = rr[np.isfinite(rr)]
    med = float(np.median(rr)); mad = float(np.median(np.abs(rr - med)))
    lines.append("%-22s %-4s %s" % ("ipp_flux_r12", "obs", "photutils (own background) / IPP APER_FLUX (r 12 px, unconvolved) over %d stars: median %.5f, MAD %.5f" % (len(rr), med, mad)))
    r2 = np.concatenate([r["iso"]["ratio_r12_raw"] for r in res]); r2 = r2[np.isfinite(r2)]
    m2 = float(np.median(r2))
    lines.append("%-22s %-4s %s" % ("ipp_flux_r12_raw", "obs", "same, no background subtracted: median %.5f, MAD %.5f" % (m2, float(np.median(np.abs(r2 - m2))))))
    nsrc = sum(r["n_source"] for r in res); npar = sum(r["n_parent"] for r in res); nipp = sum(r["n_ipp"] for r in res)
    lines.append("%-22s %-4s %d parents -> %d deblended sources; IPP catalogue %d rows" % ("totals", "obs", npar, nsrc, nipp))
    tt = {k: round(sum(r["time"][k] for r in res), 1) for k in res[0]["time"]}
    summary = {"wall_s": round(wall, 1), "nproc": NPROC, "cells": len(res), "stage_cpu_s": tt,
               "max_child_rss_gib": round(max(r["maxrss_gib"] for r in res), 2),
               "ru_maxrss_children_gib": round(resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss / 2 ** 20, 2),
               "sources": nsrc, "parents": npar, "ipp_rows": nipp,
               "hash": {r["cell"]: r["hash"] for r in res}, "missed": summary_missed,
               "per_cell": {r["cell"]: {k: r[k] for k in ("n_parent", "n_source", "n_ipp", "bkg_median", "rms_median", "frac_flux_in_sources", "n_bad")} for r in res}}
    json.dump(summary, open(OUT + "/results.json", "w"), indent=1)
    lines.append("wall %.1f s, %d processes, stage CPU-seconds %s" % (wall, NPROC, tt))
    lines.append("PHOTUTILS REAL OK" if not bad else "PHOTUTILS REAL FAILED " + ",".join(bad))
    open(OUT + "/check.txt", "w").write("\n".join(lines) + "\n")
    print("\n".join(lines), flush=True)
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
