# One Sentinel-2 L2A scene: reproduce ESA's scene-classification percentages from the SCL
# pixels, then NDVI from reflectance over the whole 10 m tile. Staged by stage-inputs.sh and
# pinned by sha256 in 01-cog.task.json (kept out of the spec: spawn's wrapper fills most of
# EC2's 16 KiB user data, spawn#1246).
import json, sys, time
import numpy as np
import rasterio

fail = []
def check(label, ok, detail):
    print(f"{label:30s} {detail}")
    if not bool(ok): fail.append(label)
def obs(label, detail):
    print(f"{label:30s} {detail}   [observation]")
def blocksum(a):
    """2x2 block sum by four elementwise adds: the same IEEE operations on every chip."""
    return a[0::2, 0::2] + a[1::2, 0::2] + a[0::2, 1::2] + a[1::2, 1::2]

item = json.load(open("/tmp/item.json"))
props, assets = item["properties"], item["assets"]
obs("rasterio_version", f"{rasterio.__version__}  GDAL {rasterio.__gdal_version__}")
obs("scene", f"{item['id']}  {props['datetime']}  cloud {props['eo:cloud_cover']}%")

# ---- the grid matches what the depositors published -------------------------------
t0 = time.time()
with rasterio.open("/tmp/B04.tif") as s:
    red = s.read(1); red_shape, red_tr, red_crs = s.shape, s.transform, s.crs
with rasterio.open("/tmp/B08.tif") as s:
    nir = s.read(1); nir_shape = s.shape
t_read = time.time() - t0
with rasterio.open("/tmp/SCL.tif") as s:
    scl = s.read(1); scl_shape = s.shape

check("grid_matches_published",
      list(red_shape) == assets["red"]["proj:shape"] == assets["nir"]["proj:shape"]
      and list(scl_shape) == assets["scl"]["proj:shape"]
      and red_crs.to_epsg() == props["proj:epsg"],
      f"red/nir {red_shape} scl {scl_shape} EPSG:{red_crs.to_epsg()}"
      f"  want the item's proj:shape and proj:epsg")
# The 2x2 aggregation below is exact only on an integer-factor, co-origined grid.
check("grids_nest_exactly",
      red_shape == nir_shape and red_shape[0] == 2 * scl_shape[0]
      and red_shape[1] == 2 * scl_shape[1]
      and red_tr.c == 199980.0 and red_tr.f == 4100040.0,
      f"10 m is exactly 2x the 20 m grid on a shared origin "
      f"({red_tr.c:.0f}, {red_tr.f:.0f})")

# ---- reproduce ESA's own scene-classification percentages -------------------------
SCL = {0: "nodata_pixel", 2: "dark_features", 3: "cloud_shadow", 4: "vegetation",
       5: "not_vegetated", 6: "water", 7: "unclassified",
       8: "medium_proba_clouds", 9: "high_proba_clouds", 10: "thin_cirrus",
       11: "snow_ice"}
counts = np.bincount(scl.ravel(), minlength=12)
total = int(counts.sum())
check("scl_pixels", total == 5490 * 5490, f"{total:,}  want 30,140,100 (5490^2)")
# ESA's denominators, measured on 153 scenes: a class over the non-nodata pixels, nodata over
# the whole grid. On a full tile (nodata 0) the two are the same number.
valid20 = total - int(counts[0])

# ESA evaluates 100*c/n in float32 and prints 6 decimals, so the bound is half the print ulp
# plus the four float32 roundings of that expression (c, n, the divide, the scale):
# 5e-7 + 4 * 2^-24 * value. Held on all 1,764 published values of tile 11SKA's 2024
# (measurements/earth-observation-real); a flat 2e-6 fails on 70 of those 153 scenes.
worst_class, worst, npub = None, 0.0, 0
for code, name in sorted(SCL.items()):
    pub = props.get(f"s2:{name}_percentage")
    if pub is None:   # processing baseline 05.11 publishes no dark_features_percentage
        continue
    npub += 1
    denom = total if code == 0 else valid20
    got = 100.0 * counts[code] / denom if denom else 0.0
    bound = 5e-7 + 4 * 2.0 ** -24 * abs(pub)
    ratio = abs(got - pub) / bound
    if ratio > worst:
        worst_class, worst = name, ratio
    obs(f"  {name}", f"{counts[code]:>9,} px  {got:.6f}%  published {pub:.6f}%  "
                     f"d={got-pub:+.2e}  ({ratio:.2f} of the bound)")
check("scl_reproduces_published", worst <= 1.0,
      f"worst |computed - published| = {worst:.2f} x the bound over {npub} classes "
      f"(worst: {worst_class})  want <= 1, bound = 5e-7 + 4 x 2^-24 x value")

