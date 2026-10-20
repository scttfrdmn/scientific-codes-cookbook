"""TEOS-10 over the whole Argo archive: every QC-good level since 1997 converted and checked.

For each daily profile file (3 oceans x every day of the record), the standard Argo-to-TEOS-10 conversion:
adjusted values where DATA_MODE is A or D, raw otherwise; levels kept where P, T and S all carry
QC 1, 2, 5 or 8 and the profile's date and position are good. Then
    SA = SA_from_SP(SP, p, lon, lat)   CT = CT_from_t(SA, t, p)   pt0, sigma0, rho, spiciness0,
    z = z_from_p(p, lat), and N^2 along each profile (Nsquared),
and the identities below, accumulated over every level of the year. Deterministic: files are
processed in sorted order and reduced in that order, maxima are order-free and sums are fsum'd.
"""
import collections, hashlib, json, math, os, resource, sys, time
from multiprocessing import Pool

import numpy as np
import netCDF4
import gsw

DIR = os.environ.get("ARGO_DIR", "/tmp/argo")
NPROC = int(os.environ.get("ARGO_PROCS", "8"))
LIMIT = int(os.environ.get("ARGO_LIMIT", "0"))
SAMPLE_DAYS = ("20230101", "20230401", "20230701", "20231001")   # full arrays staged out, cross-chip
GOOD = np.frombuffer(b"1258", dtype="S1")
BOUND = 1e-9    # the recipe's involution bound: a round trip returns its input to float precision


def ver(mod):
    return str(getattr(mod, "__version__", "unknown"))


def chars(v):
    """netCDF char array -> numpy bytes array (S1), whatever the masking."""
    a = np.ma.filled(v[:], b" ")
    return np.asarray(a).astype("S1")


def floats(ds, name, shape):
    if name not in ds.variables:
        return np.full(shape, np.nan)
    return np.ma.filled(ds.variables[name][:].astype("f8"), np.nan)


def qcs(ds, name, shape):
    if name not in ds.variables:
        return np.full(shape, b" ", dtype="S1")
    return chars(ds.variables[name]).reshape(shape)


def strs(ds, name):
    a = chars(ds.variables[name])
    return [b"".join(r).decode("ascii", "replace").strip() for r in a]


def mx(a):
    a = np.abs(np.asarray(a, dtype="f8"))
    a = a[np.isfinite(a)]
    return float(a.max()) if a.size else 0.0


