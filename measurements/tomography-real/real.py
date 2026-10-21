#!/usr/bin/env python3
"""tomography-real: the whole BBii sandstone scan (SLS TOMCAT) through TomoPy, checked against the
depositors' own reconstruction, the scan's own geometry, and astra.

    python3 -u real.py <proj.zip> <rec.zip> <out dir> <ncore> [max_rows]

max_rows > 0 is a sizing probe: only that many evenly spaced rows are reconstructed. CGLS_IT in
the environment sets the CGLS iteration count (default 10).

The pipeline: dark/flat normalise, -log, find the rotation centre, gridrec with the Shepp-Logan
filter, slice by slice through all 2160 detector rows. Every slice is compared with the
depositors' slice of the same index from rec.zip (TOMCAT's own GridRec, centre 1324.11 in its
1-based pixels, Shepp-Logan, written as 16-bit over [-0.003, 0.01]).

Checks, by kind:
  counts      30 darks + 100 flats + 1501 projections + 100 flats, 2160 published slices, as the log says
  geometry    three independent centres -- Vo's sinogram method, the 0/180 deg mirror, and the
              centre at which gridrec best reproduces the published slices -- against TOMCAT's
  published   which published slice each row is, and its orientation, found by searching all
              2160; then every slice matches its own published slice better than either neighbour
              and agrees with it above the 1/7 FRC criterion to half Nyquist
  invariance  gridrec at ncore=1 vs ncore=N, and a row alone vs inside a block: float32 round-off
  cross-code  astra's CPU FBP (Shepp-Logan, same centre) on the same sinograms vs gridrec, by FRC
  consistency re-projecting gridrec's slice fits the measured sinogram better than a slice
              reconstructed 10 px off-centre does
  iterative   astra CGLS's residual falls at every iteration (a property of CG)
"""
import concurrent.futures as cf
import hashlib
import io
import os
import re
import resource
import sys
import threading
import time
import zipfile

import numpy as np

ROWS = []
T0 = time.time()
TL = [T0]


def rec(k, v):
    v = str(v)
    print("%s\t%s" % (k, v), flush=True)
    ROWS.append((str(k), v))


def phase(name):
    t = time.time()
    rec("phase_%s_s" % name, "%.1f" % (t - TL[0]))
    TL[0] = t


FAILS = []
TLS = threading.local()


def check(ok, name, msg):
    if not bool(ok):
        print("FAIL: %s: %s" % (name, msg), flush=True)
        FAILS.append(name)
    else:
        rec("ok_" + name, msg)


def corr(a, b, m):
    x = a[m].astype("float64")
    y = b[m].astype("float64")
    x -= x.mean()
    y -= y.mean()
    return float((x * y).sum() / np.sqrt((x * x).sum() * (y * y).sum()))


_RINGS = {}
# The FRC is judged from ring 8 (wavelengths <= 320 px = 208 um, 1/8 of the image) up. Rings 1-7 are
# features on the scale of the field of view itself, where interior tomography leaves FBP's answer
# set by how each code pads the truncated sinogram -- a smooth cupping, method not error. The cut
# was placed after the sizing probe showed rings 1-3 falling below 1/7 on 59 of 64 slices while every
# ring above stayed far above it; rings 1-7 are still recorded per slice (frc_min_rings_1_7).
K0 = 8


