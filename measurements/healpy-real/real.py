"""healpy on the Planck PR3 SMICA CMB map at full resolution (Nside 2048, 50,331,648 pixels).

    python3 -u real.py <pr3 dir> <out dir>

Four jobs a CMB user runs with healpy, each checked by an identity it must satisfy:
  1. read + reorder   the NESTED IQU map and the common masks; ring<->nest is a permutation
  2. pixel geometry   every pixel, both orderings, against astropy-healpix (an independent code);
                      query_disc against brute force over all pixel centres
  3. ud_grade         2048 -> 1 conservation, and 2048 -> 4096 -> 2048 bit-exact
  4. harmonics        anafast IQU to lmax 3*nside-1, masked and full sky; synthesis against direct
                      scipy Y_lm; map2alm convergence over iterations; rotation invariance of C_l;
                      smoothing identity; a seeded synfast from Planck's best fit; the first
                      acoustic peak against Planck's published TT spectrum
Every check prints `[ok]` or `[FAIL]`; the run ends `HEALPY REAL OK` only if all pass.
"""
import hashlib
import json
import math
import os
import resource
import sys
import time

import numpy as np
import healpy as hp
import scipy
import scipy.special
import astropy
from astropy.io import fits
import astropy_healpix
from astropy_healpix import core as ahc

IN, OUT = sys.argv[1], sys.argv[2]
os.makedirs(OUT, exist_ok=True)
EPS = np.finfo(np.float64).eps
NSIDE = int(os.environ.get("HEALPY_REAL_NSIDE", "2048"))   # override only for a scratch syntax run
NPIX = 12 * NSIDE * NSIDE
LMAX = 3 * NSIDE - 1
LOG = []
RES = {}
FAIL = []
T = {}


def rec(*a):
    s = " ".join(str(x) for x in a)
    print(s, flush=True)
    LOG.append(s)


def chk(label, ok, detail):
    ok = bool(ok)
    rec("%-34s %s  [%s]" % (label, detail, "ok" if ok else "FAIL"))
    RES[label] = {"ok": ok, "detail": str(detail)}
    if not ok:
        FAIL.append(label)


def le(x, bound):
    """x <= bound, and False for NaN (a NaN passes every one-sided bound otherwise)."""
    return bool(np.isfinite(x)) and bool(x <= bound)


def h(a):
    return hashlib.sha256(np.ascontiguousarray(a).tobytes()).hexdigest()[:16]


class timer:
    def __init__(self, name):
        self.name = name

    def __enter__(self):
        self.t = time.time()
        rec("== %s" % self.name)

    def __exit__(self, *a):
        T[self.name] = round(time.time() - self.t, 2)
        rec("   (%s: %.1f s)" % (self.name, T[self.name]))


def ver(mod):
    return str(getattr(mod, "__version__", "unknown"))


T0 = time.time()
rec("healpy", ver(hp), "numpy", ver(np), "scipy", ver(scipy), "astropy", ver(astropy),
    "astropy-healpix", ver(astropy_healpix), "OMP_NUM_THREADS", os.environ.get("OMP_NUM_THREADS"))
try:
    import ctypes, glob
    lib = sorted(glob.glob(os.path.join(sys.prefix, "lib", "libopenblas*.so*")))
    core = ctypes.CDLL(lib[0]).openblas_get_corename
    core.restype = ctypes.c_char_p
    rec("openblas_corename", core().decode())
except Exception as e:  # a description of the run must never fail it
    rec("openblas_corename unavailable:", repr(e))

SMICA = os.path.join(IN, "COM_CMB_IQU-smica_2048_R3.00_full.fits")
MINT = os.path.join(IN, "COM_Mask_CMB-common-Mask-Int_2048_R3.00.fits")
MPOL = os.path.join(IN, "COM_Mask_CMB-common-Mask-Pol_2048_R3.00.fits")

