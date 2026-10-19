#!/usr/bin/env python3
"""geo-ml-real: every US census tract through geopandas + libpysal + esda + spreg, and Colorado's
DEM summarised by tract with rasterio.

Vector leg (85,529 TIGER 2024 tracts, 42 M vertices):
  geodesic area against Census's own ALAND+AWATER, and against terra's on the same bytes
  (r-spatial-real); tracts dissolved to counties against the county file; a spatial join against
  the GEOID hierarchy; queen and rook contiguity two ways (libpysal's shared vertices, GEOS
  predicates) and collapsed onto the county graph; Moran's I and four OLS routes at national scale.
Raster leg (1,447 Colorado tracts over 362.88 M GLO-30 cells):
  rasterize + bincount, cell for cell against terra's zonal table from r-spatial-real.

Writes /tmp/out/results.json, smoke-check.txt and two CSVs, and prints GEO-ML REAL OK only if
every check passed.
"""
import ctypes
import glob
import hashlib
import json
import os
import re
import resource
import sys
import time
import warnings

warnings.filterwarnings("ignore")
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

OUT, WORK = "/tmp/out", "/var/tmp/work"
os.makedirs(OUT, exist_ok=True)
os.makedirs(WORK, exist_ok=True)
R, FAIL, LINES = {}, [], []
T0 = time.time()


def rec(k, v):
    # every non-numeric value through str(): a line that only describes the run must not fail it
    if isinstance(v, (bool, np.bool_)):
        v = bool(v)
    elif isinstance(v, (int, np.integer)):
        v = int(v)
    elif isinstance(v, (float, np.floating)):
        v = float(v)
    elif not isinstance(v, (list, dict)) and v is not None:
        v = str(v)
    R[k] = v


def say(line):
    print(line, flush=True)
    LINES.append(line)


def check(label, ok, detail):
    say("%-36s %s" % (label, detail))
    rec("ok_" + label, bool(ok))
    if not bool(ok):
        FAIL.append(label)


def obs(label, detail):
    say("%-36s %s   [observation]" % (label, detail))


class clock:
    def __init__(self, name):
        self.name = name

    def __enter__(self):
        self.t = time.time()

    def __exit__(self, *a):
        el = time.time() - self.t
        rec("t_" + self.name, round(el, 2))
        print("[%s] %.1fs" % (self.name, el), flush=True)


def safe(f):
    try:
        return f()
    except Exception as e:  # noqa: BLE001 -- describing the run must never fail it
        return "unknown (%s)" % type(e).__name__


def describe():
    txt = safe(lambda: open("/proc/cpuinfo").read())
    m = re.search(r"model name\s*:\s*(.+)", txt) if isinstance(txt, str) else None
    part = re.search(r"CPU part\s*:\s*(\S+)", txt) if isinstance(txt, str) else None
    rec("cpu_model", m.group(1).strip() if m else ("arm CPU part " + part.group(1) if part else "unknown"))

    def tpc():
        s = open("/sys/devices/system/cpu/cpu0/topology/thread_siblings_list").read().strip()
        n = 0
        for tok in s.split(","):
            a, _, b = tok.partition("-")
            n += (int(b) - int(a) + 1) if b else 1
        return n
    rec("threads_per_core", safe(tpc))
    rec("nproc", os.cpu_count())

    def core():
        lib = ctypes.CDLL(sorted(glob.glob(os.path.join(sys.prefix, "lib", "libopenblas*.so*")))[0])
        lib.openblas_get_corename.restype = ctypes.c_char_p
        return lib.openblas_get_corename().decode()
    rec("openblas_corename", safe(core))
    import esda, geopandas, libpysal, pyogrio, pyproj, rasterio, shapely, sklearn, spreg, statsmodels  # noqa
    for name, mod in (("geopandas", geopandas), ("shapely", shapely), ("pyproj", pyproj),
                      ("pyogrio", pyogrio), ("libpysal", libpysal), ("esda", esda),
                      ("spreg", spreg), ("sklearn", sklearn), ("statsmodels", statsmodels),
                      ("rasterio", rasterio), ("numpy", np)):
        rec(name, getattr(mod, "__version__", "unknown"))
    rec("geos", safe(lambda: shapely.geos_version_string))
    rec("proj", getattr(pyproj, "proj_version_str", "unknown"))
    rec("gdal", safe(lambda: rasterio.__gdal_version__))


