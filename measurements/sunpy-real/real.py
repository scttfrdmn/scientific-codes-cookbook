#!/usr/bin/env python3
"""sunpy-real: a whole day of SDO/AIA 171 A (2017-09-06, the X9.3 flare) through sunpy.

    python3 -u real.py <indir> <outdir> <nproc> [file ...]

Per image, all 25,000,000 pixels:
  1. FITS checksums, recomputed here from the raw bytes (ones-complement, FITS standard 4.0
     App. J), against the DATASUM and CHECKSUM JSOC wrote in 2022.
  2. JSOC's header statistics (DATAMIN/DATAMAX/DATAMEDN/DATAMEAN) against the pixels sunpy loads.
  3. JSOC's disc geometry: RSUN_OBS against arcsin(RSUN_REF/DSUN_OBS) -- and arctan as the
     control -- and R_SUN against RSUN_OBS/CDELT.
  4. The observer: sunpy's observer coordinate against JSOC's own HGLN/CRLN/CRLT and HAE vector.
  5. Every pixel through pix -> HPC -> HGS: finite exactly on the disc that arcsin(R/d) predicts,
     and back to the same pixel; HGS -> Carrington is a rotation about the solar axis, so the
     latitude is unchanged and the longitude offset is the same for every pixel.
Then a derotated datacube: every image reprojected onto the 12:00 frame of the flaring region
with propagate_with_solar_surface, with the self-derotation as an identity and the un-derotated
cutout as the control.
"""
import hashlib
import json
import os
import resource
import sys
import time
import warnings

import numpy as np

from astropy.utils import iers
iers.conf.auto_download = False
iers.conf.auto_max_age = None
from astropy.utils.data import conf as data_conf
data_conf.allow_internet = False

INDIR, OUTDIR, NPROC = sys.argv[1], sys.argv[2], int(sys.argv[3])
ONLY = sys.argv[4:]
REF_NAME = "sdo_aia_h2_20170906T120000_0171_v1.fits"
# The flaring region, NOAA AR 12673, at 12:00: the X9.3 peaked at 12:02 UT near S09W34.
REGION = dict(cx=520.0, cy=-240.0, half=300.0)       # arcsec: a 600" x 600" cutout
CHUNK = 500                                          # rows per transform chunk (2.5 M pixels)
ARC = 3600.0 * 180.0 / np.pi


def ones_sum(buf):
    """FITS 32-bit ones-complement sum of a byte string whose length is a multiple of 4."""
    w = np.frombuffer(buf, dtype=">u4").astype(np.uint64)
    s = int(w.sum())
    while s >> 32:
        s = (s & 0xFFFFFFFF) + (s >> 32)
    return s