# ---------------------------------------------------------------- 1. read + reorder
with timer("read"):
    with fits.open(SMICA, memmap=True) as hd:
        hdr = hd[1].header
        cols = [c.name for c in hd[1].columns]
        rec("smica columns", cols, "ORDERING", hdr.get("ORDERING"), "NSIDE", hdr.get("NSIDE"),
            "TUNIT1", hdr.get("TUNIT1"))
        raw = {c: np.array(hd[1].data[c]).ravel() for c in cols}
        int_beam = np.array(hd[2].data["INT_BEAM"]).astype(np.float64) if len(hd) > 2 else None
    with fits.open(MINT, memmap=True) as hd:
        mint_cols = [c.name for c in hd[1].columns]
        mint_ord = hd[1].header.get("ORDERING")
        mint = np.array(hd[1].data[mint_cols[0]]).ravel()
    with fits.open(MPOL, memmap=True) as hd:
        mpol_cols = [c.name for c in hd[1].columns]
        mpol_ord = hd[1].header.get("ORDERING")
        mpol = np.array(hd[1].data[mpol_cols[0]]).ravel()
    rec("mask columns", mint_cols, mint_ord, mpol_cols, mpol_ord)
    chk("npix_12_nside2", NPIX == 12 * 2048 ** 2 == hp.nside2npix(NSIDE)
        and all(len(v) == NPIX for v in raw.values()) and len(mint) == len(mpol) == NPIX
        and hdr.get("NSIDE") == NSIDE,
        "every column of the map and both masks has 12*2048^2 = %d pixels" % NPIX)
    chk("ordering_nested", hdr.get("ORDERING") == "NESTED" and mint_ord == "NESTED" and mpol_ord == "NESTED",
        "map and masks are NESTED, as Planck ships them")
    nest = {c: raw[c] for c in ("I_STOKES", "Q_STOKES", "U_STOKES")}
    unseen = {c: int(np.sum(v == hp.UNSEEN)) for c, v in nest.items()}
    chk("map_finite", all(np.all(np.isfinite(v)) for v in nest.values()),
        "I,Q,U all finite; UNSEEN pixels %s" % unseen)
    RES["mask_fsky"] = {"int": float(mint.mean()), "pol": float(mpol.mean()),
                        "int_binary": bool(np.all((mint == 0) | (mint == 1))),
                        "pol_binary": bool(np.all((mpol == 0) | (mpol == 1)))}
    rec("common masks fsky: Int %.6f  Pol %.6f  binary %s %s" % (
        mint.mean(), mpol.mean(), RES["mask_fsky"]["int_binary"], RES["mask_fsky"]["pol_binary"]))
    RES["inpaint"] = {}
    for x, mk in (("I", "TMASKINP"), ("Q", "PMASKINP"), ("U", "PMASKINP")):
        if x + "_STOKES_INP" in raw and mk in raw:
            tm = raw[mk]
            d = raw[x + "_STOKES_INP"] != raw[x + "_STOKES"]
            RES["inpaint"][x] = {"mask": mk, "mask_fsky": float(tm.mean()), "differ": int(d.sum()),
                                 "differ_where_mask1": int(np.sum(d & (tm == 1))),
                                 "differ_where_mask0": int(np.sum(d & (tm == 0))),
                                 "max_abs_diff_where_mask1_K": float(np.max(np.abs(raw[x + "_STOKES_INP"] - raw[x + "_STOKES"])[tm == 1])),
                                 "rms_K": float(np.std(raw[x + "_STOKES"])),
                                 "mask0": int(np.sum(tm == 0))}
    rec("inpainting:", RES["inpaint"])
    RES["confidence_mask_fsky"] = {k: float(raw[k].mean()) for k in ("TMASK", "PMASK") if k in raw}
    rec("SMICA confidence masks fsky:", RES["confidence_mask_fsky"])

with timer("reorder"):
    ring = {c: hp.reorder(v, n2r=True) for c, v in nest.items()}
    idx = hp.nest2ring(NSIDE, np.arange(NPIX))
    perm_ok = np.array_equal(np.sort(idx), np.arange(NPIX))
    manual = np.empty_like(nest["I_STOKES"])
    manual[idx] = nest["I_STOKES"]
    chk("nest2ring_is_permutation", perm_ok, "nest2ring over all %d pixels hits each ring index once" % NPIX)
    chk("reorder_matches_index", np.array_equal(manual, ring["I_STOKES"]),
        "reorder(n2r) == scatter by nest2ring, bit-exact")
    back = all(np.array_equal(hp.reorder(ring[c], r2n=True), nest[c]) for c in nest)
    chk("reorder_roundtrip", back, "I,Q,U: nest -> ring -> nest bit-identical over all pixels")
    rmap = hp.read_map(SMICA, field=(0, 1, 2), nest=False)
    chk("read_map_ring", all(np.array_equal(rmap[i], ring[c]) for i, c in enumerate(nest)),
        "hp.read_map(nest=False) == reorder of the raw NESTED columns, bit-exact")
    RES["hash_ring_I"] = h(ring["I_STOKES"])
    del rmap, manual, idx