def sha_file(p):
    return hashlib.sha256(open(p, "rb").read()).hexdigest()


def pairs_of(w):
    """Unordered neighbour pairs of a libpysal W as a sorted int64 (k, 2) array."""
    a = [(i, j) for i, nb in w.neighbors.items() for j in nb if i < j]
    return np.array(sorted(a), dtype=np.int64).reshape(-1, 2)


def pairset(p):
    return set(map(tuple, p.tolist()))


def main():
    import geopandas as gpd
    import shapely
    from pyproj import Geod

    describe()
    obs("versions", " ".join("%s %s" % (k, R[k]) for k in (
        "geopandas", "shapely", "geos", "pyproj", "proj", "pyogrio", "gdal", "libpysal", "esda",
        "spreg", "sklearn", "statsmodels", "rasterio", "numpy")))
    obs("cpu", "%s, %s vCPU, %s thread(s)/core, OpenBLAS %s" %
        (R["cpu_model"], R["nproc"], R["threads_per_core"], R["openblas_corename"]))

    # =========================================================================================
    # VECTOR: every census tract in the US
    # =========================================================================================
    zips = sorted(glob.glob("/tmp/tract/tl_2024_*_tract.zip"))
    check("tract_files", len(zips) == 56, "%d zips  want 56 (50 states, DC, PR, 4 island areas)" % len(zips))
    with clock("read_tracts"):
        parts = [gpd.read_file(z, columns=["GEOID", "ALAND", "AWATER"]) for z in zips]
        epsg = {p.crs.to_epsg() for p in parts}
        tr = pd.concat(parts, ignore_index=True)
        tr = gpd.GeoDataFrame(tr, geometry="geometry", crs=parts[0].crs)
        del parts
    check("tracts_read", len(tr) == 85529 and tr.GEOID.is_unique and (tr.GEOID.str.len() == 11).all()
          and epsg == {4269},
          "%d tracts, %d unique 11-digit GEOIDs, CRS %s  want 85529, all unique, EPSG:4269"
          % (len(tr), tr.GEOID.nunique(), sorted(epsg)))
    nv = int(shapely.get_num_coordinates(tr.geometry.values).sum())
    rec("tract_vertices", nv)
    obs("tract_vertices", "%s" % format(nv, ","))
    census = tr.ALAND.astype("int64").values + tr.AWATER.astype("int64").values

    # ---- 1. geodesic area against Census's published areas -----------------------------------
    geod = Geod(ellps="WGS84")
    with clock("geodesic_tracts"):
        ap = np.array([geod.geometry_area_perimeter(g) for g in tr.geometry.values])
    area, perim = np.abs(ap[:, 0]), ap[:, 1]
    resid = np.abs(area - census)
    # The bound is r-spatial-real's, for the same reason: Census publishes integer m^2 (1 m^2) from
    # its own coordinates; a coordinate-level difference moves a boundary and so scales with
    # PERIMETER (half a 1e-6 degree step of latitude, 0.0557 m, all the way round).
    bound = 1.0 + perim * 0.0557
    rel = resid / census
    check("geodesic_vs_census_every_tract", (resid <= bound).all(),
          "%d of %d tracts outside 1 m^2 + perimeter x 0.0557 m; worst at %.4f of the bound"
          % (int((resid > bound).sum()), len(tr), (resid / bound).max()))
    rec("geod_max_rel", float(rel.max()))
    rec("geod_median_rel", float(np.median(rel)))
    rec("geod_resid_over_bound_max", float((resid / bound).max()))
    obs("geodesic_vs_census_rel", "median %.3e  max %.3e; %d tracts over the fixture's flat 1e-5"
        % (np.median(rel), rel.max(), int((rel > 1e-5).sum())))
    tot = abs(area.sum() - census.sum()) / census.sum()
    rec("geod_total_rel", tot)
    check("total_area_conserved", tot < 1e-6, "sum rel err %.3e  want < 1e-6" % tot)

    # cross-code: terra::expanse() on the same bytes, same chip family (r-spatial-real)
    ta = pd.read_csv("/tmp/terra-tract-areas.csv", dtype={"GEOID": str})
    rec("terra_tract_areas_sha256", sha_file("/tmp/terra-tract-areas.csv"))
    ta = ta.set_index("GEOID").reindex(tr.GEOID)
    d = np.abs(area - ta.terra_m2.values.astype("float64"))
    # terra and PROJ both evaluate GeographicLib's geodesic polygon area on the WGS84 ellipsoid.
    # The claim is that they are interchangeable for every statement on this page, so the bound is
    # 1/100 of Census's own 1 m^2 resolution -- not a fit to how closely they happen to agree.
    check("pyproj_vs_terra_every_tract", ta.terra_m2.notna().all() and (d < 0.01).all(),
          "max |pyproj - terra| %.3e m^2 (%.3e rel), median %.3e m^2, %d identical in the printed "
          "1e-6 m^2  want < 0.01 m^2 on all %d" % (d.max(), (d / area).max(), np.median(d),
                                                  int((d < 5e-7).sum()), len(tr)))
    rec("pyproj_vs_terra_max_m2", float(d.max()))
    rec("pyproj_vs_terra_max_rel", float((d / area).max()))
    for nm, epsg_ in (("albers_5070", 5070), ("web_mercator_3857", 3857)):
        with clock("area_" + nm):
            a = tr.geometry.to_crs(epsg_).area.values
        r = np.abs(a - census) / census
        rec(nm + "_median_rel", float(np.median(r)))
        rec(nm + "_max_rel", float(r.max()))
        obs("  via " + nm, "median rel err %.3e  max %.3e" % (np.median(r), r.max()))
    pd.DataFrame({"GEOID": tr.GEOID, "census_m2": census,
                  "pyproj_m2": ["%.6f" % x for x in area]}).to_csv(OUT + "/tract-areas.csv", index=False)

    # ---- 2. tracts nest in counties: by area, by dissolve, by spatial join -------------------
    co = gpd.read_file("/tmp/tiger.zip")
    check("counties_read", len(co) == 3235 and co.crs.to_epsg() == 4269, "%d counties  want 3235" % len(co))
    key = tr.GEOID.str[:5].values
    check("tract_keys_are_counties", set(key) == set(co.GEOID), "%d county keys in tracts vs %d counties"
          % (len(set(key)), len(co)))
    with clock("geodesic_counties"):
        ca = np.array([abs(geod.geometry_area_perimeter(g)[0]) for g in co.geometry.values])
    cidx = pd.Series(np.arange(len(co)), index=co.GEOID.values)
    s = pd.Series(area).groupby(key).sum().reindex(co.GEOID).values
    add = np.abs(s - ca) / ca
    rec("additivity_max_rel", float(add.max()))
    check("tracts_sum_to_county_area", add.max() < 1e-12,
          "max rel |sum(tract) - county| %.3e over %d counties  want < 1e-12 (interior edges cancel)"
          % (add.max(), len(co)))
    with clock("dissolve_counties"):
        dis = tr[["geometry"]].assign(k=key).dissolve(by="k")
    da = np.array([abs(geod.geometry_area_perimeter(g)[0]) for g in dis.geometry.values])
    dr = np.abs(da - ca[cidx[dis.index].values]) / ca[cidx[dis.index].values]
    rec("dissolve_max_rel", float(dr.max()))
    # A dissolve of a clean coverage removes the interior edges and adds no vertex, so the county
    # it rebuilds has the county file's area to rounding. A 1 m sliver along a 100 km county edge
    # would already be ~1e-4 of a 1e9 m^2 county, so 1e-9 is a test of topology, not a fit.
    check("dissolve_rebuilds_counties", len(dis) == len(co) and dr.max() < 1e-9,
          "%d dissolved, max rel area err vs county file %.3e  want 3235, < 1e-9" % (len(dis), dr.max()))
    with clock("sjoin_point_in_county"):
        pts = gpd.GeoDataFrame({"tk": key}, geometry=tr.representative_point().values, crs=tr.crs)
        j = gpd.sjoin(pts, co[["GEOID", "geometry"]], predicate="within", how="left")
    nper = j.groupby(level=0).size().reindex(range(len(tr)), fill_value=0)
    good = int((j.GEOID == j.tk).groupby(level=0).all().reindex(range(len(tr)), fill_value=False)[nper == 1].sum())
    check("sjoin_recovers_geoid", (nper == 1).all() and good == len(tr),
          "%d tracts in exactly one county, %d of them the county their GEOID names  want %d"
          % (int((nper == 1).sum()), good, len(tr)))

    # ---- 3. contiguity at national scale, two independent routes -----------------------------
    from libpysal.weights import Queen, Rook
    with clock("queen_libpysal"):
        wq = Queen.from_dataframe(tr, use_index=False, silence_warnings=True)
    with clock("rook_libpysal"):
        wr = Rook.from_dataframe(tr, use_index=False, silence_warnings=True)
    pq, pr = pairs_of(wq), pairs_of(wr)
    rec("queen_links", len(pq)); rec("rook_links", len(pr))
    rec("queen_islands", len(wq.islands)); rec("rook_islands", len(wr.islands))
    obs("contiguity", "queen %d pairs (%d islands), rook %d pairs (%d islands), over %d tracts"
        % (len(pq), len(wq.islands), len(pr), len(wr.islands), wq.n))
    sym = all(i in wq.neighbors[j] for i in wq.neighbors for j in wq.neighbors[i])
    check("queen_symmetric", sym and wq.s0 == 2 * len(pq), "every link reciprocated, s0 = %d = 2 x %d"
          % (wq.s0, len(pq)))
    check("rook_subset_of_queen", pairset(pr) <= pairset(pq),
          "%d rook pairs, %d not queen pairs  want 0" % (len(pr), len(pairset(pr) - pairset(pq))))
    # Route B: GEOS. Queen contiguity is "the closures intersect"; rook is "the boundaries share a
    # line" (DE-9IM boundary/boundary dimension 1). Neither looks at vertices, so on a topologically
    # clean coverage they must reproduce libpysal's shared-vertex graph pair for pair.
    with clock("queen_geos_sjoin"):
        g1 = tr[["geometry"]].reset_index(drop=True)
        sj = gpd.sjoin(g1, g1, predicate="intersects")
        a_, b_ = sj.index.values.astype(np.int64), sj["index_right"].values.astype(np.int64)
        keep = a_ < b_
        gq = np.unique(np.c_[a_[keep], b_[keep]], axis=0)
    with clock("rook_geos_relate"):
        de9 = shapely.relate(tr.geometry.values[gq[:, 0]], tr.geometry.values[gq[:, 1]])
        de9 = np.asarray(de9, dtype=str)
    gr = gq[np.array([x[4] == "1" for x in de9], dtype=bool)]       # boundary/boundary is a line
    over = int(sum(x[0] != "F" for x in de9))                           # interior/interior meets
    rec("geos_queen_links", len(gq)); rec("geos_rook_links", len(gr)); rec("geos_interior_overlaps", over)
    check("no_overlapping_tracts", over == 0, "%d intersecting pairs whose interiors meet  want 0" % over)
    sq, sgq = pairset(pq), pairset(gq)
    check("queen_libpysal_eq_geos", sq == sgq, "libpysal %d, GEOS %d, only-libpysal %d, only-GEOS %d"
          % (len(sq), len(sgq), len(sq - sgq), len(sgq - sq)))
    sr, sgr = pairset(pr), pairset(gr)
    check("rook_libpysal_eq_geos", sr == sgr, "libpysal %d, GEOS %d, only-libpysal %d, only-GEOS %d"
          % (len(sr), len(sgr), len(sr - sgr), len(sgr - sr)))
    # Collapse the tract graph onto counties: two counties touch iff some tract of one touches some
    # tract of the other, because a county boundary IS its tracts' outer edges.
    with clock("county_contiguity"):
        cq = Queen.from_dataframe(co, use_index=False, silence_warnings=True)
        cr = Rook.from_dataframe(co, use_index=False, silence_warnings=True)
    kidx = cidx[key].values

    def quotient(p):
        a, b = kidx[p[:, 0]], kidx[p[:, 1]]
        m = a != b
        return set(map(tuple, np.sort(np.c_[a[m], b[m]], axis=1).tolist()))
    for nm, p, cw in (("queen", pq, cq), ("rook", pr, cr)):
        qs, cs = quotient(p), pairset(pairs_of(cw))
        rec("county_%s_links" % nm, len(cs))
        check("tract_%s_collapses_to_county" % nm, qs == cs,
              "%d county pairs from tracts, %d from the county file, %d differ  want 0"
              % (len(qs), len(cs), len(qs ^ cs)))
    eh = hashlib.sha256(pq.tobytes()).hexdigest()
    rec("queen_pairs_sha256", eh)

    # ---- 4. Moran's I and four OLS routes on 85 k tracts --------------------------------------
    from esda.moran import Moran
    from libpysal.weights import w_subset
    sel = np.where(tr.ALAND.astype("int64").values > 0)[0]   # log10(ALAND) needs land
    with clock("moran_subset"):
        ids = [int(i) for i in sel]
        while True:
            ws = w_subset(wq, ids, silence_warnings=True)
            isl = set(ws.islands)
            if not isl:
                break
            ids = [i for i in ids if i not in isl]
    ids = np.array(ids)
    ws.transform = "r"
    check("moran_weights_row_standardised", abs(ws.s0 - ws.n) < 1e-9 and not ws.islands,
          "n = %d tracts with land and a neighbour, s0 = %.9f, %d islands  want s0 = n, 0"
          % (ws.n, ws.s0, len(ws.islands)))
    y = np.log10(tr.ALAND.astype("float64").values[ids])
    with clock("moran"):
        m1 = Moran(y, ws, permutations=0)
        m2 = Moran(3.7 * y + 112.5, ws, permutations=0)
        m3 = Moran(-y, ws, permutations=0)
    rec("morans_I", m1.I)
    obs("morans_I", "%.12f  log10(ALAND) over %d tracts, z_norm %.3f" % (m1.I, ws.n, m1.z_norm))
    # Negation commutes exactly with every operation Moran's I performs, so this one is bit-exact.
    check("moran_sign_invariant", m1.I == m3.I, "|I(y) - I(-y)| = %.2e  want 0 exactly" % abs(m1.I - m3.I))
    # An affine map is exact in real arithmetic only: the centring and the two sums round
    # differently after scaling, so over n terms the identity holds to ~n x eps (~2e-11 here).
    check("moran_affine_invariant", abs(m1.I - m2.I) < 1e-10,
          "|I(y) - I(3.7y + 112.5)| = %.2e  want < 1e-10 (n x eps rounding)" % abs(m1.I - m2.I))
    check("moran_expectation", abs(m1.EI + 1.0 / (ws.n - 1)) < 1e-15, "EI = %.12e  want -1/(n-1)" % m1.EI)
    W = ws.sparse.tocsr()
    z = y - y.mean()
    Id = len(y) / W.sum() * (z @ (W @ z)) / (z @ z)
    check("moran_direct_sparse", abs(Id - m1.I) < 1e-10,
          "esda %.15f vs n/S0 z'Wz/z'z %.15f, |diff| %.2e  want < 1e-10" % (m1.I, Id, abs(Id - m1.I)))
    with clock("moran_999_permutations"):
        np.random.seed(20261019)
        mp = Moran(y, ws, permutations=999)
    rec("moran_p_sim", mp.p_sim); rec("moran_z_sim", mp.z_sim)
    obs("moran_permutations", "999 seeded: p_sim %.4f, z_sim %.3f, max permuted I %.4f"
        % (mp.p_sim, mp.z_sim, mp.sim.max()))

    import spreg
    import statsmodels.api as sm
    from sklearn.linear_model import LinearRegression
    lat = np.abs(shapely.get_y(tr.representative_point().values)[ids])
    X = np.c_[np.log10(perim[ids]), lat]
    with clock("ols_four_routes"):
        ols = sm.OLS(y, sm.add_constant(X)).fit()
        sk = LinearRegression().fit(X, y)
        beta = np.linalg.lstsq(np.c_[np.ones(len(y)), X], y, rcond=None)[0]
        sp = spreg.OLS(y.reshape(-1, 1), X, w=ws, spat_diag=True, moran=True)
    B = np.vstack([ols.params, np.r_[sk.intercept_, sk.coef_], beta, np.asarray(sp.betas).ravel()])
    spread = float(np.abs(B - B[0]).max())
    rec("ols_spread", spread)
    obs("ols_fit", "log10(ALAND) ~ log10(perimeter) + |lat|: %s  R2 %.9f" %
        (" ".join("%.9f" % b for b in B[0]), ols.rsquared))
    check("four_ols_routes_agree", spread < 1e-10,
          "max |diff| across statsmodels / sklearn / lstsq / spreg = %.2e  want < 1e-10 (conditioning)" % spread)
    mr = Moran(np.asarray(sp.u).ravel(), ws, permutations=0)
    dI = abs(mr.I - sp.moran_res[0])
    rec("resid_moran_I", mr.I)
    check("residual_moran_esda_eq_spreg", dI < 1e-10,
          "esda %.12f vs spreg %.12f on the OLS residuals, |diff| %.2e  want < 1e-10" % (mr.I, sp.moran_res[0], dI))

    del wq, wr, ws, sj, dis
    # =========================================================================================
    # RASTER: Colorado's DEM by tract, against terra's zonal table (r-spatial-real)
    # =========================================================================================
    import rasterio
    from osgeo import gdal
    from rasterio.features import rasterize
    tifs = sorted(glob.glob("/tmp/dem/Copernicus_DSM_COG_10_*_DEM.tif"))
    check("dem_tiles", len(tifs) == 28, "%d tiles  want 28" % len(tifs))
    vrt = os.path.join(WORK, "co.vrt")
    gdal.BuildVRT(vrt, tifs).FlushCache()
    with rasterio.open(vrt) as src:
        shp, tfm, dcrs = (src.height, src.width), src.transform, src.crs
        with clock("read_dem"):
            dem = src.read(1, masked=True)
    check("dem_mosaic", shp == (14400, 25200), "%d x %d  want 14400 x 25200" % shp)
    valid = ~np.ma.getmaskarray(dem)
    # Integer decimetres, so every sum below is exact in any order. terra's round() is std::round,
    # half away from zero. Two routes to the product: x10 in double is exact (a float32 times 10
    # needs at most 28 bits), and x10 rounded to float32 first, which is what an intermediate
    # stored as terra's default FLT4S holds.
    def rnd(x):
        return np.where(valid, np.sign(x) * np.floor(np.abs(x) + 0.5), 0.0)
    x = dem.filled(0).astype("float64") * 10.0
    di = rnd(x)                                             # round(v x 10), exactly
    rec("dem_half_cells", int(((np.abs(x) % 1.0) == 0.5)[valid].sum()))
    del x
    di32 = rnd((dem.filled(0) * np.float32(10)).astype("float64"))   # round(float32(v x 10))
    rec("dem_cells_f32_route_differs", int((di32 != di)[valid].sum()))
    del dem
    cot = tr[tr.GEOID.str[:2] == "08"].reset_index(drop=True)
    coc = co[co.STATEFP == "08"].reset_index(drop=True)
    check("colorado_units", len(cot) == 1447 and len(coc) == 64, "%d tracts, %d counties  want 1447, 64"
          % (len(cot), len(coc)))
    c0 = shapely.get_coordinates(cot.geometry.values)
    cotw, cocw = cot.to_crs(dcrs), coc.to_crs(dcrs)
    shift = float(np.abs(shapely.get_coordinates(cotw.geometry.values) - c0).max())
    rec("nad83_to_wgs84_max_shift_deg", shift)
    obs("nad83_to_wgs84_max_shift_deg", "%.3e  (terra/PROJ 9.9 in r-spatial-real: 8.003e-06)" % shift)

    def zonal(gdf, extra=False):
        z = rasterize(((g, i + 1) for i, g in enumerate(gdf.geometry.values)), out_shape=shp,
                      transform=tfm, fill=0, dtype="int32", all_touched=False).ravel()
        k = len(gdf) + 1
        # integer partial sums < 2^53, so float64 bincount is exact
        r = [np.bincount(z, weights=a.ravel(), minlength=k)[1:].astype(np.int64)
             for a in ((valid, di, di32) if extra else (valid, di))]
        return r
    with clock("zonal_tracts"):
        nt, st, st32 = zonal(cotw, extra=True)
    with clock("zonal_counties"):
        nc, sc = zonal(cocw)
    tk = cot.GEOID.str[:5].values
    an = pd.Series(nt).groupby(tk).sum().reindex(coc.GEOID).values
    ad = pd.Series(st).groupby(tk).sum().reindex(coc.GEOID).values
    check("tract_zones_nest_in_county_zones", (an == nc).all() and (ad == sc).all(),
          "%d of 64 counties differ in cells, %d in decimetre sums  want 0, 0"
          % (int((an != nc).sum()), int((ad != sc).sum())))
    rec("tract_cells", int(nt.sum())); rec("tract_dm_sum", int(st.sum()))
    tz = pd.read_csv("/tmp/terra-co-tract-zonal.csv", dtype={"GEOID": str}).set_index("GEOID").reindex(cot.GEOID)
    rec("terra_zonal_sha256", sha_file("/tmp/terra-co-tract-zonal.csv"))
    tn, ts = tz.cells.values.astype(np.int64), tz.dm_sum.values.astype("float64").astype(np.int64)
    dn, ds, ds32 = int((tn != nt).sum()), int((ts != st).sum()), int((ts != st32).sum())
    rec("terra_zonal_cells_differ", dn); rec("terra_zonal_dm_differ", ds); rec("terra_zonal_dm32_differ", ds32)
    rec("tract_dm32_sum", int(st32.sum()))
    # Membership: GDAL's cell-centre rule in both, on polygons PROJ moved identically.
    check("zonal_cells_rasterio_eq_terra", dn == 0 and int(nt.sum()) == 360398892,
          "%d of 1447 tracts differ in cell count; total %d  want 0, 360398892" % (dn, nt.sum()))
    # Values: terra's sums are reproduced by the float32-product route, tract for tract.
    check("zonal_dm_rasterio_eq_terra_f32", ds32 == 0 and int(st32.sum()) == 7541399513167,
          "%d of 1447 tracts differ via round(float32(v x 10)); total %d dm  want 0, 7541399513167"
          % (ds32, st32.sum()))
    obs("zonal_dm_exact_product", "round(v x 10) in double: %d of 1447 tracts differ from terra, total %d dm "
        "(%+d); %d of %d cells round differently" % (ds, st.sum(), int(st.sum()) - 7541399513167,
                                                    R["dem_cells_f32_route_differs"], int(valid.sum())))
    with clock("zonal_tracts_unprojected"):
        n83, _ = zonal(cot)
    obs("control_nad83_rasterized_raw", "%d of 1447 tracts move, by up to %d cells  "
        "(terra in r-spatial-real: 913, 61)" % (int((n83 != nt).sum()), int(np.abs(n83 - nt).max())))
    rec("control_tracts_moved", int((n83 != nt).sum()))
    pd.DataFrame({"GEOID": cot.GEOID, "cells": nt, "dm_sum": st}).to_csv(OUT + "/co-tract-zonal.csv", index=False)

    for f in ("tract-areas.csv", "co-tract-zonal.csv"):
        rec("sha256_" + f, sha_file(os.path.join(OUT, f)))
        obs("sha256 " + f, R["sha256_" + f])
    rec("ru_maxrss_gib", round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1048576, 2))
    rec("t_total", round(time.time() - T0, 1))
    obs("peak_rss", "%.2f GiB (ru_maxrss), %.0f s total" % (R["ru_maxrss_gib"], R["t_total"]))


if __name__ == "__main__":
    try:
        main()
    except Exception as e:  # report the failure, then fail
        import traceback
        traceback.print_exc()
        FAIL.append("exception: %s: %s" % (type(e).__name__, e))
    rec("failed", FAIL)
    json.dump(R, open(OUT + "/results.json", "w"), indent=1, sort_keys=True)
    open(OUT + "/smoke-check.txt", "w").write("\n".join(LINES) + "\n")
    if FAIL:
        print("FAILED: " + ", ".join(FAIL), flush=True)
        sys.exit(1)
    print("GEO-ML REAL OK", flush=True)