def fits_checksums(path):
    """Recompute HDU 1's DATASUM and CHECKSUM from the raw bytes, independent of astropy."""
    raw = open(path, "rb").read()
    pos, hdus = 0, []
    while pos < len(raw):
        cards, start = {}, pos
        while True:
            block = raw[pos:pos + 2880]
            pos += 2880
            done = False
            for i in range(0, 2880, 80):
                c = block[i:i + 80].decode("ascii")
                if c.startswith("END") and c[3:].strip() == "":
                    done = True
                    break
                if c[8:10] == "= ":
                    v = c[10:].split("/")[0].strip() if not c[10:].strip().startswith("'") else c[10:].split("'")[1]
                    cards.setdefault(c[:8].strip(), v)
            if done:
                break
        hend = pos
        nax = int(cards.get("NAXIS", 0))
        size = 0
        if nax:
            size = abs(int(cards["BITPIX"])) // 8
            for k in range(1, nax + 1):
                size *= int(cards["NAXIS%d" % k])
            size = (size + int(cards.get("PCOUNT", 0))) * int(cards.get("GCOUNT", 1))
        dend = hend + ((size + 2879) // 2880) * 2880
        hdus.append((cards, start, hend, dend))
        pos = dend
    cards, start, hend, dend = hdus[1]
    datasum = ones_sum(raw[hend:dend])
    total = ones_sum(raw[start:dend])
    return {"n_hdu": len(hdus), "file_bytes": len(raw),
            "datasum_ok": str(datasum) == cards.get("DATASUM", "").strip(),
            "datasum": datasum, "datasum_hdr": cards.get("DATASUM"),
            "checksum_ok": total == 0xFFFFFFFF, "checksum_total": "%08x" % total}


def hpc_rho(tx, ty):
    """Angular distance of a helioprojective direction from Sun centre (radians), haversine."""
    return 2.0 * np.arcsin(np.sqrt(np.sin(ty / 2) ** 2 + np.cos(ty) * np.sin(tx / 2) ** 2))


def ncc(a, b):
    m = np.isfinite(a) & np.isfinite(b)
    x, y = a[m] - a[m].mean(), b[m] - b[m].mean()
    return float((x * y).sum() / np.sqrt((x * x).sum() * (y * y).sum())), int(m.sum())


def region_wcs(ref):
    import astropy.units as u
    from astropy.coordinates import SkyCoord
    c = REGION
    bl = SkyCoord((c["cx"] - c["half"]) * u.arcsec, (c["cy"] - c["half"]) * u.arcsec, frame=ref.coordinate_frame)
    tr = SkyCoord((c["cx"] + c["half"]) * u.arcsec, (c["cy"] + c["half"]) * u.arcsec, frame=ref.coordinate_frame)
    return ref.submap(bl, top_right=tr)


def work(name):
    t0 = time.time()
    import astropy.units as u
    from astropy.coordinates import SkyCoord, HeliocentricMeanEcliptic
    from astropy.io import fits
    import sunpy.map
    from sunpy.coordinates import frames, propagate_with_solar_surface
    warnings.simplefilter("ignore")
    path = os.path.join(INDIR, name)
    r = {"file": name}
    T = {}

    # ---- 1. checksums from the raw bytes
    r.update(fits_checksums(path))
    T["checksum"] = time.time() - t0

    # ---- 2. load through sunpy, header statistics
    m = sunpy.map.Map(path)
    h = m.meta
    r["map_class"] = type(m).__name__
    r["date_obs"] = str(m.date.isot)
    r["shape"] = list(m.data.shape)
    r["dtype"] = str(m.data.dtype)
    d = np.asarray(m.data, dtype=np.float64)
    blank = h.get("blank")
    nblank = int((m.data == blank).sum()) if blank is not None and m.data.dtype.kind in "iu" else 0
    r["n_blank"] = nblank
    r["n_nan"] = int(np.isnan(d).sum())
    good = d[np.isfinite(d)]
    if blank is not None and m.data.dtype.kind in "iu":
        good = d[(m.data != blank)]
    r["stats"] = {"n": int(good.size), "min": float(good.min()), "max": float(good.max()),
                  "median": float(np.median(good)), "mean": float(good.mean()),
                  "rms": float(good.std())}
    r["stats_hdr"] = {k: h.get(k) for k in ("datavals", "datamin", "datamax", "datamedn", "datamean", "datarms")}
    r["data_sha256"] = hashlib.sha256(np.ascontiguousarray(m.data).tobytes()).hexdigest()
    T["load"] = time.time() - t0 - T["checksum"]

    # ---- 3. disc geometry in JSOC's own keywords
    dsun, rref = float(h["dsun_obs"]), float(h["rsun_ref"])
    x = rref / dsun
    rs = float(h["rsun_obs"])
    r["rsun_obs"] = rs
    r["rsun_rel_arcsin"] = abs(rs - np.arcsin(x) * ARC) / rs
    r["rsun_rel_arctan"] = abs(rs - np.arctan(x) * ARC) / rs
    r["rsun_x2_2"] = x * x / 2
    r["r_sun_px_rel"] = abs(float(h["r_sun"]) - rs / float(h["cdelt1"])) / float(h["r_sun"])
    r["map_rsun_obs"] = float(m.rsun_obs.to_value(u.arcsec))
    r["map_rsun_meters"] = float(m.rsun_meters.to_value(u.m))
    r["map_dsun"] = float(m.dsun.to_value(u.m))

    # ---- 4. the observer, sunpy vs JSOC
    obs = m.observer_coordinate
    r["observer_from"] = str(getattr(obs, "name", ""))
    hgs = obs.transform_to(frames.HeliographicStonyhurst(obstime=m.date))
    r["obs_hgln_minus_hdr_deg"] = float(hgs.lon.to_value(u.deg) - float(h["hgln_obs"]))
    r["obs_hglt_minus_crlt_deg"] = float(hgs.lat.to_value(u.deg) - float(h["crlt_obs"]))
    r["obs_dsun_minus_hdr_m"] = float(hgs.radius.to_value(u.m) - dsun)
    hgc = obs.transform_to(frames.HeliographicCarrington(obstime=m.date, observer="self"))
    r["obs_crln_minus_hdr_deg"] = float(((hgc.lon.to_value(u.deg) - float(h["crln_obs"]) + 180) % 360) - 180)
    hae = obs.transform_to(HeliocentricMeanEcliptic(obstime=m.date, equinox=m.date))
    v = hae.cartesian.xyz.to_value(u.m)
    hae_hdr = np.array([float(h["haex_obs"]), float(h["haey_obs"]), float(h["haez_obs"])])
    r["obs_hae_minus_hdr_km"] = float(np.linalg.norm(v - hae_hdr) / 1e3)
    # JSOC's HAE vector, read as heliocentric mean ecliptic of J2000 and of date, into Stonyhurst:
    # which of JSOC's own keywords (HGLN_OBS, or CRLN/CRLT via sunpy) does the position vector agree with?
    for eq, tag in (("J2000", "j2000"), (m.date, "date")):
        c = SkyCoord(*(hae_hdr * u.m), representation_type="cartesian",
                     frame=HeliocentricMeanEcliptic(obstime=m.date, equinox=eq))
        s = c.transform_to(frames.HeliographicStonyhurst(obstime=m.date))
        r["hae_%s_hgln_minus_hdr_deg" % tag] = float(s.lon.to_value(u.deg) - float(h["hgln_obs"]))
        r["hae_%s_hgln_minus_sunpy_deg" % tag] = float((s.lon - hgs.lon).wrap_at(180 * u.deg).to_value(u.deg))
        r["hae_%s_hglt_minus_crlt_deg" % tag] = float(s.lat.to_value(u.deg) - float(h["crlt_obs"]))
        r["hae_%s_r_minus_dsun_m" % tag] = float(s.radius.to_value(u.m) - dsun)
    T["meta"] = time.time() - t0 - T["checksum"] - T["load"]

    # ---- 5. every pixel
    ny, nx = m.data.shape
    lim = np.arcsin(m.rsun_meters.to_value(u.m) / m.dsun.to_value(u.m))
    n_on = n_mis = 0
    mis_worst = 0.0
    rt_worst = rt_worst_inner = 0.0
    rt_worst_rho = 0.0
    lat_worst = 0.0
    off_min, off_max = np.inf, -np.inf
    hgc_frame = frames.HeliographicCarrington(obstime=m.date, observer=obs)
    xs = np.arange(nx, dtype=np.float64)
    for y0 in range(0, ny, CHUNK):
        yy, xx = np.meshgrid(np.arange(y0, min(y0 + CHUNK, ny), dtype=np.float64), xs, indexing="ij")
        xx, yy = xx.ravel(), yy.ravel()
        hpc = m.wcs.pixel_to_world(xx, yy)
        tx, ty = hpc.Tx.to_value(u.rad), hpc.Ty.to_value(u.rad)
        rho = hpc_rho(tx, ty)
        s = SkyCoord(hpc).transform_to(frames.HeliographicStonyhurst(obstime=m.date))
        lon, lat = s.lon.to_value(u.deg), s.lat.to_value(u.deg)
        fin = np.isfinite(lon) & np.isfinite(lat)
        ana = rho < lim
        mis = fin != ana
        n_on += int(fin.sum())
        if mis.any():
            n_mis += int(mis.sum())
            mis_worst = max(mis_worst, float(np.abs(np.sin(rho[mis]) / np.sin(lim) - 1.0).max()))
        if fin.any():
            sf = s[fin]
            back = sf.transform_to(m.coordinate_frame)
            px, py = m.wcs.world_to_pixel(back)
            err = np.hypot(px - xx[fin], py - yy[fin])
            if not np.all(np.isfinite(err)):
                rt_worst = np.inf
            else:
                k = int(np.argmax(err))
                if err[k] > rt_worst:
                    rt_worst, rt_worst_rho = float(err[k]), float(rho[fin][k] / lim)
                inner = rho[fin] < 0.99 * lim
                if inner.any():
                    rt_worst_inner = max(rt_worst_inner, float(err[inner].max()))
            c = sf.transform_to(hgc_frame)
            lat_worst = max(lat_worst, float(np.abs(c.lat.to_value(u.deg) - lat[fin]).max()))
            off = (c.lon.to_value(u.deg) - lon[fin]) % 360.0
            off_min, off_max = min(off_min, float(off.min())), max(off_max, float(off.max()))
    r["n_on_disc"] = n_on
    # TAN projection: the limb is a circle of radius tan(lim)/cdelt pixels.
    rpx = np.tan(lim) / np.radians(float(h["cdelt1"]) / 3600.0)
    r["limb_radius_px"] = float(rpx)
    r["n_on_disc_pred"] = float(np.pi * rpx ** 2)
    r["n_limb_mismatch"] = n_mis
    r["limb_mismatch_worst_rel"] = mis_worst
    r["roundtrip_px_max"] = rt_worst
    r["roundtrip_px_max_at_rho"] = rt_worst_rho
    r["roundtrip_px_max_inner99"] = rt_worst_inner
    r["hgc_lat_minus_hgs_max_deg"] = lat_worst
    r["hgc_lon_offset_deg"] = off_min
    r["hgc_lon_offset_spread_deg"] = off_max - off_min
    r["hgc_offset_minus_hdr_deg"] = float(((off_min - (float(h["crln_obs"]) - float(h["hgln_obs"])) + 180) % 360) - 180)
    T["pixels"] = time.time() - t0 - sum(T.values())

    # ---- 6. derotate onto the reference frame of the flaring region
    ref = sunpy.map.Map(os.path.join(INDIR, REF_NAME))
    ref_sub = region_wcs(ref)
    with propagate_with_solar_surface():
        der = m.reproject_to(ref_sub.wcs)
    dd = np.asarray(der.data, dtype=np.float64)
    raw_sub = region_wcs(m)
    rr = np.asarray(raw_sub.data, dtype=np.float64)
    refd = np.asarray(ref_sub.data, dtype=np.float64)
    r["dt_hours"] = float((m.date - ref.date).to_value(u.hour))
    r["region_shape"] = list(refd.shape)
    r["ncc_derot"], r["n_derot"] = ncc(dd, refd)
    r["ncc_raw"], _ = ncc(rr, refd)
    r["derot_finite"] = int(np.isfinite(dd).sum())
    r["derot_sha256"] = hashlib.sha256(np.ascontiguousarray(dd).tobytes()).hexdigest()
    fd = dd[np.isfinite(dd)]
    r["derot_sum"] = repr(float(fd.sum()))
    if name == REF_NAME:
        both = np.isfinite(dd) & np.isfinite(refd)
        r["self_derot_max_rel"] = float(np.abs(dd[both] - refd[both]).max() / np.abs(refd[both]).max())
        r["self_derot_finite_match"] = bool((np.isfinite(dd) == np.isfinite(refd)).all())
    T["derotate"] = time.time() - t0 - sum(T.values())
    r["t"] = T
    r["wall_s"] = time.time() - t0
    r["maxrss_kib"] = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return r


def main():
    os.makedirs(OUTDIR, exist_ok=True)
    import multiprocessing as mp
    import astropy
    import sunpy
    import reproject
    names = sorted(f for f in os.listdir(INDIR) if f.endswith(".fits"))
    if ONLY:
        names = [n for n in names if n in ONLY]
    vers = {}
    for k, mod in (("sunpy", sunpy), ("astropy", astropy), ("numpy", np), ("reproject", reproject)):
        vers[k] = str(getattr(mod, "__version__", "unknown"))
    try:
        import ctypes
        libs = sorted({l.split()[-1] for l in open("/proc/self/maps") if "openblas" in l.lower() and ".so" in l})
        vers["openblas"] = str(libs)
        if libs:
            f = ctypes.CDLL(libs[0]).openblas_get_corename
            f.restype = ctypes.c_char_p
            vers["openblas_corename"] = str(f().decode())
    except Exception as exc:
        vers["openblas_corename"] = "unknown (%s)" % type(exc).__name__
    print("versions", vers, "network", iers.conf.auto_download, data_conf.allow_internet, flush=True)
    print("images", len(names), "workers", NPROC, flush=True)
    t0 = time.time()
    res = []
    with mp.get_context("fork").Pool(NPROC, maxtasksperchild=8) as pool:
        for r in pool.imap_unordered(work, names):
            res.append(r)
            print("done %3d/%d %s %.1fs on=%d mis=%d rt=%.2e ncc %.3f/%.3f" % (
                len(res), len(names), r["file"][11:26], r["wall_s"], r["n_on_disc"],
                r["n_limb_mismatch"], r["roundtrip_px_max"], r["ncc_derot"], r["ncc_raw"]), flush=True)
    wall = time.time() - t0
    res.sort(key=lambda r: r["file"])
    json.dump({"versions": vers, "wall_s": wall, "nproc": NPROC, "images": res,
               "children_maxrss_kib": resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss},
              open(os.path.join(OUTDIR, "results.json"), "w"), indent=1)
    print("wall_s %.1f" % wall, flush=True)
    sys.exit(check(res, vers))


def check(res, vers):
    lines, bad = [], []

    def a(name, ok, detail):
        ok = bool(ok)
        lines.append("%s\t%s\t%s" % ("PASS" if ok else "FAIL", name, detail))
        if not ok:
            bad.append(name)

    def fin(*v):
        return all(np.all(np.isfinite(np.asarray(x, dtype=float))) for x in v)

    def col(k):
        return np.array([r[k] for r in res], dtype=float)

    n = len(res)
    a("network off", not iers.conf.auto_download and not data_conf.allow_internet, "hermetic")
    a("all images ran", n == len(ONLY) if ONLY else n >= 300, "%d images" % n)
    a("AIAMap, 5000x5000", all(r["map_class"] == "AIAMap" and r["shape"] == [5000, 5000] for r in res),
      str(sorted({(r["map_class"], tuple(r["shape"]), r["dtype"]) for r in res})))
    a("DATASUM recomputed from raw bytes", all(r["datasum_ok"] for r in res), "%d/%d" % (sum(r["datasum_ok"] for r in res), n))
    a("CHECKSUM: HDU sums to -0", all(r["checksum_ok"] for r in res), "%d/%d" % (sum(r["checksum_ok"] for r in res), n))
    a("DATAVALS == pixels loaded, none blank", all(r["stats"]["n"] == r["stats_hdr"]["datavals"] == 25000000
      and r["n_nan"] == 0 and r["n_blank"] == 0 for r in res), "25,000,000 every image")
    # DATAMAX, DATAMEDN and DATAMIN are integer-DN keywords of a quantised image: within 1 DN, with
    # the exact count reported (JSOC's DATAMIN is 1 DN above the stored minimum in every image, and
    # its DATAMEDN 1 DN off in a minority). DATAMEAN and DATARMS are JSOC's float sums over 25 M
    # values: 1e-4 relative is ~70x the float32-accumulation error the full day showed.
    for k, hk, dn, tol in (("max", "datamax", 1, None), ("median", "datamedn", 1, None), ("min", "datamin", 1, None),
                           ("mean", "datamean", None, 1e-4), ("rms", "datarms", None, 1e-4)):
        got = np.array([r["stats"][k] for r in res])
        want = np.array([np.nan if r["stats_hdr"][hk] is None else float(r["stats_hdr"][hk]) for r in res])
        diff = want - got
        rel = np.abs(diff) / np.maximum(np.abs(want), 1e-300)
        detail = "max rel %.3e, %d/%d exact" % (np.max(rel), int((diff == 0).sum()), n)
        if dn is not None:
            vals = {}
            for x in diff.tolist():
                vals[x] = vals.get(x, 0) + 1
            a("header %s vs pixels, within %d DN" % (hk.upper(), dn), fin(diff) and np.abs(diff).max() <= dn,
              detail + "; header - pixels: %s" % ", ".join("%+g DN x%d" % kv for kv in sorted(vals.items())))
        else:
            a("header %s vs pixels" % hk.upper(), fin(rel) and rel.max() <= tol, detail + " (bound %g)" % tol)
    ras, rat, x2 = col("rsun_rel_arcsin"), col("rsun_rel_arctan"), col("rsun_x2_2")
    a("RSUN_OBS == arcsin(RSUN_REF/DSUN_OBS)", fin(ras) and ras.max() < 1e-9, "max rel %.3e" % ras.max())
    a("control: arctan form misses by x^2/2", fin(rat) and np.all(np.abs(rat / x2 - 1) < 1e-3),
      "rel %.4e..%.4e, ratio to x^2/2 %.6f..%.6f" % (rat.min(), rat.max(), (rat / x2).min(), (rat / x2).max()))
    rp = col("r_sun_px_rel")
    a("R_SUN == RSUN_OBS/CDELT1", fin(rp) and rp.max() < 1e-6, "max rel %.3e" % rp.max())
    mr = col("map_rsun_obs") - col("rsun_obs")
    a("sunpy rsun_obs == RSUN_OBS", fin(mr) and np.all(mr == 0), "max %.3e" % np.abs(mr).max())
    for k, unit in (("obs_hgln_minus_hdr_deg", "deg"), ("obs_hglt_minus_crlt_deg", "deg"), ("obs_dsun_minus_hdr_m", "m"),
                    ("obs_crln_minus_hdr_deg", "deg"), ("obs_hae_minus_hdr_km", "km"), ("hgc_offset_minus_hdr_deg", "deg"),
                    ("hae_j2000_hgln_minus_hdr_deg", "deg"), ("hae_j2000_hgln_minus_sunpy_deg", "deg"),
                    ("hae_j2000_hglt_minus_crlt_deg", "deg"), ("hae_j2000_r_minus_dsun_m", "m"),
                    ("hae_date_hgln_minus_hdr_deg", "deg"), ("hae_date_hgln_minus_sunpy_deg", "deg"),
                    ("hae_date_hglt_minus_crlt_deg", "deg"), ("hae_date_r_minus_dsun_m", "m")):
        v = col(k)
        lines.append("RECORD\t%s\t|max| %.3e %s, mean %.3e" % (k, np.abs(v).max(), unit, v.mean()))
    on, pred, rpx = col("n_on_disc"), col("n_on_disc_pred"), col("limb_radius_px")
    # Lattice points in a circle: |N - pi r^2| is bounded by the annulus of half-diagonal width,
    # 2 sqrt(2) pi r. Guaranteed, so it cannot flake; the measured excess is reported.
    a("on-disc pixel count == pi r^2 (TAN limb)", fin(on, pred) and np.all(np.abs(on - pred) <= 2 * np.sqrt(2) * np.pi * rpx),
      "%d..%d, max |N - pi r^2| %.0f px against bound %.0f (rel %.2e)" % (on.min(), on.max(), np.abs(on - pred).max(),
      (2 * np.sqrt(2) * np.pi * rpx).min(), np.abs(on / pred - 1).max()))
    mw = col("limb_mismatch_worst_rel")
    a("finite HGS exactly on the arcsin disc", fin(mw) and mw.max() < 1e-12,
      "%d boundary pixels disagree in %d images, all within %.2e of the limb" % (col("n_limb_mismatch").sum(), n, mw.max()))
    rt, rti = col("roundtrip_px_max"), col("roundtrip_px_max_inner99")
    a("pix->HPC->HGS->HPC->pix, every on-disc pixel", fin(rt) and rt.max() < 1e-6,
      "max %.3e px (at rho/limb %.6f); inside 0.99 limb %.3e px" % (rt.max(), res[int(np.argmax(rt))]["roundtrip_px_max_at_rho"], rti.max()))
    lw = col("hgc_lat_minus_hgs_max_deg")
    a("Carrington lat == Stonyhurst lat", fin(lw) and lw.max() < 1e-9, "max %.3e deg" % lw.max())
    sp = col("hgc_lon_offset_spread_deg")
    a("Carrington-Stonyhurst lon offset uniform per image", fin(sp) and sp.max() < 1e-9, "max spread %.3e deg" % sp.max())
    ref = [r for r in res if r["file"] == REF_NAME]
    if ref:
        a("self-derotation == reference", fin(ref[0]["self_derot_max_rel"]) and ref[0]["self_derot_max_rel"] < 1e-6
          and ref[0]["self_derot_finite_match"], "max rel %.3e" % ref[0]["self_derot_max_rel"])
    far = [r for r in res if abs(r["dt_hours"]) >= 1.0]
    wins = [r for r in far if r["ncc_derot"] > r["ncc_raw"]]
    a("derotation beats the raw cutout, |dt| >= 1 h", far and len(wins) == len(far),
      "%d/%d; ncc derot %.3f..%.3f, raw %.3f..%.3f" % (len(wins), len(far),
      min(r["ncc_derot"] for r in far) if far else 0, max(r["ncc_derot"] for r in far) if far else 0,
      min(r["ncc_raw"] for r in far) if far else 0, max(r["ncc_raw"] for r in far) if far else 0))
    for r in res:
        lines.append("FRAME\t%s\tdt=%+.3f h\tncc %.4f raw %.4f\ton %d\trt %.2e\tderot %s" % (
            r["file"][11:26], r["dt_hours"], r["ncc_derot"], r["ncc_raw"], r["n_on_disc"], r["roundtrip_px_max"], r["derot_sha256"][:16]))
    lines.append("SUNPY REAL OK" if not bad else "FAILED: " + ", ".join(bad))
    open(os.path.join(OUTDIR, "check.txt"), "w").write("\n".join(lines) + "\n")
    print("\n".join(l for l in lines if not l.startswith("FRAME")), flush=True)
    return 0 if not bad else 1


if __name__ == "__main__":
    main()
