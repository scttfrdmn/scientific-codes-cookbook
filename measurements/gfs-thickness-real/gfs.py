#!/usr/bin/env python3
"""MetPy + eccodes over one whole GFS forecast cycle at 0.25 deg (gfs.20251001/00, f000-f384).

    gfs.py pin    <dir> <md5 list> <sha256 out>      check every file against the source MD5, write sha256s
    gfs.py verify <dir> <sha256 list>                 check every file against its sha256 pin
    gfs.py run    <dir> <outdir> <workers> [hours]    the workload; hours = comma list (probe) or all

Per forecast hour (one GRIB2 file, ~743 messages, ~540 MB):
  1. eccodes decodes EVERY message and checks it against NOAA's wgrib2 inventory (.idx): same
     count, same byte offset, same forecast end-hour; the messages tile the file exactly; every
     TMP/RH/HGT isobaric record is the eccodes field it names; missing-value counts match the bitmap.
  2. cfgrib opens t, r, gh (one shortName per open, indexpath="" -- the recipe's two settings) and
     must return the same level vector for all three and the same values eccodes decoded.
  3. MetPy computes the 1000-500 hPa hydrostatic thickness for all 1,038,240 columns at once and is
     checked three ways: against an independent trapezoid in numpy (same formula, own code), against
     its own documented 1-D profile call on 205 columns, and against the file's own geopotential.
  4. theta(p,T) -> T(p,theta) over the whole 3-D temperature field.
"""
import os
for _v in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS"):
    os.environ[_v] = "1"          # one process per file; never a BLAS thread per core on top
import sys, time, json, hashlib, resource

ROWS = []


def ver(p):
    try:
        from importlib.metadata import version
        return str(version(p))
    except Exception:
        return "unknown"


def hours_all():
    return list(range(0, 121)) + list(range(123, 385, 3))


def fname(h):
    return "gfs.t00z.pgrb2.0p25.f%03d" % h


def _hash(path):
    m, s = hashlib.md5(), hashlib.sha256()
    with open(path, "rb") as fh:
        while True:
            b = fh.read(1 << 24)
            if not b:
                break
            m.update(b)
            s.update(b)
    return os.path.basename(path), m.hexdigest(), s.hexdigest()


def pin(d, md5list, out):
    import multiprocessing as mp
    want = dict((l.split()[1], l.split()[0]) for l in open(md5list) if l.strip())
    paths = [os.path.join(d, k) for k in sorted(want)]
    t0 = time.time()
    with mp.Pool(os.cpu_count()) as pool:
        got = pool.map(_hash, paths, chunksize=1)
    bad = [k for k, m, s in got if want[k] != m]
    print("pin\t%d files hashed in %.1f s; %d md5 mismatches against the source ETags" % (len(got), time.time() - t0, len(bad)))
    if bad or len(got) != len(want):
        sys.exit("FAIL: staged bytes do not match the source MD5s: %s" % bad[:5])
    with open(out, "w") as fh:
        for k, m, s in got:
            fh.write("%s  %s\n" % (s, k))
    print("pin\twrote %s" % out)


def verify(d, shalist):
    import multiprocessing as mp
    want = dict((l.split()[1], l.split()[0]) for l in open(shalist) if l.strip())
    t0 = time.time()
    with mp.Pool(os.cpu_count()) as pool:
        got = pool.map(_hash, [os.path.join(d, k) for k in sorted(want)], chunksize=1)
    bad = [k for k, m, s in got if want[k] != s]
    print("verify\t%d files, %d sha256 mismatches, %.1f s" % (len(got), len(bad), time.time() - t0))
    if bad or len(got) != len(want):
        sys.exit("FAIL: a staged file does not match its pin: %s" % bad[:5])


def idx_end_hour(s):
    # "anl" -> 0; "3 hour fcst" -> 3; "0-3 hour ave fcst" -> 3; "0-1 day acc fcst" -> 24
    if s == "anl":
        return 0
    tok, unit = s.split()[:2]
    return int(tok.split("-")[-1]) * {"hour": 1, "day": 24}[unit]