# ---- NDVI from reflectance over the full 10 m scene ---------------------------------
# Reflectance = DN * scale + offset. raster:bands declares 0.0001 and -0.1, but this item also
# says earthsearch:boa_offset_applied: true -- Earth Search already removed L2A's +1000 DN from
# these COGs and left the -0.1 in raster:bands. Applying it again subtracts 0.1 twice, so the
# offset used is 0 when the flag is true and the declared one otherwise.
t1 = time.time()
rb = assets["red"]["raster:bands"][0]
nb = assets["nir"]["raster:bands"][0]
applied = bool(props.get("earthsearch:boa_offset_applied", False))
r_off = 0.0 if applied else float(rb.get("offset", 0.0))
n_off = 0.0 if applied else float(nb.get("offset", 0.0))
obs("red/nir scale/offset declared", f"{rb.get('scale')}/{rb.get('offset')}  {nb.get('scale')}/{nb.get('offset')}")
obs("boa_offset_applied", f"{applied}  -> offset used {r_off} / {n_off}")
valid = (red != 0) & (nir != 0)
cls10 = lambda code: np.broadcast_to((scl == code)[:, None, :, None],
                                     (scl_shape[0], 2, scl_shape[1], 2)).reshape(red_shape) & valid
water10 = cls10(6)
nw10 = int(water10.sum())
# The sign of NIR - red does not depend on the offset: water absorbs NIR.
nw_lt = int((nir[water10] < red[water10]).sum())
del water10
# Which offset is right is a property of the bytes, so check it on them: vegetation reflects
# some red, so its median red reflectance must be > 0. Subtracting 0.1 twice puts it below 0.
veg10 = cls10(4)
vred = red[veg10].astype(np.float64) * float(rb.get("scale", 1.0))
del veg10
med_used = float(np.median(vred + r_off))
med_declared = float(np.median(vred + float(rb.get("offset", 0.0))))
neg_declared = float((vred + float(rb.get("offset", 0.0)) <= 0).mean())
del vred
check("veg_red_reflectance_positive", med_used > 0,
      f"median red reflectance over ESA vegetation = {med_used:+.4f} with the offset used  want > 0")
obs("  with the declared -0.1 too", f"median {med_declared:+.4f}; red <= 0 on {100*neg_declared:.1f}% of vegetation px")
rf = red.astype(np.float32); rf *= np.float32(rb.get("scale", 1.0)); rf += np.float32(r_off)
nf = nir.astype(np.float32); nf *= np.float32(nb.get("scale", 1.0)); nf += np.float32(n_off)
del red, nir
valid &= rf > 0
valid &= nf > 0
ndvi = nf - rf
nf += rf
del rf
np.divide(ndvi, nf, out=ndvi, where=valid)
ndvi[~valid] = np.nan
del nf
t_ndvi = time.time() - t1
nv = int(valid.sum())
# With both reflectances positive, (NIR-R)/(NIR+R) is in [-1, 1] by algebra -- not a band.
vmin, vmax = float(np.nanmin(ndvi)), float(np.nanmax(ndvi))
check("ndvi_in_range", -1.0 <= vmin and vmax <= 1.0,
      f"[{vmin:.4f}, {vmax:.4f}] over {nv:,} defined px  want within [-1, 1]")

# Aggregate onto the 20 m classification grid, only where all four 10 m pixels are defined.
# Averaging the index down is the defensible direction; upsampling the CLASS would invent
# classifications.
four = blocksum(valid.astype(np.uint8)) == 4
agg = blocksum(np.where(valid, ndvi, np.float32(0))) * np.float32(0.25)
del ndvi, valid

def stats(code):
    m = (scl == code) & four
    n = int(m.sum())
    if not n: return (int((scl == code).sum()), 0, None, None, None)
    v = agg[m]
    return (int((scl == code).sum()), n, float(v.mean(dtype=np.float64)),
            float(np.percentile(v, 10)), float(np.percentile(v, 90)))

veg, nveg, water = stats(4), stats(5), stats(6)
for nm, st in (("vegetation", veg), ("not_vegetated", nveg), ("water", water)):
    if st[1]:
        obs(f"  ndvi[{nm}]", f"defined on {st[1]:,} of {st[0]:,} px  mean={st[2]:+.4f} "
                            f"p10={st[3]:+.4f} p90={st[4]:+.4f}")
    else:
        obs(f"  ndvi[{nm}]", f"defined on 0 of {st[0]:,} px")

# Cross-validation, not a band: ESA's SCL comes from a full multi-spectral classifier and
# NDVI from two bands by a different route. An ordering of disjoint percentiles has no
# tolerance to pick. Holds on this scene; at scale it is a tendency, not a law.
check("classifier_agrees_with_index", veg[3] > nveg[4],
      f"p10(vegetation)={veg[3]:+.4f} > p90(not_vegetated)={nveg[4]:+.4f} "
      f"(margin {veg[3]-nveg[4]:+.4f})  want the distributions to separate")
# Water absorbs NIR almost completely, so NDVI over water is negative. Physics.
check("water_ndvi_negative", water[1] > 0 and water[2] < 0,
      f"mean NDVI over ESA-classified water = {water[2] if water[2] is None else format(water[2], '+.4f')}  want < 0 (NIR absorbed)")
obs("water: NIR < red", f"{nw_lt:,} of {nw10:,} ESA-water 10 m px ({100.0*nw_lt/nw10:.1f}%)")

obs("wall_read_2x10980sq", f"{t_read:.1f}s for 2 x 120,560,400 uint16")
obs("wall_ndvi", f"{t_ndvi:.1f}s over 120,560,400 px")

if fail:
    print("FAILED: " + ", ".join(fail)); sys.exit(1)