def process(path):
    t0 = time.time()
    r = {"file": os.path.relpath(path, DIR)}
    with netCDF4.Dataset(path) as ds:
        ds.set_auto_maskandscale(True)
        ds.set_auto_chartostring(False)
        # PSAL may be absent: the earliest floats (1997-) measured temperature only, and their
        # levels are counted as rejected (no SA without salinity), never silently dropped.
        for need in ("DATA_MODE", "POSITION_QC", "JULD_QC", "LATITUDE", "LONGITUDE", "PRES", "TEMP",
                     "PLATFORM_NUMBER", "CYCLE_NUMBER", "DIRECTION"):
            if need not in ds.variables:
                raise KeyError("%s has no %s" % (path, need))
        r["no_psal"] = int("PSAL" not in ds.variables)
        npf = len(ds.dimensions["N_PROF"]); nlev = len(ds.dimensions["N_LEVELS"])
        shape = (npf, nlev)
        mode = chars(ds.variables["DATA_MODE"]).reshape(npf)
        use_adj = np.isin(mode, np.frombuffer(b"AD", dtype="S1"))
        val, qc = {}, {}
        for v in ("PRES", "TEMP", "PSAL"):
            raw, adj = floats(ds, v, shape), floats(ds, v + "_ADJUSTED", shape)
            q, aq = qcs(ds, v + "_QC", shape), qcs(ds, v + "_ADJUSTED_QC", shape)
            val[v] = np.where(use_adj[:, None], adj, raw)
            qc[v] = np.where(use_adj[:, None], aq, q)
        lat = floats(ds, "LATITUDE", (npf,)); lon = floats(ds, "LONGITUDE", (npf,))
        posqc = qcs(ds, "POSITION_QC", (npf,)); juldqc = qcs(ds, "JULD_QC", (npf,))
        keys = list(zip(strs(ds, "PLATFORM_NUMBER"),
                        [int(c) for c in np.ma.filled(ds.variables["CYCLE_NUMBER"][:], -1)],
                        [d.decode() for d in chars(ds.variables["DIRECTION"]).reshape(npf)]))
    r["profiles"] = npf
    r["mode_counts"] = {m.decode(): int((mode == m).sum()) for m in np.unique(mode)}
    prof_ok = np.isfinite(lat) & np.isfinite(lon) & np.isin(posqc, GOOD) & np.isin(juldqc, GOOD)
    filled = np.isfinite(val["PRES"])
    ok = prof_ok[:, None] & np.isfinite(val["PRES"]) & np.isfinite(val["TEMP"]) & np.isfinite(val["PSAL"])
    for v in ("PRES", "TEMP", "PSAL"):
        ok &= np.isin(qc[v], GOOD)
    r["slots"] = npf * nlev
    r["filled"] = int(filled.sum())
    r["good"] = int(ok.sum())
    r["rejected"] = int((filled & ~ok).sum())
    r["good_profiles"] = int(ok.any(axis=1).sum())

    p = val["PRES"][ok]; t = val["TEMP"][ok]; SP = val["PSAL"][ok]
    LAT = np.broadcast_to(lat[:, None], shape)[ok]; LON = np.broadcast_to(lon[:, None], shape)[ok]

    SA = gsw.SA_from_SP(SP, p, LON, LAT)
    CT = gsw.CT_from_t(SA, t, p)
    pt0 = gsw.pt0_from_t(SA, t, p)
    sig0 = gsw.sigma0(SA, CT)
    rho = gsw.rho(SA, CT, p)
    rho_x = gsw.rho_t_exact(SA, t, p)          # full Gibbs function, not the 75-term polynomial
    spic = gsw.spiciness0(SA, CT)
    z = gsw.z_from_p(p, LAT)

    finite = np.isfinite(SA) & np.isfinite(CT)
    r["sa_nonfinite"] = int((~finite).sum())
    r["sp_nonpositive"] = int((SP <= 0).sum())
    pos = finite & (SP > 0)      # SA_from_SP sets SP<0 to 0, which no inverse can undo

    # ROUND TRIPS (involutions) and TWO-ROUTE IDENTITIES on every converted level
    r["rt_SA_SP_SA"] = mx(gsw.SP_from_SA(SA, p, LON, LAT)[pos] - SP[pos])
    r["rt_t_CT_t"] = mx(gsw.t_from_CT(SA, CT, p)[finite] - t[finite])
    r["rt_CT_pt_CT"] = mx(gsw.CT_from_pt(SA, gsw.pt_from_CT(SA, CT))[finite] - CT[finite])
    r["id_CT_via_pt0"] = mx(gsw.CT_from_pt(SA, pt0)[finite] - CT[finite])
    r["rt_p_z_p"] = mx(gsw.p_from_z(z, LAT)[finite] - p[finite])
    r["id_sigma0_rho0"] = mx((gsw.rho(SA, CT, 0.0) - 1000.0)[finite] - sig0[finite])
    r["id_rho_specvol"] = mx((rho * gsw.specvol(SA, CT, p))[finite] - 1.0)

    # 75-term polynomial vs the Gibbs function: a cross-method observation, not an assertion
    d = (rho - rho_x)[finite]
    r["poly_n"] = int(d.size)
    r["poly_max"] = mx(d)
    r["poly_sumsq"] = float(math.fsum((d * d).tolist())) if d.size else 0.0
    r["poly_sum"] = float(math.fsum(d.tolist())) if d.size else 0.0
    fit = finite & (p <= 8000) & ((p <= 6500) | ((CT <= 10) & (SA >= 30)))
    r["poly_max_fitregion"] = mx((rho - rho_x)[fit])
    r["outside_fitregion"] = int((finite & ~fit).sum())
    r["below_freezing"] = int((CT[finite] < gsw.CT_freezing(SA[finite], p[finite], 0.0) - 1e-9).sum())

    # N^2 along each profile, on the 2-D grid with rejected levels as NaN (pairs touching one drop)
    SA2 = np.full(shape, np.nan); CT2 = np.full(shape, np.nan); P2 = np.where(ok, val["PRES"], np.nan)
    SA2[ok] = SA; CT2[ok] = CT
    with np.errstate(divide="ignore", invalid="ignore"):
        N2, pmid = gsw.Nsquared(SA2, CT2, P2, np.broadcast_to(lat[:, None], shape), axis=1)
    dp = np.diff(P2, axis=1)
    r["n2_dp_nonpositive"] = int((np.isfinite(dp) & (dp <= 0)).sum())   # repeated/inverted pressures
    n2 = N2[np.isfinite(N2)]
    r["n2_pairs"] = int(n2.size)
    r["n2_negative"] = int((n2 < 0).sum())

    # CHUNKING INVARIANCE: the ufuncs are elementwise, so per-profile calls must be bit-identical
    # to the whole-file call.
    rows = np.repeat(np.arange(npf), nlev).reshape(shape)[ok]
    diff = 0
    for i in np.unique(rows)[:: max(1, npf // 25)]:
        m = rows == i
        if not np.array_equal(gsw.CT_from_t(gsw.SA_from_SP(SP[m], p[m], LON[m], LAT[m]), t[m], p[m]),
                              CT[m], equal_nan=True):
            diff += 1
    r["chunk_mismatch"] = diff

    # sums for the year, and a digest of the converted bytes for cross-chip comparison
    for name, a in (("SA", SA), ("CT", CT), ("sigma0", sig0), ("spic", spic)):
        r["sum_" + name] = float(math.fsum(a[finite].tolist()))
    h = hashlib.sha256()
    for a in (SA, CT, pt0, sig0, rho, rho_x, spic, z):
        h.update(np.ascontiguousarray(a).tobytes())
    r["digest"] = h.hexdigest()
    r["dupkeys"] = [k for k, g in zip(keys, ok.any(axis=1)) if g]
    if os.path.basename(path)[:8] in SAMPLE_DAYS:
        out = "/tmp/sample/" + r["file"].replace("/", "__").replace(".nc", ".npz")
        np.savez(out, p=p, t=t, SP=SP, SA=SA, CT=CT, pt0=pt0, sigma0=sig0, rho=rho, rho_x=rho_x,
                 spic=spic, z=z, N2=N2)
    r["sec"] = time.time() - t0
    r["worker_maxrss_kib"] = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return r


def safe(path):
    """A file that cannot be read is a reported failure, never a crash that strands the pool."""
    try:
        return process(path)
    except Exception as e:
        return {"file": os.path.relpath(path, DIR), "error": "%s: %s" % (type(e).__name__, e)}


def checkvalues():
    """The recipe's own check: gsw's published TEOS-10 check-value table, at its tolerances."""
    cv = np.load(os.path.join(os.path.dirname(gsw.__file__), "tests", "gsw_cv_v3_0.npz"))
    SA, CT, p = cv["SA_chck_cast"], cv["CT_chck_cast"], cv["p_chck_cast"]
    failed = 0
    for name in ("rho", "specvol", "alpha", "beta", "sound_speed", "internal_energy", "enthalpy",
                 "dynamic_enthalpy"):
        d = mx(getattr(gsw, name)(SA, CT, p) - cv[name])
        tol = float(np.max(np.abs(cv[name + "_ca"])))
        failed += not (d <= tol)
        print("checkvalue\t%-16s %.3e  tol %.3e" % (name, d, tol))
    d = mx(gsw.pt_from_CT(SA, CT) - cv["pt_from_CT"])
    failed += not (d <= float(np.max(np.abs(cv["pt_from_CT_ca"]))))
    print("checkvalue\tpt_from_CT       %.3e" % d)
    return failed


def corename():
    try:
        import ctypes, glob
        for lib in glob.glob(os.path.join(sys.prefix, "lib", "libopenblas*.so*")):
            f = ctypes.CDLL(lib).openblas_get_corename
            f.restype = ctypes.c_char_p
            return str(f().decode())
    except Exception as e:
        return "unavailable (%s)" % type(e).__name__
    return "no libopenblas"


def main():
    T0 = time.time()
    print("version\tgsw %s numpy %s netCDF4 %s python %s" % (ver(gsw), ver(np), ver(netCDF4),
                                                             sys.version.split()[0]))
    print("openblas_corename\t%s" % corename())
    files = sorted(os.path.join(dp, f) for dp, _, fs in os.walk(DIR) for f in fs if f.endswith("_prof.nc"))
    if LIMIT:
        files = files[:LIMIT]
    print("files\t%d" % len(files))
    os.makedirs("/tmp/sample", exist_ok=True)
    cv_failed = checkvalues()

    t1 = time.time()
    with Pool(NPROC) as pool:      # the with-block terminates the workers on any exit path
        res = list(pool.imap(safe, files, chunksize=1))
    # Python 3.14 starts workers from a forkserver, so they are not our children and
    # RUSAGE_CHILDREN never sees them: each worker reports its own ru_maxrss instead.
    wall = time.time() - t1
    errors = [r for r in res if "error" in r]
    for r in errors[:20]:
        print("ERROR\t%s\t%s" % (r["file"], r["error"]))
    print("file_errors\t%d" % len(errors))
    if errors:
        sys.exit("FAIL: %d files could not be converted" % len(errors))
    print("convert_wall_s\t%.1f" % wall)

    S = collections.Counter()
    for k in ("profiles", "good_profiles", "slots", "filled", "good", "rejected", "sa_nonfinite",
              "sp_nonpositive", "poly_n", "outside_fitregion", "below_freezing", "n2_pairs",
              "n2_negative", "n2_dp_nonpositive", "chunk_mismatch", "no_psal"):
        S[k] = sum(r[k] for r in res)
    modes = collections.Counter()
    for r in res:
        modes.update(r["mode_counts"])
    M = {k: max(r[k] for r in res) for k in ("rt_SA_SP_SA", "rt_t_CT_t", "rt_CT_pt_CT", "id_CT_via_pt0",
                                              "rt_p_z_p", "id_sigma0_rho0", "id_rho_specvol",
                                              "poly_max", "poly_max_fitregion")}
    sums = {k: math.fsum(r["sum_" + k] for r in res) for k in ("SA", "CT", "sigma0", "spic")}
    poly_rms = math.sqrt(math.fsum(r["poly_sumsq"] for r in res) / max(1, S["poly_n"]))
    poly_mean = math.fsum(r["poly_sum"] for r in res) / max(1, S["poly_n"])
    keys = collections.Counter(k for r in res for k in r["dupkeys"])
    geo = collections.Counter()
    for r in res:
        o, y = r["file"].split("/")[:2]
        geo[(o[0].upper(), y)] += r["profiles"]
    idx = collections.Counter()
    ip = os.path.join(DIR, "ar_index_global_prof.txt.gz")
    if os.path.exists(ip):
        import gzip
        with gzip.open(ip, "rt") as fh:
            for line in fh:
                if line.startswith("#") or line.startswith("file,"):
                    continue
                f = line.split(",")
                if len(f) > 4:
                    idx[(f[4], f[1][:4])] += 1
    dups = sum(c - 1 for c in keys.values() if c > 1)

    out = collections.OrderedDict()
    out["files"] = len(files)
    out.update((k, S[k]) for k in ("profiles", "good_profiles", "slots", "filled", "good", "rejected"))
    out["data_mode"] = " ".join("%s=%d" % kv for kv in sorted(modes.items()))
    out["distinct_profile_keys"] = len(keys)
    out["duplicate_profile_keys"] = dups
    years = sorted({y for _, y in geo})
    for o in "API":
        out["profiles_geo_" + o] = sum(geo[(o, y)] for y in years)
        out["profiles_index_" + o] = sum(idx[(o, y)] for y in years)
    out["ocean_years"] = 3 * len(years)
    out["ocean_years_geo_eq_index"] = sum(geo[(o, y)] == idx[(o, y)] for o in "API" for y in years)
    for k in ("sa_nonfinite", "sp_nonpositive", "outside_fitregion", "below_freezing", "n2_pairs",
              "n2_negative", "n2_dp_nonpositive", "chunk_mismatch", "no_psal"):
        out[k] = S[k]
    for k, v in M.items():
        out[k] = "%.3e" % v
    out["poly_rms"] = "%.3e" % poly_rms
    out["poly_mean"] = "%.3e" % poly_mean
    for k, v in sums.items():
        out["mean_" + k] = "%.10f" % (v / max(1, S["good"] - S["sa_nonfinite"]))
    out["checkvalue_failed"] = cv_failed
    out["year_digest"] = hashlib.sha256("".join(r["digest"] for r in res).encode()).hexdigest()
    out["slowest_file_s"] = "%.2f" % max(r["sec"] for r in res)
    out["convert_wall_s"] = "%.1f" % wall
    out["total_wall_s"] = "%.1f" % (time.time() - T0)
    out["maxrss_self_mib"] = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss // 1024
    out["maxrss_worker_mib"] = max(r["worker_maxrss_kib"] for r in res) // 1024
    try:
        out["cgroup_memory_peak_mib"] = int(open("/sys/fs/cgroup/memory.peak").read()) // 2**20
    except Exception:
        out["cgroup_memory_peak_mib"] = "unavailable"
    with open("/tmp/gsw-results.tsv", "w") as fh:
        for k, v in out.items():
            fh.write("%s\t%s\n" % (k, str(v)))
            print("%s\t%s" % (k, str(v)))
    with open("/tmp/gsw-years.tsv", "w") as fh:
        fh.write("ocean\tyear\tprofiles_geo\tprofiles_index\n")
        for o in "API":
            for y in years:
                fh.write("%s\t%s\t%d\t%d\n" % (o, y, geo[(o, y)], idx[(o, y)]))
    with open("/tmp/gsw-perfile.tsv", "w") as fh:
        fh.write("file\tprofiles\tgood\trt_SA_SP_SA\trt_t_CT_t\tpoly_max\tdigest\tsec\n")
        for r in res:
            fh.write("%s\t%d\t%d\t%.3e\t%.3e\t%.3e\t%s\t%.2f\n" % (r["file"], r["profiles"], r["good"],
                     r["rt_SA_SP_SA"], r["rt_t_CT_t"], r["poly_max"], r["digest"], r["sec"]))

    fails = []
    def need(ok, msg):
        if not bool(ok):
            fails.append(msg)
    need(cv_failed == 0, "%d check values outside their published tolerance" % cv_failed)
    need(S["filled"] == S["good"] + S["rejected"], "level bookkeeping: filled != good + rejected")
    need(S["good"] > 0 and S["sa_nonfinite"] == 0, "%d converted levels gave non-finite SA/CT" % S["sa_nonfinite"])
    for k in ("rt_SA_SP_SA", "rt_t_CT_t", "rt_CT_pt_CT", "id_CT_via_pt0", "id_sigma0_rho0"):
        need(M[k] <= BOUND, "%s = %.3e > %.0e" % (k, M[k], BOUND))
    need(M["rt_p_z_p"] <= BOUND, "rt_p_z_p = %.3e > %.0e" % (M["rt_p_z_p"], BOUND))
    need(M["id_rho_specvol"] <= 1e-12, "rho * specvol - 1 = %.3e" % M["id_rho_specvol"])
    need(S["chunk_mismatch"] == 0, "%d profiles differ when converted alone" % S["chunk_mismatch"])
    if fails:
        for f in fails:
            print("FAIL\t" + f)
        sys.exit(1)
    print("GSW ARGO OK")


if __name__ == "__main__":
    main()