def one(args):
    d, h, outdir = args
    import numpy as np
    import eccodes as ec
    import xarray as xr
    import metpy.calc as mpcalc
    from metpy.units import units
    import metpy.constants as C
    path = os.path.join(d, fname(h))
    r = {"hour": h, "fail": []}
    fail = r["fail"]
    T = {}
    t0 = time.time()

    # ---------------------------------------------------------------- 1. every message vs the .idx
    inv = []
    for l in open(path + ".idx"):
        if l.strip():
            f = l.rstrip("\n").split(":")
            inv.append((int(f[0]), int(f[1]), f[3], f[4], f[5]))
    size = os.path.getsize(path)
    dig = hashlib.sha256()
    n = 0
    end = 0
    nvals = 0
    nmiss_tot = 0
    step_bad = 0
    named_bad = 0
    t_iso, r_iso, gh_iso, sp = {}, {}, {}, None
    with open(path, "rb") as fh:
        while True:
            g = ec.codes_grib_new_from_file(fh)
            if g is None:
                break
            try:
                off = ec.codes_get(g, "offset")
                L = ec.codes_get(g, "totalLength")
                sn = ec.codes_get(g, "shortName")
                tl = ec.codes_get(g, "typeOfLevel")
                lev = ec.codes_get(g, "level")
                es = ec.codes_get(g, "endStep")
                npts = ec.codes_get(g, "numberOfDataPoints")
                nmiss = ec.codes_get(g, "numberOfMissing")
                miss = ec.codes_get(g, "missingValue")
                ddate, dtime = ec.codes_get(g, "dataDate"), ec.codes_get(g, "dataTime")
                v = ec.codes_get_values(g)
            finally:
                ec.codes_release(g)
            if n >= len(inv):
                fail.append("more messages than the inventory lists")
                break
            num, ioff, ivar, ilev, ifc = inv[n]
            if num != n + 1 or ioff != off:
                fail.append("message %d at offset %d, inventory says %d" % (n + 1, off, ioff))
            if off != end:
                fail.append("message %d does not start where %d ended" % (n + 1, n))
            end = off + L
            if es != idx_end_hour(ifc) or es != h:
                step_bad += 1
            if (ddate, dtime) != (20251001, 0):
                fail.append("message %d reference time %s %s" % (n + 1, ddate, dtime))
            if v.size != npts or npts != 1038240:
                fail.append("message %d decoded %d of %d points" % (n + 1, v.size, npts))
            if not bool(np.isfinite(v).all()):
                fail.append("message %d has non-finite decoded values" % (n + 1))
            if nmiss and int((v == miss).sum()) != nmiss:
                fail.append("message %d: %d missing-valued points, bitmap says %d" % (n + 1, int((v == miss).sum()), nmiss))
            nmiss_tot += nmiss
            dig.update(v.tobytes())
            nvals += v.size
            # the publisher's name for this message must be the field eccodes decoded
            if ivar in ("TMP", "RH", "HGT") and ilev.endswith(" mb"):
                p_hpa = float(ilev[:-3])
                want_sn = {"TMP": "t", "RH": "r", "HGT": "gh"}[ivar]
                got_hpa = lev if tl == "isobaricInhPa" else (lev / 100.0 if tl == "isobaricInPa" else None)
                if sn != want_sn or got_hpa is None or abs(got_hpa - p_hpa) > 1e-9:
                    named_bad += 1
                elif tl == "isobaricInhPa":     # cfgrib decodes to float32: hash what it should return
                    {"t": t_iso, "r": r_iso, "gh": gh_iso}[sn][int(lev)] = \
                        hashlib.sha256(v.astype(np.float32).tobytes()).hexdigest()
            if ivar == "PRES" and ilev == "surface":
                sp = v.reshape(721, 1440)
            n += 1
    if n != len(inv):
        fail.append("%d messages decoded, inventory lists %d" % (n, len(inv)))
    if end != size:
        fail.append("messages end at byte %d, file is %d" % (end, size))
    if step_bad:
        fail.append("%d messages whose endStep differs from the inventory/file hour" % step_bad)
    if named_bad:
        fail.append("%d TMP/RH/HGT isobaric records are not the field the inventory names" % named_bad)
    if sp is None:
        fail.append("no surface pressure")
    r.update(messages=n, inventory=len(inv), values=nvals, missing=nmiss_tot, decode_sha256=dig.hexdigest(),
             iso_levels=[len(t_iso), len(r_iso), len(gh_iso)])
    t1 = time.time()

    # ---------------------------------------------------------------- 2. cfgrib, the recipe's path
    def load(short):
        return xr.open_dataset(path, engine="cfgrib", backend_kwargs={
            "indexpath": "", "filter_by_keys": {"typeOfLevel": "isobaricInhPa", "shortName": short}})
    dsT, dsR, dsH = load("t"), load("r"), load("gh")
    levT, levR, levH = dsT.isobaricInhPa.values, dsR.isobaricInhPa.values, dsH.isobaricInhPa.values
    if not (np.array_equal(levT, levR) and np.array_equal(levT, levH)):
        fail.append("T, RH and HGT on different isobaric levels (%d/%d/%d)" % (levT.size, levR.size, levH.size))
    lat, lon = dsT.latitude.values, dsT.longitude.values
    if not (lat.size == 721 and lon.size == 1440 and lat[0] == 90 and lat[-1] == -90 and lon[0] == 0 and lon[-1] == 359.75):
        fail.append("unexpected grid")
    aT, aR, aH = dsT["t"].values, dsR["r"].values, dsH["gh"].values
    cf_bad = 0
    for k, lv in enumerate(levT):
        for arr, ref in ((aT, t_iso), (aR, r_iso), (aH, gh_iso)):
            e = ref.get(int(lv))
            if arr.dtype != np.float32 or e != hashlib.sha256(np.ascontiguousarray(arr[k]).tobytes()).hexdigest():
                cf_bad += 1
    if cf_bad:
        fail.append("%d cfgrib level fields differ from eccodes' direct decode" % cf_bad)
    r["cfgrib_levels"] = int(levT.size)
    r["cfgrib_dtype"] = str(aT.dtype)
    t2 = time.time()

    # ---------------------------------------------------------------- 3. MetPy thickness, every column
    m = (levT >= 500) & (levT <= 1000)
    order = np.argsort(-levT[m])
    plev = levT[m][order].astype(np.float64)
    if plev.size != 13 or plev[0] != 1000 or plev[-1] != 500:
        fail.append("1000-500 hPa levels: %s" % plev)
    Tk = np.moveaxis(aT[m][order].astype(np.float64), 0, -1)          # (721, 1440, 13)
    RH = np.moveaxis(aR[m][order].astype(np.float64), 0, -1)
    p_q = plev * units.hPa
    T_q = Tk * units.kelvin
    rh_q = np.clip(RH, 0.0, 100.0) * units.percent
    w = mpcalc.mixing_ratio_from_relative_humidity(p_q, T_q, rh_q)
    thick = np.asarray(mpcalc.thickness_hydrostatic(p_q, T_q, mixing_ratio=w).m_as("m"))
    if thick.shape != (721, 1440):
        fail.append("thickness shape %s" % (thick.shape,))
    wm = np.asarray(w.m_as(""))
    eps, Rd, g = C.nounit.epsilon, C.nounit.Rd, C.nounit.g
    Tv = Tk * (wm + eps) / (eps * (1.0 + wm))
    x = np.log(plev * 100.0)
    own = -(Rd / g) * (0.5 * (Tv[..., 1:] + Tv[..., :-1]) * np.diff(x)).sum(axis=-1)
    r["own_vs_metpy_max_rel"] = float(np.max(np.abs(own - thick) / np.abs(thick)))
    # MetPy documents thickness_hydrostatic as 1-D only: the grid call must equal the profile call
    rng = np.random.default_rng(20251001 + h)
    cols = [(int(a), int(b)) for a, b in zip(rng.integers(0, 721, 200), rng.integers(0, 1440, 200))]
    for la, lo in ((40.0, 260.0), (0.0, 180.0), (-45.0, 30.0), (60.0, 350.0), (20.0, 100.0)):
        cols.append((int(round((90 - la) * 4)), int(round(lo * 4))))
    prof = 0.0
    for i, j in cols:
        w1 = mpcalc.mixing_ratio_from_relative_humidity(p_q, Tk[i, j] * units.kelvin, np.clip(RH[i, j], 0, 100) * units.percent)
        t1d = float(mpcalc.thickness_hydrostatic(p_q, Tk[i, j] * units.kelvin, mixing_ratio=w1).m_as("m"))
        prof = max(prof, abs(t1d - thick[i, j]) / thick[i, j])
    r["profile_vs_grid_max_rel"] = prof
    # against the file's own geopotential
    k500, k1000 = int(np.where(levT == 500)[0][0]), int(np.where(levT == 1000)[0][0])
    model = aH[k500].astype(np.float64) - aH[k1000].astype(np.float64)
    rel = np.abs(thick - model) / np.abs(model)
    if not bool(np.isfinite(rel).all()):
        fail.append("non-finite thickness deviation")
    above = sp >= 100000.0                       # 1000 hPa is above the ground: both sides are real air
    cw = np.cos(np.deg2rad(lat))[:, None] * np.ones((1, 1440))
    r.update(cols_above=int(above.sum()), cols_below=int((~above).sum()),
             rel_max_above=float(rel[above].max()), rel_p99_above=float(np.percentile(rel[above], 99)),
             rel_p50_above=float(np.median(rel[above])),
             rel_mean_above_areaw=float((rel * cw)[above].sum() / cw[above].sum()),
             bias_mean_above_m=float(((thick - model) * cw)[above].sum() / cw[above].sum()),
             rel_max_below=float(rel[~above].max()) if (~above).any() else 0.0,
             rel_p50_below=float(np.median(rel[~above])) if (~above).any() else 0.0,
             thick_mean_areaw_m=float((thick * cw).sum() / cw.sum()),
             model_mean_areaw_m=float((model * cw).sum() / cw.sum()),
             thick_sha256=hashlib.sha256(np.ascontiguousarray(thick).tobytes()).hexdigest())
    ia = np.unravel_index(np.argmax(np.where(above, rel, -1)), rel.shape)
    r["rel_max_above_at"] = [float(lat[ia[0]]), float(lon[ia[1]]), float(sp[ia] / 100.0)]
    np.save(os.path.join(outdir, "thick", "f%03d.npy" % h), thick)
    t3 = time.time()

    # ---------------------------------------------------------------- 4. theta involution, whole 3-D field
    p3 = levT.astype(np.float64)[:, None, None] * units.hPa
    T3 = aT.astype(np.float64) * units.kelvin
    th = mpcalc.potential_temperature(p3, T3)
    Tb = mpcalc.temperature_from_potential_temperature(p3, th)
    r["theta_involution_max_abs_K"] = float(np.nanmax(np.abs(Tb.m_as("kelvin") - T3.m)))
    r["theta_values"] = int(T3.size)
    t4 = time.time()
    r["t_decode"], r["t_cfgrib"], r["t_metpy"], r["t_theta"] = t1 - t0, t2 - t1, t3 - t2, t4 - t3
    r["maxrss_gib"] = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 2 ** 20
    return r