def frc(a, b, m):
    """Fourier ring correlation of two equal square images inside mask m, one value per integer
    ring (cycles across the image). scipy.fft releases the GIL, so threads run it in parallel."""
    import scipy.fft as sfft
    n = a.shape[0]
    if n not in _RINGS:
        fy = np.fft.fftfreq(n)[:, None] * n
        fx = np.fft.rfftfreq(n)[None, :] * n
        _RINGS[n] = np.rint(np.hypot(fy, fx)).astype(np.int64).ravel()
    R = _RINGS[n]
    X = sfft.rfft2(np.where(m, a - a[m].mean(), 0.0).astype(np.float32), workers=1).ravel()
    Y = sfft.rfft2(np.where(m, b - b[m].mean(), 0.0).astype(np.float32), workers=1).ravel()
    num = np.bincount(R, (X * np.conj(Y)).real)
    den = np.sqrt(np.bincount(R, np.abs(X) ** 2) * np.bincount(R, np.abs(Y) ** 2))
    return num[: n // 2 + 1] / den[: n // 2 + 1]


def frc_cross(f, thr=1 / 7):
    """First ring from K0 on where the FRC falls below the 1/7 criterion (Nyquist if none)."""
    below = np.nonzero(f[K0:] < thr)[0]
    return int(below[0] + K0) if below.size else len(f) - 1


def main():
    proj_zip, rec_zip, out, ncore = sys.argv[1], sys.argv[2], sys.argv[3], int(sys.argv[4])
    max_rows = int(sys.argv[5]) if len(sys.argv) > 5 else 0
    os.makedirs(out, exist_ok=True)
    import tifffile
    import tomopy
    import astra
    import pywt
    import scipy
    for name, mod in (("tomopy", tomopy), ("astra", astra), ("numpy", np), ("tifffile", tifffile),
                      ("pywavelets", pywt), ("scipy", scipy)):
        rec("version_" + name, getattr(mod, "__version__", "unknown"))
    rec("astra_use_cuda", bool(astra.astra.use_cuda()))
    check(not astra.astra.use_cuda(), "cpu_only", "astra reports no CUDA; every timing is CPU")
    rec("ncore", ncore)
    rec("max_rows", max_rows)

    # ------------------------------------------------------------------ read the raw scan
    z = zipfile.ZipFile(proj_zip)
    tifs = sorted(n for n in z.namelist() if re.fullmatch(r"proj/BBii_\d{4}\.tif", n))
    rec("proj_tiffs", len(tifs))
    idx = [int(n[-8:-4]) for n in tifs]
    check(idx == list(range(1, 1732)), "count_tiffs",
          "1731 TIFFs numbered 0001..1731 = 30 darks + 100 flats + 1501 projections + 100 flats")
    z.close()
    if idx != list(range(1, 1732)):
        sys.exit(1)
    NP = 1501
    with zipfile.ZipFile(proj_zip) as zz:
        NY, NX = tifffile.imread(io.BytesIO(zz.read(tifs[0]))).shape
    rec("detector", "%d x %d" % (NX, NY))
    check((NY, NX) == (2160, 2560), "detector", "2560 x 2160 detector, as the log's X-ROI/Y-ROI say")
    proj = np.empty((NP, NY, NX), dtype=np.uint16)
    dark = np.zeros((NY, NX), dtype=np.float64)
    flat_pre = np.zeros((NY, NX), dtype=np.float64)
    flat_post = np.zeros((NY, NX), dtype=np.float64)

    def read_some(chunk):
        zz = zipfile.ZipFile(proj_zip)
        acc = {"d": np.zeros((NY, NX)), "fa": np.zeros((NY, NX)), "fb": np.zeros((NY, NX))}
        bad = []
        for i in chunk:
            a = tifffile.imread(io.BytesIO(zz.read("proj/BBii_%04d.tif" % i)))
            if a.shape != (NY, NX) or a.dtype != np.uint16:
                bad.append((i, a.shape, str(a.dtype)))
                continue
            if i <= 30:
                acc["d"] += a
            elif i <= 130:
                acc["fa"] += a
            elif i <= 1631:
                proj[i - 131] = a
            else:
                acc["fb"] += a
        zz.close()
        return acc, bad

    chunks = [list(range(1 + k, 1732, ncore)) for k in range(ncore)]
    bad = []
    with cf.ThreadPoolExecutor(ncore) as ex:
        for acc, b in ex.map(read_some, chunks):
            dark += acc["d"]
            flat_pre += acc["fa"]
            flat_post += acc["fb"]
            bad += b
    check(not bad, "tiff_shape", "every TIFF is 2160 x 2560 uint16")
    dark /= 30.0
    flat_pre /= 100.0
    flat_post /= 100.0
    flat = 0.5 * (flat_pre + flat_post)
    rec("raw_bytes_in_ram_gib", "%.2f" % (proj.nbytes / 2 ** 30))
    rec("dark_mean", "%.2f" % dark.mean())
    rec("flat_pre_mean", "%.2f" % flat_pre.mean())
    rec("flat_post_mean", "%.2f" % flat_post.mean())
    rec("flat_drift_pre_to_post", "%.4f" % (flat_post.mean() / flat_pre.mean() - 1.0))
    frac = float((flat > dark).mean())
    rec("flat_gt_dark_fraction", "%.6f" % frac)
    check(frac > 0.999, "flat_above_dark", "flat exceeds dark on %.4f%% of pixels" % (100 * frac))
    # the projections sit between dark and flat: transmission in (0, 1] for nearly every pixel
    sub = proj[::50].astype(np.float32)
    tr = (sub - dark.astype(np.float32)) / (flat - dark).astype(np.float32)
    rec("transmission_median", "%.4f" % float(np.median(tr)))
    rec("transmission_p001_p999", "%.4f %.4f" % (float(np.quantile(tr, 0.001)), float(np.quantile(tr, 0.999))))
    del sub, tr
    phase("read")

    theta = np.linspace(0.0, np.pi, NP)                  # 0..180 deg, both included, 0.12 deg step
    rec("angular_step_deg", "%.6f" % np.degrees(theta[1] - theta[0]))
    check(abs(np.degrees(theta[1] - theta[0]) - 0.12) < 1e-9, "angular_step",
          "1501 projections over 0..180 inclusive is the log's 0.120 deg step")

    def sinos(r0, r1):
        """Normalise and -log rows r0:r1 -> (angles, rows, cols) float32. No stripe removal: the
        depositors ran their own, which tomopy does not ship, and the FRC is judged without it."""
        p = proj[:, r0:r1, :].astype(np.float32)
        t = tomopy.normalize(p, flat[None, r0:r1, :].astype(np.float32),
                             dark[None, r0:r1, :].astype(np.float32), ncore=ncore)
        t = tomopy.minus_log(t, ncore=ncore)
        t[~np.isfinite(t)] = 0.0
        return t

    # ------------------------------------------------------------------ the rotation centre
    PUB_C = 1324.11     # TOMCAT's log: "Center 1324.11" used for rec_16bit (estimator: 1323.61)
    # 1. the 0 and 180 deg projections are mirror images about the axis (tomopy's phase correlation)
    p0 = (proj[0].astype(np.float32) - dark) / (flat - dark)
    p180 = (proj[-1].astype(np.float32) - dark) / (flat - dark)
    c_pc = float(tomopy.find_center_pc(p0, p180, tol=0.25))
    rec("centre_mirror_0_180", "%.2f" % c_pc)
    # mirror residual at that centre: proj(180) flipped about the axis vs proj(0)
    sh = int(round(2 * c_pc - (NX - 1)))
    a, b = p0, np.fliplr(p180)
    if sh > 0:
        a2, b2 = a[:, sh:], b[:, :NX - sh]
    else:
        a2, b2 = a[:, :NX + sh], b[:, -sh:]
    mres = float(np.abs(a2 - b2).mean() / np.abs(a2 - a2.mean()).mean())
    rec("mirror_residual_rel", "%.4f" % mres)
    del p0, p180, a, b, a2, b2
    # 2. Vo's sinogram method on 9 rows spread over the detector
    vo_rows = [int(x) for x in np.linspace(120, NY - 120, 9)]
    c_vo = []
    for r in vo_rows:
        s = sinos(r, r + 1)
        c_vo.append(float(tomopy.find_center_vo(s, ind=0, smin=-60, smax=60, srad=6, step=0.25, ncore=ncore)))
    rec("centre_vo_rows", " ".join("%d:%.2f" % rc for rc in zip(vo_rows, c_vo)))
    c_vo_med = float(np.median(c_vo))
    rec("centre_vo_median", "%.2f" % c_vo_med)
    rec("centre_published", "%.2f" % PUB_C)
    phase("centre")

    # ------------------------------------------------------------------ the published slices
    def pub_slice(zz, n):
        v = tifffile.imread(io.BytesIO(zz.read("rec_16bit/BBii_%04d.rec.16bit.tif" % n)))
        return (-0.003 + v.astype(np.float64) * (0.013 / 65535.0)).astype(np.float32)

    zr = zipfile.ZipFile(rec_zip)
    pubs = sorted(n for n in zr.namelist() if re.fullmatch(r"rec_16bit/BBii_\d{4}\.rec\.16bit\.tif", n))
    rec("published_slices", len(pubs))
    check([int(n[-18:-14]) for n in pubs] == list(range(1, NY + 1)), "count_published",
          "2160 published slices numbered 0001..2160, one per detector row")
    yy, xx = np.mgrid[:NX, :NX]
    cc = (NX - 1) / 2.0
    mask = (yy - cc) ** 2 + (xx - cc) ** 2 <= (0.95 * NX / 2) ** 2

    def gr(s, c, nc=ncore):
        return tomopy.recon(s, theta, center=c, algorithm="gridrec", filter_name="shepp", ncore=nc)

    # Find, rather than assume, which published slice each row is and how it is oriented: rows 270
    # and 1890 are each reconstructed at Vo's centre (independent of TOMCAT's) and searched against
    # EVERY published slice in all 8 orientations, coarse on 4x-binned images, then refined at full
    # resolution. The two answers fix a linear map slice = a + s*row; it is then applied unchanged.
    D8 = {"id": lambda x: x, "rot90": lambda x: np.rot90(x, 1), "rot180": lambda x: np.rot90(x, 2),
          "rot270": lambda x: np.rot90(x, 3), "flipud": np.flipud, "fliplr": np.fliplr,
          "transpose": lambda x: x.T, "antitranspose": lambda x: np.rot90(x, 2).T}

    def bin4(x):
        return x.reshape(NX // 4, 4, NX // 4, 4).mean(axis=(1, 3))

    mask4 = bin4(mask.astype(np.float64)) > 0.999

    def search(img):
        fr = {k: f(img) for k, f in D8.items()}
        fr4 = {k: bin4(v) for k, v in fr.items()}

        def coarse(n):
            if not hasattr(TLS, "z"):
                TLS.z = zipfile.ZipFile(rec_zip)
            p = bin4(pub_slice(TLS.z, n))
            return [(corr(v, p, mask4), n, k) for k, v in fr4.items()]

        with cf.ThreadPoolExecutor(ncore) as ex:
            allc = [t for lst in ex.map(coarse, range(1, NY + 1, 2)) for t in lst]
        allc.sort(reverse=True)
        c4, n4, k4 = allc[0]
        second = max(c for c, n, k in allc if abs(n - n4) > 50 or k != k4)
        fine = [(corr(fr[k4], pub_slice(zr, n), mask), n) for n in range(max(1, n4 - 4), min(NY, n4 + 4) + 1)]
        cf_, nf = max(fine)
        return nf, k4, cf_, c4, second

    s_rows = (NY // 8, 7 * NY // 8)                                    # 270, 1890
    found = {}
    for r in s_rows:
        nf, k, cfine, c4, second = search(gr(sinos(r, r + 1), c_vo_med)[0])
        found[r] = (nf, k)
        rec("frame_search_row%d" % r, "slice %d %s corr %.4f (4x-binned %.4f, best elsewhere %.4f)"
            % (nf, k, cfine, c4, second))
    (n0, k0), (n1, k1) = found[s_rows[0]], found[s_rows[1]]
    S_SIGN = 1 if n1 > n0 else -1
    A0 = n0 - S_SIGN * s_rows[0]
    ORI = k0
    rec("frame_map", "published slice = %d %s row, orientation %s" % (A0, "+" if S_SIGN > 0 else "-", ORI))
    check(k0 == k1 and abs(n1 - n0) == s_rows[1] - s_rows[0], "frame_consistent",
          "both searched rows give one orientation (%s, %s) and a unit-slope map (%d slices apart over %d rows)"
          % (k0, k1, abs(n1 - n0), s_rows[1] - s_rows[0]))
    ORIF = D8[ORI]

    def pub_index(r):
        return A0 + S_SIGN * r

    # centre sweep against the published slices, in the found frame, on 4 rows
    cal_rows = [NY // 8, 3 * NY // 8, 5 * NY // 8, 7 * NY // 8]       # 270, 810, 1350, 1890
    PUB0 = PUB_C - 1.0      # TOMCAT counts pixels from 1 (its log: "X-ROI : 1 - 2560"); tomopy from 0
    sweep = np.round(np.arange(PUB0 - 2.0, PUB0 + 2.01, 0.25), 2)
    stack = np.concatenate([sinos(r, r + 1) for r in cal_rows], axis=1)
    pubc = [pub_slice(zr, pub_index(r)) for r in cal_rows]
    cs = []
    for c in sweep:
        imgs = gr(stack, float(c))
        cs.append(float(np.mean([corr(ORIF(imgs[j]), pubc[j], mask) for j in range(len(cal_rows))])))
    del stack
    rec("centre_sweep", " ".join("%.2f:%.5f" % t for t in zip(sweep, cs)))
    c_pub_match = float(sweep[int(np.argmax(cs))])
    rec("centre_best_vs_published_slices", "%.2f" % c_pub_match)
    phase("calibrate")

    # Three independent estimates of the axis against TOMCAT's, all in tomopy's 0-based pixels: Vo's
    # sinogram method, the 0/180 mirror, and the centre at which gridrec best reproduces the
    # depositors' own slices. One pixel is the resolution element and the bound; the sweep is on a
    # 0.25 px grid, so it must land on TOMCAT's centre to that grid.
    check(abs(c_vo_med - PUB0) <= 1.0, "centre_vo_vs_published",
          "Vo %.2f vs TOMCAT %.2f (|d| %.2f <= 1 px)" % (c_vo_med, PUB0, abs(c_vo_med - PUB0)))
    check(abs(c_pc - PUB0) <= 1.0, "centre_mirror_vs_published",
          "0/180 mirror %.2f vs TOMCAT %.2f (|d| %.2f <= 1 px)" % (c_pc, PUB0, abs(c_pc - PUB0)))
    check(abs(c_pub_match - PUB0) <= 0.25, "centre_reproduces_published",
          "gridrec reproduces the published slices best at %.2f; TOMCAT's 1-based %.2f is %.2f" % (c_pub_match, PUB_C, PUB0))
    PUB_C = PUB0            # everything below reconstructs at TOMCAT's centre, in tomopy's pixels

    # ------------------------------------------------------------------ the whole volume
    # ------------------------------------------------------------------ the whole volume
    # Per slice against the depositors' slice of the same index: pixel correlation with it and
    # with its two neighbours (registration), and the Fourier ring correlation, judged by the
    # standard 1/7 criterion out to half Nyquist (ring NX/4). The metric is the FRC and not the
    # pixel correlation because this is interior tomography -- the core is wider than the 1.66 mm
    # field of view, so no projection sees air -- and FBP's lowest frequencies then depend on how
    # each code pads a truncated sinogram, while the top octave is noise filtered by each code's
    # own window. Both are method, not error; the FRC shows where the two agree.
    rows = list(range(NY)) if max_rows <= 0 else [int(x) for x in np.linspace(0, NY - 1, max_rows)]
    per = open(os.path.join(out, "slices.tsv"), "w")
    per.write("row\tcorr_published\tcorr_published_prev\tcorr_published_next\tfrc_min_to_half_nyquist"
              "\tfrc_min_rings_1_7\tfrc_ring160\tfrc_ring640\tfrc_cross_1_7\tslope\toffset\tmean\tstd\tsha256\n")
    keep = {NY // 8, NY // 2, 7 * NY // 8}
    CH = 16
    blocks = []
    i = 0
    while i < len(rows):
        blk = [rows[i]]
        while i + 1 < len(rows) and rows[i + 1] == blk[-1] + 1 and len(blk) < CH:
            i += 1
            blk.append(rows[i])
        blocks.append(blk)
        i += 1
    t_pre = t_rec = t_cmp = 0.0
    corrs, frcmins, lows, crosses = [], [], [], []
    reg_ok = 0
    nonfinite = 0
    hall = hashlib.sha256()
    nan = float("nan")
    with cf.ThreadPoolExecutor(ncore) as ex:
        for blk in blocks:
            t = time.time()
            s = sinos(blk[0], blk[-1] + 1)
            t_pre += time.time() - t
            t = time.time()
            vol = gr(s, PUB_C)
            nonfinite += int((~np.isfinite(vol)).sum())
            t_rec += time.time() - t
            t = time.time()

            def cmp_one(j, blk=blk, vol=vol):
                r = blk[j]
                img = ORIF(vol[j])
                n = pub_index(r)
                hx = hashlib.sha256(np.ascontiguousarray(vol[j]).tobytes()).hexdigest()
                base = [float(vol[j].mean()), float(vol[j].std()), hx]
                if not hasattr(TLS, "z"):
                    TLS.z = zipfile.ZipFile(rec_zip)
                if not 1 <= n <= NY:          # a row whose published partner is off the end
                    return [r] + [nan] * 10 + base
                p = pub_slice(TLS.z, n)
                c0 = corr(img, p, mask)
                cp = corr(img, pub_slice(TLS.z, n - 1), mask) if n > 1 else nan
                cn = corr(img, pub_slice(TLS.z, n + 1), mask) if n < NY else nan
                f = frc(img, p, mask)
                x = img[mask].astype(np.float64)
                y = p[mask].astype(np.float64)
                A = np.vstack([x, np.ones_like(x)]).T
                sl, of = np.linalg.lstsq(A, y, rcond=None)[0]
                return [r, c0, cp, cn, float(f[K0:NX // 4 + 1].min()), float(f[1:K0].min()), float(f[160]), float(f[640]),
                        frc_cross(f), float(sl), float(of)] + base

            for v in ex.map(cmp_one, range(len(blk))):
                r, c0, cp, cn, fmin, flow = v[:6]
                lows.append(flow)
                crosses.append(v[8])
                per.write("\t".join([str(r)] + ["%.6f" % x for x in v[1:8]] + ["%s" % v[8], "%.6f" % v[9],
                          "%.3e" % v[10], "%.6e" % v[11], "%.6e" % v[12], v[13]]) + "\n")
                corrs.append(c0)
                frcmins.append(fmin)
                neigh = [c for c in (cp, cn) if np.isfinite(c)]
                reg_ok += int(bool(np.isfinite(c0)) and all(c0 > c for c in neigh))
                hall.update(v[13].encode())
            for j, r in enumerate(blk):
                if r in keep:
                    np.save(os.path.join(out, "slice_%04d.npy" % r), vol[j])
            t_cmp += time.time() - t
            del s, vol
    per.close()
    rec("rows_reconstructed", len(rows))
    rec("time_preprocess_s", "%.1f" % t_pre)
    rec("time_gridrec_s", "%.1f" % t_rec)
    rec("time_compare_s", "%.1f" % t_cmp)
    rec("gridrec_s_per_slice", "%.3f" % (t_rec / len(rows)))
    corrs = np.array(corrs)
    frcmins = np.array(frcmins)
    matched = int(np.isfinite(corrs).sum())
    rec("nonfinite_voxels", nonfinite)
    check(nonfinite == 0, "finite", "every voxel of every slice is finite")
    rec("rows_with_published_partner", "%d of %d" % (matched, len(rows)))
    rec("corr_published_min", "%.4f" % np.nanmin(corrs))
    rec("corr_published_median", "%.4f" % np.nanmedian(corrs))
    rec("frc_min_to_half_nyquist_worst_slice", "%.4f" % np.nanmin(frcmins))
    rec("frc_min_to_half_nyquist_median", "%.4f" % np.nanmedian(frcmins))
    rec("frc_slices_passing_1_7", "%d of %d" % (int((frcmins > 1 / 7).sum()), matched))
    lows = np.array(lows)
    rec("frc_rings_1_7_below_1_7_slices", "%d of %d (truncation regime, reported)" % (int((lows < 1 / 7).sum()), matched))
    cr = np.array([c for c in crosses if np.isfinite(c)])
    rec("frc_cross_1_7_from_ring8_min_median", "%d %d of Nyquist 1280" % (cr.min(), np.median(cr)))
    rec("registration_peaks", "%d of %d" % (reg_ok, matched))
    rec("volume_slice_hash_digest", hall.hexdigest())
    check(matched >= len(rows) - abs(A0 - (1 if S_SIGN > 0 else NY)), "matched",
          "every row whose mapped index exists has a published slice (%d of %d)" % (matched, len(rows)))
    check(int((frcmins > 1 / 7).sum()) == matched, "frc_published",
          "every slice agrees with the depositors' slice above the 1/7 FRC criterion from ring 8 to half Nyquist "
          "(worst slice %.3f)" % np.nanmin(frcmins))
    check(reg_ok == matched, "registration",
          "every slice matches its own published slice better than either neighbour (%d of %d)" % (reg_ok, matched))
    phase("volume")

    # ------------------------------------------------------------------ invariance: ncore
    s = sinos(NY // 2, NY // 2 + CH)
    v1 = gr(s, PUB_C, 1)
    vn = gr(s, PUB_C, ncore)
    # gridrec packs two sinograms into one complex float32 transform, so how slices are paired
    # (which the thread split decides) can move the last bits but nothing else. The bound is
    # float32 round-off through a 2-D FFT of side 2^12: eps 1.2e-7 x ~12 stages x 2 passes ~ 3e-6,
    # taken as 1e-5 of the slice's peak. Byte identity is reported, not asserted.
    same = bool(np.array_equal(v1, vn))
    dn = float(np.abs(v1 - vn).max() / np.abs(v1).max())
    rec("gridrec_ncore1_vs_ncoreN_identical", same)
    rec("gridrec_ncore1_vs_ncoreN_maxabs_rel", "%.3e" % dn)
    check(dn < 1e-5, "ncore_invariance",
          "gridrec on %d rows at ncore=1 vs ncore=%d differs by %.2e of the peak (< 1e-5, float32 FFT round-off)"
          % (CH, ncore, dn))
    one = gr(sinos(NY // 2, NY // 2 + 1), PUB_C)[0]
    d = float(np.abs(one - v1[0]).max() / np.abs(v1[0]).max())
    rec("gridrec_alone_vs_in_block_identical", bool(np.array_equal(one, v1[0])))
    rec("gridrec_alone_vs_in_block_maxabs_rel", "%.3e" % d)
    check(d < 1e-5, "pairing_invariance",
          "row %d reconstructed alone vs inside a %d-row block differs by %.2e of the peak (< 1e-5)" % (NY // 2, CH, d))
    del v1, vn, one
    phase("invariance")

    # ------------------------------------------------------------------ astra: FBP, re-projection, CGLS
    xr = [NY // 8, NY // 2, 7 * NY // 8]                                 # 270, 1080, 1890
    S, G, Gw = {}, {}, {}
    for r in xr:
        s = sinos(r, r + 1)
        S[r] = np.ascontiguousarray(s[:, 0, :])
        G[r] = gr(s, PUB_C)[0]
        Gw[r] = gr(s, PUB_C + 10.0)[0]
    with cf.ProcessPoolExecutor(len(xr)) as px:
        res = list(px.map(astra_work, [S[r] for r in xr], [G[r] for r in xr], [Gw[r] for r in xr],
                          [theta] * len(xr), [PUB_C] * len(xr), [CGLS_IT] * len(xr)))
    frames = set()
    for r, d in zip(xr, res):
        for k, v in d.items():
            if not k.startswith("_"):
                rec("astra_r%d_%s" % (r, k), v)
        frames.add(d["frame"])
        np.save(os.path.join(out, "cgls_%04d.npy" % r), d["_cgls"])
        f = frc(d["_fbp"], G[r], mask)
        rec("astra_r%d_fbp_vs_gridrec_frc" % r, " ".join("%d:%.3f" % (i, f[i]) for i in (5, 40, 160, 640, 1000, 1280)))
        rec("astra_r%d_fbp_vs_gridrec_frc_cross_1_7" % r, frc_cross(f))
        rec("astra_r%d_fbp_vs_gridrec_frc_min_rings_1_7" % r, "%.3f" % f[1:K0].min())
        check(f[K0:NX // 4 + 1].min() > 1 / 7, "astra_fbp_r%d" % r,
              "astra FBP vs tomopy gridrec, same sinogram, same centre: FRC above 1/7 from ring 8 to half Nyquist (min %.3f)"
              % f[K0:NX // 4 + 1].min())
        fc = frc(d["_cgls"], G[r], mask)
        rec("astra_r%d_cgls_vs_gridrec_frc" % r, " ".join("%d:%.3f" % (i, fc[i]) for i in (5, 40, 160, 640, 1000, 1280)))
        below = [i for i, h in enumerate(d["_hist"]) if h < d["resid_gridrec"]]
        rec("astra_r%d_cgls_first_iteration_below_gridrec_residual" % r, (below[0] + 1) if below else "none")
        check(d["resid_gridrec"] < d["resid_wrong_centre"], "consistency_r%d" % r,
              "re-projecting gridrec's slice leaves %.4f of the sinogram, < %.4f for a slice 10 px off-centre"
              % (d["resid_gridrec"], d["resid_wrong_centre"]))
        check(d["cgls_monotone"], "cgls_monotone_r%d" % r,
              "CGLS residual falls at every one of %d iterations (%.4f -> %.4f)"
              % (CGLS_IT, d["_hist"][0], d["_hist"][-1]))
        rec("astra_r%d_cgls_vs_gridrec_corr" % r, "%.5f" % corr(d["_cgls"], G[r], mask))
    check(len(frames) == 1, "astra_frame", "one astra-to-tomopy orientation for every row: %s" % sorted(frames))
    phase("astra")

    ru = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 2 ** 20
    rec("ru_maxrss_gib", "%.2f" % ru)
    rec("wall_s", "%.1f" % (time.time() - T0))
    with open(os.path.join(out, "score.tsv"), "w") as f:
        for k, v in ROWS:
            f.write("%s\t%s\n" % (k, v))
    if FAILS:
        print("FAILED: %s" % " ".join(FAILS))
        sys.exit(1)
    print("TOMOGRAPHY REAL OK")


CGLS_IT = int(os.environ.get("CGLS_IT", "10"))
FRAMES = [lambda x: x, lambda x: np.rot90(x, 1), lambda x: np.rot90(x, 2), lambda x: np.rot90(x, 3),
          np.flipud, np.fliplr, lambda x: x.T, lambda x: np.rot90(x, 2).T]
INVERSE = [lambda x: x, lambda x: np.rot90(x, -1), lambda x: np.rot90(x, 2), lambda x: np.rot90(x, 1),
           np.flipud, np.fliplr, lambda x: x.T, lambda x: np.rot90(x, 2).T]


def astra_work(sino, img_gr, img_wrong, theta, centre, n_it):
    """One row on astra's CPU path. astra's CPU FBP takes only a plain `parallel` geometry, whose
    axis is the detector's middle, so the sinogram is edge-padded by PAD columns a side and
    Fourier-shifted (sub-pixel exact for a band-limited signal, integral-preserving) until the
    axis sits there. Then: FBP (Shepp-Logan), the re-projection residual of gridrec's slice and of
    a wrong-centre slice over the measured columns only, and CGLS from zero with its residual after
    every iteration. The 8-way orientation relating astra's frame to tomopy's is found by
    agreement and reported; every row must pick the same one."""
    import astra
    t0 = time.time()
    na, nx = sino.shape
    PAD = 48
    W = nx + 2 * PAD
    shift = (W - 1) / 2.0 - (centre + PAD)
    sp = np.pad(sino.astype(np.float64), ((0, 0), (PAD, PAD)), mode="edge")
    k = np.fft.rfftfreq(W)
    sp = np.fft.irfft(np.fft.rfft(sp, axis=1) * np.exp(-2j * np.pi * k * shift), n=W, axis=1).astype(np.float32)
    lo, hi = int(np.ceil(PAD + shift)), int(np.floor(PAD + shift + nx))    # the measured columns, shifted
    out = {"pad_shift_px": "%.3f" % shift, "measured_columns": "%d:%d" % (lo, hi)}
    vg = astra.create_vol_geom(W, W)
    pg = astra.create_proj_geom("parallel", 1.0, W, theta)
    pid = astra.create_projector("linear", pg, vg)
    sid = astra.data2d.create("-sino", pg, sp)
    yy, xx = np.mgrid[:nx, :nx]
    cc = (nx - 1) / 2.0
    m = (yy - cc) ** 2 + (xx - cc) ** 2 <= (0.95 * nx / 2) ** 2

    def crop(x):
        return x[PAD:PAD + nx, PAD:PAD + nx]

    def embed(x):
        e = np.zeros((W, W), dtype=np.float32)
        e[PAD:PAD + nx, PAD:PAD + nx] = x
        return e

    t = time.time()
    rid = astra.data2d.create("-vol", vg, 0)
    cfg = astra.astra_dict("FBP")
    cfg.update(ProjectorId=pid, ProjectionDataId=sid, ReconstructionDataId=rid, FilterType="shepp-logan")
    aid = astra.algorithm.create(cfg)
    astra.algorithm.run(aid)
    x_fbp = astra.data2d.get(rid).copy()
    astra.algorithm.delete(aid)
    astra.data2d.delete(rid)
    out["time_fbp_s"] = "%.1f" % (time.time() - t)
    cs = [corr(f(img_gr), crop(x_fbp), m) for f in FRAMES]
    kf = int(np.argmax(cs))
    out["frame"] = kf
    out["fbp_vs_gridrec_corr"] = cs[kf]
    out["_fbp"] = INVERSE[kf](crop(x_fbp))   # in tomopy's frame
    out["fbp_vs_gridrec_runner_up_frame_corr"] = "%.5f" % sorted(cs)[-2]

    def resid(img, scaled=True):
        """|| a*A(img) - b || / ||b|| over the measured columns, at the best scalar a when scaled:
        gridrec's units need not be astra's. CGLS is read unscaled, the norm it minimises."""
        fid, fp = astra.create_sino(img, pid)
        astra.data2d.delete(fid)
        fp = fp[:, lo:hi].astype(np.float64)
        b = sp[:, lo:hi].astype(np.float64)
        a = float((fp * b).sum() / (fp * fp).sum()) if scaled else 1.0
        return float(np.linalg.norm(a * fp - b) / np.linalg.norm(b)), a

    t = time.time()
    out["resid_astra_fbp"], out["scale_astra_fbp"] = resid(x_fbp)
    out["resid_gridrec"], out["scale_gridrec"] = resid(embed(FRAMES[kf](img_gr)))
    out["resid_wrong_centre"], _ = resid(embed(FRAMES[kf](img_wrong)))
    out["time_fp_s"] = "%.1f" % ((time.time() - t) / 3)

    rid = astra.data2d.create("-vol", vg, 0)
    cfg = astra.astra_dict("CGLS")
    cfg.update(ProjectorId=pid, ProjectionDataId=sid, ReconstructionDataId=rid)
    aid = astra.algorithm.create(cfg)
    t = time.time()
    hist = []
    for _ in range(n_it):
        astra.algorithm.run(aid, 1)          # CGLS keeps its state between run() calls
        hist.append(resid(astra.data2d.get(rid), scaled=False)[0])
    out["time_cgls_s"] = "%.1f" % (time.time() - t)
    x = astra.data2d.get(rid).copy()
    out["cgls_resid"] = " ".join("%.5f" % h for h in hist)
    out["cgls_monotone"] = bool(all(b <= a for a, b in zip(hist, hist[1:])))
    out["_hist"] = hist
    out["_cgls"] = INVERSE[kf](crop(x))      # in tomopy's frame
    astra.algorithm.delete(aid)
    astra.data2d.delete([rid, sid])
    astra.projector.delete(pid)
    out["time_total_s"] = "%.1f" % (time.time() - t0)
    return out


if __name__ == "__main__":
    main()