mint_r = hp.reorder(mint.astype(np.float64), n2r=True)
mpol_r = hp.reorder(mpol.astype(np.float64), n2r=True)

# ---------------------------------------------------------------- 2. pixel geometry
with timer("geometry"):
    CH = 1 << 22
    worst = {"ang_nest": 0, "ang_ring": 0, "vec_nest": 0, "vec_ring": 0, "r2n": 0,
             "ahp_nest": 0, "ahp_ring": 0, "ahp_cross_nest": 0, "ahp_cross_ring": 0}
    sep_max = {"nest": 0.0, "ring": 0.0}
    area_sum = math.fsum([hp.nside2pixarea(NSIDE)] * NPIX)
    for s in range(0, NPIX, CH):
        p = np.arange(s, min(s + CH, NPIX))
        worst["r2n"] += int(np.sum(hp.ring2nest(NSIDE, hp.nest2ring(NSIDE, p)) != p))
        for o, ne in (("nest", True), ("ring", False)):
            th, ph = hp.pix2ang(NSIDE, p, nest=ne)
            worst["ang_" + o] += int(np.sum(hp.ang2pix(NSIDE, th, ph, nest=ne) != p))
            v = hp.pix2vec(NSIDE, p, nest=ne)
            worst["vec_" + o] += int(np.sum(hp.vec2pix(NSIDE, v[0], v[1], v[2], nest=ne) != p))
            lon, lat = ahc.healpix_to_lonlat(p, NSIDE, order="nested" if ne else "ring")
            lon = np.asarray(lon.to_value("rad")); lat = np.asarray(lat.to_value("rad"))
            # separation between the two codes' pixel centres (haversine, stable at tiny angles)
            dlat = lat - (np.pi / 2 - th)
            dlon = np.angle(np.exp(1j * (lon - ph)))
            a = np.sin(dlat / 2) ** 2 + np.cos(lat) * np.cos(np.pi / 2 - th) * np.sin(dlon / 2) ** 2
            sep_max[o] = max(sep_max[o], float(np.max(2 * np.arcsin(np.sqrt(a)))))
            worst["ahp_" + o] += int(np.sum(ahc.lonlat_to_healpix(lon * astropy.units.rad, lat * astropy.units.rad,
                                                                  NSIDE, order="nested" if ne else "ring") != p))
            worst["ahp_cross_" + o] += int(np.sum(hp.ang2pix(NSIDE, np.pi / 2 - lat, lon, nest=ne) != p))
        del th, ph, v, lon, lat, a
    chk("area_sum_4pi", abs(area_sum - 4 * np.pi) <= 4 * np.pi * EPS,
        "fsum of %d pixel areas = %r, 4pi = %r" % (NPIX, area_sum, 4 * np.pi))
    chk("ring_nest_bijection", worst["r2n"] == 0, "nest->ring->nest identity, all %d pixels" % NPIX)
    for o in ("nest", "ring"):
        chk("pix_ang_pix_" + o, worst["ang_" + o] == 0, "pix2ang->ang2pix identity, all pixels (%s)" % o)
        chk("pix_vec_pix_" + o, worst["vec_" + o] == 0, "pix2vec->vec2pix identity, all pixels (%s)" % o)
        chk("astropy_healpix_centres_" + o, le(sep_max[o], 1e-12),
            "astropy-healpix vs healpy pixel centres, max separation %.2e rad (bound 1e-12; "
            "pixel size %.1e)" % (sep_max[o], hp.nside2resol(NSIDE)))
        chk("astropy_healpix_roundtrip_" + o, worst["ahp_" + o] == 0 and worst["ahp_cross_" + o] == 0,
            "astropy-healpix(healpy centres) and healpy(astropy-healpix centres) both return the pixel, "
            "all pixels (%s): %d / %d misses" % (o, worst["ahp_" + o], worst["ahp_cross_" + o]))
    RES["centre_sep_max_rad"] = sep_max

    # random positions: two codes must agree, and any disagreement must be adjacent pixels
    rng = np.random.default_rng(20180717)
    n = 20_000_000
    z = rng.uniform(-1, 1, n); phi = rng.uniform(0, 2 * np.pi, n)
    th = np.arccos(z)
    a = hp.ang2pix(NSIDE, th, phi, nest=True)
    b = ahc.lonlat_to_healpix(phi * astropy.units.rad, (np.pi / 2 - th) * astropy.units.rad, NSIDE, order="nested")
    dis = np.nonzero(a != b)[0]
    adj = True
    if len(dis):
        nb = hp.get_all_neighbours(NSIDE, a[dis], nest=True)
        adj = bool(np.all(np.any(nb == b[dis][None, :], axis=0)))
    chk("ang2pix_random_vs_astropy_healpix", adj,
        "%d random sky positions: %d disagreements, all adjacent pixels: %s" % (n, len(dis), adj))
    RES["random_ang2pix_disagree"] = int(len(dis))
    del z, phi, th, a, b

    # query_disc against brute force over every pixel centre
    vx, vy, vz = hp.pix2vec(NSIDE, np.arange(NPIX))
    bad, edge, tot = 0, 0, 0
    for k in range(24):
        c = rng.normal(size=3); c /= np.linalg.norm(c)
        r = np.radians(rng.uniform(0.5, 20.0))
        got = np.zeros(NPIX, bool); got[hp.query_disc(NSIDE, c, r, inclusive=False)] = True
        d = vx * c[0] + vy * c[1] + vz * c[2]
        near = np.abs(d - np.cos(r)) <= 1e-13
        want = d > np.cos(r)
        bad += int(np.sum((got != want) & ~near)); edge += int(near.sum()); tot += int(want.sum())
    chk("query_disc_vs_bruteforce", bad == 0,
        "24 discs 0.5-20 deg: %d pixels inside, %d disagreements (%d centres within 1e-13 of the "
        "edge excluded)" % (tot, bad, edge))
    del vx, vy, vz, d, got, want, near