def run(d, outdir, workers, hours):
    import multiprocessing as mp
    import numpy as np
    os.makedirs(os.path.join(outdir, "thick"), exist_ok=True)
    for p in ("metpy", "cfgrib", "eccodes", "eccodeslib", "xarray", "pint", "numpy"):
        print("version\t%s %s" % (p, ver(p)))
    try:
        import eccodes
        print("version\teccodes-lib %s" % str(eccodes.codes_get_api_version()))
    except Exception as e:
        print("version\teccodes-lib unknown (%s)" % str(e))
    t0 = time.time()
    with mp.Pool(workers, maxtasksperchild=8) as pool:
        res = list(pool.imap_unordered(one, [(d, h, outdir) for h in hours], chunksize=1))
    wall = time.time() - t0
    res.sort(key=lambda r: r["hour"])
    keys = ["hour", "messages", "inventory", "values", "missing", "cfgrib_levels", "cols_above", "cols_below",
            "rel_max_above", "rel_p99_above", "rel_p50_above", "rel_mean_above_areaw", "bias_mean_above_m",
            "rel_max_below", "rel_p50_below", "own_vs_metpy_max_rel", "profile_vs_grid_max_rel",
            "theta_involution_max_abs_K", "thick_mean_areaw_m", "model_mean_areaw_m",
            "t_decode", "t_cfgrib", "t_metpy", "t_theta", "maxrss_gib", "decode_sha256", "thick_sha256"]
    with open(os.path.join(outdir, "per-hour.tsv"), "w") as fh:
        fh.write("\t".join(keys) + "\n")
        for r in res:
            fh.write("\t".join(("%.6g" % r[k]) if isinstance(r[k], float) else str(r[k]) for k in keys) + "\n")
    with open(os.path.join(outdir, "per-hour.json"), "w") as fh:
        json.dump(res, fh, indent=0)

    S = {}
    def rec(k, v):
        S[k] = str(v)
        print("%s\t%s" % (k, str(v)))
    bad = [(r["hour"], f) for r in res for f in r["fail"]]
    for hh, f in bad[:40]:
        print("FAILED\tf%03d %s" % (hh, f))
    rec("hours", len(res))
    rec("hours_expected", len(hours))
    rec("messages_decoded", sum(r["messages"] for r in res))
    rec("inventory_records", sum(r["inventory"] for r in res))
    rec("values_decoded", sum(r["values"] for r in res))
    rec("missing_points_by_bitmap", sum(r["missing"] for r in res))
    rec("columns_thickness", 1038240 * len(res))
    rec("columns_above_ground", sum(r["cols_above"] for r in res))
    rec("rel_max_above", "%.5f" % max(r["rel_max_above"] for r in res))
    worst = max(res, key=lambda r: r["rel_max_above"])
    rec("rel_max_above_where", "f%03d lat %.2f lon %.2f psfc %.1f hPa" % tuple([worst["hour"]] + worst["rel_max_above_at"]))
    rec("rel_p99_above_max_over_hours", "%.5f" % max(r["rel_p99_above"] for r in res))
    rec("rel_p50_above_median_over_hours", "%.6f" % float(np.median([r["rel_p50_above"] for r in res])))
    rec("rel_mean_above_areaw_mean", "%.6f" % float(np.mean([r["rel_mean_above_areaw"] for r in res])))
    rec("bias_mean_above_m_mean", "%.3f" % float(np.mean([r["bias_mean_above_m"] for r in res])))
    rec("rel_max_below", "%.5f" % max(r["rel_max_below"] for r in res))
    rec("rel_p50_below_median_over_hours", "%.6f" % float(np.median([r["rel_p50_below"] for r in res])))
    rec("own_vs_metpy_max_rel", "%.3e" % max(r["own_vs_metpy_max_rel"] for r in res))
    rec("profile_vs_grid_max_rel", "%.3e" % max(r["profile_vs_grid_max_rel"] for r in res))
    rec("theta_involution_max_abs_K", "%.3e" % max(r["theta_involution_max_abs_K"] for r in res))
    rec("theta_values", sum(r["theta_values"] for r in res))
    cyc = hashlib.sha256("".join(r["decode_sha256"] for r in res).encode()).hexdigest()
    cth = hashlib.sha256("".join(r["thick_sha256"] for r in res).encode()).hexdigest()
    rec("decode_digest_all_hours", cyc)
    rec("thickness_digest_all_hours", cth)
    rec("workers", workers)
    rec("workload_wall_s", "%.1f" % wall)
    rec("sum_file_s", "%.1f" % sum(r["t_decode"] + r["t_cfgrib"] + r["t_metpy"] + r["t_theta"] for r in res))
    for k in ("t_decode", "t_cfgrib", "t_metpy", "t_theta"):
        rec("sum_" + k + "_s", "%.1f" % sum(r[k] for r in res))
    rec("peak_rss_worker_gib", "%.2f" % max(r["maxrss_gib"] for r in res))
    with open(os.path.join(outdir, "summary.json"), "w") as fh:
        json.dump(S, fh, indent=1)

    # ------------------------------------------------------------------------- assertions
    def need(ok, msg):
        if not bool(ok):
            print("FAIL\t" + msg)
            return False
        return True
    ok = True
    ok &= need(not bad, "%d per-hour failures (above)" % len(bad))
    ok &= need(len(res) == len(hours), "hours processed %d != %d" % (len(res), len(hours)))
    ok &= need(max(r["rel_max_above"] for r in res) <= 0.02, "hypsometric thickness off the file's geopotential by >2% above ground")
    ok &= need(max(r["own_vs_metpy_max_rel"] for r in res) <= 1e-12, "MetPy's grid thickness != an independent trapezoid")
    ok &= need(max(r["profile_vs_grid_max_rel"] for r in res) <= 1e-12, "MetPy's grid call != its 1-D profile call")
    ok &= need(max(r["theta_involution_max_abs_K"] for r in res) <= 1e-9, "theta round trip is not an involution")
    if not ok:
        sys.exit("FAIL: a check failed")
    print("GFS THICKNESS REAL OK")


if __name__ == "__main__":
    mode = sys.argv[1]
    if mode == "pin":
        pin(*sys.argv[2:5])
    elif mode == "verify":
        verify(*sys.argv[2:4])
    elif mode == "run":
        d, outdir, workers = sys.argv[2], sys.argv[3], int(sys.argv[4])
        hrs = hours_all() if len(sys.argv) < 6 or sys.argv[5] == "all" else [int(x) for x in sys.argv[5].split(",")]
        run(d, outdir, workers, hrs)
    else:
        sys.exit("usage: gfs.py pin|verify|run ...")