# ---------------------------------------------------------------- 3. ud_grade
with timer("ud_grade"):
    m = nest["I_STOKES"].astype(np.float64)
    S = math.fsum(m)
    A = float(np.sum(np.abs(m)))
    rec("I map: sum %r  sum|.| %r  mean %.6e K" % (S, A, S / NPIX))
    cons = []
    for ns in [NSIDE // 2 >> k for k in range(NSIDE.bit_length() - 1)]:
        d = hp.ud_grade(m, ns, order_in="NESTED", order_out="NESTED")
        r = (NSIDE // ns) ** 2
        err = abs(math.fsum(d) * r - S)
        cons.append((ns, err / A))
        ok = le(err, 4 * NPIX * EPS * A)
        if not ok:
            chk("ud_grade_conserves_%d" % ns, False, "err %r" % err)
        if ns == NSIDE // 8:
            d_ring = hp.ud_grade(hp.reorder(m, n2r=True), ns)
            chk("ud_grade_ring_equals_nest", np.array_equal(hp.reorder(d, n2r=True), d_ring),
                "ud_grade in RING == reorder(ud_grade in NESTED), bit-exact, 2048 -> 256")
            blk = m.reshape(-1, r).mean(axis=1)
            RES["ud_vs_blockmean_256_maxrel"] = float(np.max(np.abs(blk - d)) / np.max(np.abs(d)))
    chk("ud_grade_conserves_sum", all(le(e, 4 * NPIX * EPS) for _, e in cons),
        "2048 -> 1024 ... 1: sum(out)*ratio == fsum(in), worst rel %.2e (bound 4*npix*eps*sum|m|)"
        % max(e for _, e in cons))
    up = hp.ud_grade(m, 2 * NSIDE, order_in="NESTED", order_out="NESTED")
    rep = np.array_equal(up, np.repeat(m, 4))
    dn = hp.ud_grade(up, NSIDE, order_in="NESTED", order_out="NESTED")
    chk("ud_grade_up_down_exact", rep and np.array_equal(dn, m),
        "2048 -> 4096 (201,326,592 pixels) repeats each parent; -> 2048 returns the float32-valued map bit-exact")
    RES["hash_ud_64"] = h(hp.ud_grade(m, 64, order_in="NESTED", order_out="NESTED"))
    del up, dn, m, d

# ---------------------------------------------------------------- 4. harmonics
iqu = [ring[c].astype(np.float64) for c in ("I_STOKES", "Q_STOKES", "U_STOKES")]
del ring, raw, nest

with timer("anafast_full"):   # anafast is exactly map2alm + alm2cl; the a_lm are kept for what follows
    alm = hp.map2alm(iqu, lmax=LMAX, iter=3, pol=True)
    cl_full = hp.alm2cl(alm)   # TT EE BB TE EB TB
    RES["alm_size"] = int(len(alm[0]))
    rec("full-sky IQU to lmax %d, iter 3: %d a_lm per field" % (LMAX, len(alm[0])))

with timer("monopole"):
    a0 = hp.map2alm(iqu[0], lmax=2, iter=0)[0].real
    mean = math.fsum(iqu[0]) / NPIX
    err = abs(a0 - math.sqrt(4 * np.pi) * mean)
    bound = 64 * EPS * math.sqrt(4 * np.pi) * float(np.mean(np.abs(iqu[0])))
    chk("monopole_is_mean", le(err, bound),
        "map2alm(iter=0) a00 %.6e == sqrt(4pi)*mean %.6e: |diff| %.2e (bound 64 eps sqrt(4pi) mean|m| = %.2e)"
        % (a0, math.sqrt(4 * np.pi) * mean, err, bound))

with timer("anafast_masked"):
    mT = iqu[0] * mint_r
    mQ = iqu[1] * mpol_r; mU = iqu[2] * mpol_r
    cl_mask = hp.anafast([mT, mQ, mU], lmax=LMAX, iter=3, pol=True)
    fskyT = float(np.mean(mint_r ** 2))
    del mT, mQ, mU

with timer("published_tt"):
    pub = np.loadtxt(os.path.join(IN, "COM_PowerSpect_CMB-TT-full_R3.01.txt"))
    ell = np.arange(LMAX + 1)
    bl = hp.gauss_beam(np.radians(5 / 60), lmax=LMAX)
    if int_beam is not None:   # SMICA's own effective beam transfer function (HDU 2), l <= 4096
        n = min(len(int_beam), LMAX + 1)
        bl = bl.copy(); bl[:n] = int_beam[:n]
    try:
        pw = hp.pixwin(NSIDE, lmax=LMAX)
        rec("pixel window from healpy's own data file")
    except Exception as e:
        pw = np.ones(LMAX + 1)
        rec("pixwin unavailable (%r): no pixel-window correction" % e)
    if int_beam is not None:
        g = hp.gauss_beam(np.radians(5 / 60), lmax=2000)
        r1 = float(np.max(np.abs(int_beam[2:2001] / g[2:] - 1)))
        r2 = float(np.max(np.abs(int_beam[2:2001] / (g[2:] * pw[2:2001]) - 1)))
        rec("SMICA INT_BEAM, l 2-2000: max |B/gauss5' - 1| %.2e, max |B/(gauss5' * pixwin) - 1| %.2e; INT_BEAM[0:2] %s"
            % (r1, r2, int_beam[:2]))
        RES["int_beam"] = {"vs_gauss": r1, "vs_gauss_pixwin": r2}
        if r2 < r1:   # the effective beam already carries the pixel window
            pw = np.ones(LMAX + 1)
            rec("INT_BEAM includes the pixel window: not applied twice")
    dl_smica = 1e12 * ell * (ell + 1) / (2 * np.pi) * cl_mask[0] / fskyT / (bl * pw) ** 2
    lp, dp = pub[:, 0].astype(int), pub[:, 1]

    def peak(l, d, lo=170, hi=270):
        """first acoustic peak: vertex of a quadratic fitted to D_l over lo..hi (the same fit for every spectrum)"""
        s = (l >= lo) & (l <= hi)
        c = np.polyfit(l[s].astype(float), d[s], 2)
        return float(-c[1] / (2 * c[0]))

    thf = np.loadtxt(os.path.join(IN, "COM_PowerSpect_CMB-base-plikHM-TTTEEE-lowl-lowE-lensing-minimum-theory_R3.01.txt"))
    pk_s, pk_p, pk_t = peak(ell, dl_smica), peak(lp, dp), peak(thf[:, 0].astype(int), thf[:, 1])
    chk("first_peak_vs_planck", abs(pk_s - pk_p) <= 5,
        "first acoustic peak (quadratic vertex, l 170-270): SMICA masked pseudo-C_l %.2f, Planck PR3 published TT %.2f "
        "(|diff| <= 5); best-fit theory %.2f, theory argmax %d" % (pk_s, pk_p, pk_t, int(thf[np.argmax(thf[:400, 1]), 0])))
    ratios = []
    for e in range(30, 1500, 50):
        s = (ell >= e) & (ell < e + 50); q = (lp >= e) & (lp < e + 50)
        ratios.append((e, float(np.mean(dl_smica[s]) / np.mean(dp[q]))))
    RES["smica_over_published_tt"] = ratios
    rec("SMICA masked pseudo-D_l / published TT, 50-l bins 30-1480: min %.4f max %.4f median %.4f" % (
        min(r for _, r in ratios), max(r for _, r in ratios), float(np.median([r for _, r in ratios]))))
    RES["first_peak"] = {"smica": pk_s, "published": pk_p, "theory": pk_t}

with timer("synthesis_vs_scipy"):
    L = min(128, NSIDE)
    aT = hp.resize_alm(alm[0], LMAX, LMAX, L, L)
    mp = hp.alm2map(aT, NSIDE, lmax=L)
    pix = np.random.default_rng(7).choice(NPIX, 4000, replace=False)
    th, ph = hp.pix2ang(NSIDE, pix)
    direct = np.zeros(len(pix))
    for mm in range(L + 1):
        ls = np.arange(mm, L + 1)
        y = scipy.special.sph_harm_y(ls[:, None], mm, th[None, :], ph[None, :])
        a = aT[hp.Alm.getidx(L, ls, mm)]
        direct += (1 if mm == 0 else 2) * np.real(np.sum(a[:, None] * y, axis=0))
    err = float(np.max(np.abs(direct - mp[pix])))
    scale = float(np.max(np.abs(mp)))
    chk("alm2map_vs_scipy_Ylm", le(err, 1e-11 * scale),
        "alm2map (l<=128, real SMICA a_lm) vs direct sum of scipy sph_harm_y at 4000 pixels: "
        "max |diff| %.2e = %.1e of max|map| (bound 1e-11)" % (err, err / scale))
    RES["synthesis_rel_err"] = err / scale

with timer("roundtrip_convergence"):
    conv = {}
    for L in (NSIDE, 2 * NSIDE):
        a = hp.resize_alm(alm[0], LMAX, LMAX, L, L)
        mp = hp.alm2map(a, NSIDE, lmax=L)
        nrm = np.sqrt(np.sum(np.abs(a) ** 2))
        conv[L] = [float(np.sqrt(np.sum(np.abs(hp.map2alm(mp, lmax=L, iter=k) - a) ** 2)) / nrm)
                   for k in range(4)]
        rec("map2alm round trip, band limit %d: rel residual by iteration %s" % (
            L, " ".join("%.3e" % x for x in conv[L])))
    RES["roundtrip_residual"] = {str(k): v for k, v in conv.items()}
    # Jacobi iteration on a fixed grid is a linear contraction: each step multiplies the residual by the
    # same factor, set by band limit / Nside and not by the data. Assert the rate, not a residual value.
    rates = {L: [v[k] / v[k + 1] for k in range(3)] for L, v in conv.items()}
    RES["roundtrip_rate"] = {str(L): r for L, r in rates.items()}
    chk("roundtrip_geometric_rate", all(min(r) > 2 and max(r) / min(r) < 1.1 for r in rates.values()),
        "map2alm(alm2map(a)) residual shrinks by a constant factor per iteration: band limit %d x%s, %d x%s "
        "(factor > 2, spread < 10%%)" % (NSIDE, "/".join("%.2f" % x for x in rates[NSIDE]),
                                        2 * NSIDE, "/".join("%.2f" % x for x in rates[2 * NSIDE])))

with timer("rotation"):
    L = NSIDE
    a = hp.resize_alm(alm[0], LMAX, LMAX, L, L)
    rot = hp.Rotator(coord=["G", "C"])
    ar = rot.rotate_alm(a, lmax=L)
    back = hp.Rotator(coord=["C", "G"]).rotate_alm(ar, lmax=L)
    c0, c1 = hp.alm2cl(a)[2:], hp.alm2cl(ar)[2:]
    cl_err = float(np.max(np.abs(c1 / c0 - 1)))
    rt_err = float(np.sqrt(np.sum(np.abs(back - a) ** 2) / np.sum(np.abs(a) ** 2)))
    chk("rotation_preserves_cl", le(cl_err, 1e-9),
        "Galactic -> equatorial rotate_alm (l<=2048): max_l |C_l'/C_l - 1| = %.2e (bound 1e-9)" % cl_err)
    chk("rotation_roundtrip", le(rt_err, 1e-9),
        "G -> C -> G returns the a_lm: rel residual %.2e (bound 1e-9)" % rt_err)
    RES["rotation"] = {"cl_err": cl_err, "rt_err": rt_err}

with timer("smoothing"):
    fw = np.radians(10 / 60)
    sig = fw / math.sqrt(8 * math.log(2))
    l = np.arange(LMAX + 1)
    b_ind = np.exp(-0.5 * l * (l + 1) * sig ** 2)
    a_s = hp.smoothalm(alm[0], fwhm=fw, inplace=False)
    c_s = hp.alm2cl(a_s)
    ok = b_ind ** 2 > 1e-200
    err = float(np.max(np.abs(c_s[ok] / (cl_full[0][ok] * b_ind[ok] ** 2) - 1)))
    chk("smoothing_is_beam_squared", le(err, 1e-12),
        "smoothalm(10') C_l == C_l * exp(-l(l+1)sigma^2), independently computed: max rel %.2e "
        "over %d multipoles (bound 1e-12)" % (err, int(ok.sum())))

with timer("synfast"):
    th_ = np.loadtxt(os.path.join(IN, "COM_PowerSpect_CMB-base-plikHM-TTTEEE-lowl-lowE-lensing-minimum-theory_R3.01.txt"))
    LS = min(2500, 3 * NSIDE - 1)
    lt = th_[:, 0].astype(int)
    cls = np.zeros((4, LS + 1))   # TT EE BB TE, C_l in K^2
    for i, col in enumerate((1, 3, 4, 2)):
        s = (lt >= 2) & (lt <= LS)
        cls[i, lt[s]] = th_[s, col] * 2 * np.pi / (lt[s] * (lt[s] + 1)) * 1e-12
    np.random.seed(20180717)
    sa = hp.synalm(list(cls), lmax=LS, new=True)
    smap = hp.alm2map(sa, NSIDE, lmax=LS, pol=True)
    chat = hp.anafast(smap, lmax=LS, iter=3, pol=True)
    cin = hp.alm2cl(sa)
    l = np.arange(2, LS + 1)
    z = {}
    for name, i in (("TT", 0), ("EE", 1)):
        x = (2 * l + 1) * cin[i][2:] / cls[i][2:]
        z[name] = float(np.sum(x - (2 * l + 1)) / np.sqrt(np.sum(2 * (2 * l + 1))))
    chk("synalm_cosmic_variance", all(abs(v) < 5 for v in z.values()),
        "seeded synalm from Planck's best fit, l 2-2500: sum((2l+1)C^/C - (2l+1)) in sigma, TT %.2f EE %.2f (|z|<5)"
        % (z["TT"], z["EE"]))
    rt = float(max(np.max(np.abs(chat[i][2:] / cin[i][2:] - 1)) for i in (0, 1)))
    chk("synfast_map_recovers_cl", le(rt, 1e-4),
        "anafast(alm2map(a)) vs alm2cl(a), TT and EE, l 2-2500 at Nside 2048: max rel %.2e (bound 1e-4)" % rt)
    RES["synfast"] = {"z": z, "map_rt": rt, "hash_alm": h(sa[0])}

np.savez_compressed(os.path.join(OUT, "spectra.npz"), cl_full=np.array(cl_full),
                    cl_mask=np.array(cl_mask), synfast_cl=np.array(chat), synalm_cl=np.array(cin))
RES["timings_s"] = T
RES["wall_s"] = round(time.time() - T0, 1)
RES["ru_maxrss_gib"] = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 2 ** 20
RES["failed"] = FAIL
rec("timings", json.dumps(T))
rec("wall %.1f s  ru_maxrss %.2f GiB" % (RES["wall_s"], RES["ru_maxrss_gib"]))
json.dump(RES, open(os.path.join(OUT, "results.json"), "w"), indent=1, default=str)
LOG.append("HEALPY REAL OK" if not FAIL else "HEALPY REAL FAILED " + " ".join(FAIL))
open(os.path.join(OUT, "check.txt"), "w").write("\n".join(LOG) + "\n")
print(LOG[-1], flush=True)
sys.exit(1 if FAIL else 0)
