#!/usr/bin/env python3
"""stackstac and odc-stac on one pinned STAC item, against each other and against ESA.

The asset hrefs are rewritten to the local staged copies, so every read is of bytes the
earth-observation recipe already sha256-verified: offline, pinned, and still the real code path
(both libraries hand hrefs to rasterio). `links` is cleared too -- a bare local path gets
resolved against the item's base href, which silently produced
`s3://sentinel-cogs/tmp/SCL.tif` and an ObjectNotFound, so the rewrite uses a file:// URI.

THE HEADLINE IS A DISAGREEMENT, AND IT IS SILENT. Handed this item as-is, the two libraries
produce different grids:

  * the STAC projection extension v2 renamed `proj:epsg` to `proj:code`
  * pystac 1.15.2 migrates on parse, so `item.properties["proj:epsg"]` is None
  * stackstac 0.5.1 reads `proj:epsg`, finds nothing, and says
    "asset 'scl' of item 0 ... does not have one" -- which points at the data
  * supplying `epsg=` answers that complaint WITHOUT restoring the asset transform, so
    stackstac derives a grid from the item's lat/lon geometry and RESAMPLES: 5491x5491 at
    21.138368 m with 3.2M NaNs, no warning
  * odc-stac reads `proj:transform` directly and is unaffected

Restoring the key makes them agree on every one of 30,140,100 pixels. The wrong configuration
is kept below as the discrimination control: it is what shows the agreement is a result rather
than a coincidence.

Run with `python3 -u`.
"""
import json
import os
import sys

import numpy as np

out = {}


def rec(k, v):
    out[k] = v
    print("%s\t%s" % (k, v))


def dump():
    with open("/tmp/score.tsv", "w") as fh:
        fh.write("observable\tvalue\n")
        for k, v in out.items():
            fh.write("%s\t%s\n" % (k, v))


def die(msg):
    dump()
    sys.exit("FAIL: %s" % msg)


import odc.stac
import pystac
import pystac_client
import rioxarray
import stackstac
import xarray

for m in (stackstac, odc.stac, pystac, pystac_client, rioxarray, xarray):
    rec(m.__name__, getattr(m, "__version__", "unknown"))

RAW = json.load(open("/tmp/item.json"))
LOCAL = {"scl": "file:///tmp/SCL.tif", "coastal": "file:///tmp/B01.tif"}
EPSG = 32611
SCL_T = RAW["assets"]["scl"]["proj:transform"]
SCL_SHAPE = RAW["assets"]["scl"]["proj:shape"]
rec("item_id", RAW["id"])
rec("asset_scl_shape", SCL_SHAPE)
rec("asset_scl_transform", SCL_T[:6])


def build(restore_epsg=False):
    r = json.loads(json.dumps(RAW))
    for k, p in LOCAL.items():
        r["assets"][k]["href"] = p
    r["links"] = []
    it = pystac.Item.from_dict(r)
    if restore_epsg:
        it.properties["proj:epsg"] = EPSG
    return it


# ---- the key rename, measured rather than asserted from the changelog --------------------
probe = build()
rec("pystac_proj_keys", sorted(k for k in probe.properties if k.startswith("proj:")))
rec("proj_epsg_after_parse", repr(probe.properties.get("proj:epsg")))
rec("proj_code_after_parse", repr(probe.properties.get("proj:code")))
if probe.properties.get("proj:epsg") is not None:
    die("proj:epsg survived parsing -- the whole premise of this recipe has changed")
rec("identity_key_renamed",
    "pystac exposes proj:code=%r and no proj:epsg, which is why stackstac cannot find a CRS"
    % probe.properties.get("proj:code"))

# ---- odc-stac: the reference grid, read straight from proj:transform ---------------------
ds = odc.stac.load([build()], bands=["scl"], chunks={})
odc_arr = np.asarray(ds["scl"].squeeze().compute())
odc_t = tuple(ds.odc.geobox.transform)[:6]
rec("odc_shape", odc_arr.shape)
rec("odc_dtype", str(odc_arr.dtype))
rec("odc_transform", odc_t)
if list(odc_arr.shape) != SCL_SHAPE:
    die("odc-stac gave %s, the asset declares %s" % (list(odc_arr.shape), SCL_SHAPE))
if max(abs(a - b) for a, b in zip(odc_t, SCL_T[:6])) > 1e-9:
    die("odc-stac transform %s != the asset's %s" % (odc_t, SCL_T[:6]))
rec("identity_odc_native_grid",
    "odc-stac reproduced the asset's own %dx%d grid and transform exactly" % tuple(SCL_SHAPE))

# ---- the control: stackstac WITHOUT the restored key must differ -------------------------
bad = stackstac.stack([build()], assets=["scl"], epsg=EPSG, chunksize=2048)
rec("stackstac_unfixed_shape", tuple(bad.shape[-2:]))
rec("stackstac_unfixed_resolution", bad.spec.resolutions_xy)
if tuple(bad.shape[-2:]) == odc_arr.shape:
    die("the unfixed configuration now matches -- the control no longer discriminates, "
        "so stackstac may have learned proj:code and this recipe needs re-deriving")
rec("identity_silent_resample",
    "without proj:epsg, stackstac silently returns %dx%d at %.6f m instead of %dx%d at 20 m"
    % (bad.shape[-2], bad.shape[-1], bad.spec.resolutions_xy[0], SCL_SHAPE[0], SCL_SHAPE[1]))

# ---- route A: restore the key ------------------------------------------------------------
daA = stackstac.stack([build(restore_epsg=True)], assets=["scl"], chunksize=2048)
rec("routeA_shape", tuple(daA.shape[-2:]))
rec("routeA_resolution", daA.spec.resolutions_xy)
a = np.asarray(daA.squeeze().compute())
nan_a = int((~np.isfinite(a)).sum())
rec("routeA_nan_cells", nan_a)
if nan_a:
    die("route A produced %d NaN cells -- it is still resampling" % nan_a)
if a.shape != odc_arr.shape:
    die("route A shape %s != odc %s" % (a.shape, odc_arr.shape))
dA = int(np.abs(a.astype(np.int64) - odc_arr.astype(np.int64)).max())
rec("routeA_max_abs_diff_vs_odc", dA)
if dA != 0:
    die("route A differs from odc-stac by %d" % dA)
rec("identity_crosslib_A",
    "stackstac and odc-stac agree on all %d pixels, max abs diff 0" % a.size)

# ---- route B: pin the grid explicitly, touching no metadata ------------------------------
# A second, independent way to the native grid, so the agreement is not an artifact of one
# configuration. The dtype/fill_value/rescale triad took two tries and both errors are worth
# recording, because each rejects a request that looks reasonable:
#   rescale=True + dtype='uint16' -> "safe casting cannot be completed between asset scale
#     value 1 and output dtype uint16"; stackstac applies the raster extension's scale/offset
#     by default, so an integer dtype needs rescale=False
#   rescale=False + dtype='uint16', fill_value=0 -> "The fill_value 0 is incompatible with the
#     output dtype uint16. Either use dtype='int64'..." -- even though 0 is representable
# Following its own suggestion, int64 with a 0 fill is accepted.
x0, y0 = SCL_T[2], SCL_T[5]
bounds = (x0, y0 - 20.0 * SCL_SHAPE[0], x0 + 20.0 * SCL_SHAPE[1], y0)
rec("routeB_bounds", bounds)
daB = stackstac.stack([build()], assets=["scl"], epsg=EPSG, resolution=20, bounds=bounds,
                      snap_bounds=False, rescale=False, dtype="int64", fill_value=0,
                      chunksize=2048)
b = np.asarray(daB.squeeze().compute())
rec("routeB_shape", b.shape)
if b.shape != odc_arr.shape:
    die("route B shape %s != odc %s" % (b.shape, odc_arr.shape))
dB = int(np.abs(b.astype(np.int64) - odc_arr.astype(np.int64)).max())
rec("routeB_max_abs_diff_vs_odc", dB)
if dB != 0:
    die("route B differs from odc-stac by %d" % dB)
rec("identity_crosslib_B",
    "an explicitly pinned grid reaches the same pixels without touching metadata")

# ---- rioxarray: a third reader, and the fourth unexercised package -----------------------
rx = rioxarray.open_rasterio("/tmp/SCL.tif")
rx_arr = np.asarray(rx.squeeze())
rec("rioxarray_shape", rx_arr.shape)
rec("rioxarray_epsg", rx.rio.crs.to_epsg())
if rx.rio.crs.to_epsg() != EPSG:
    die("rioxarray reports EPSG:%s" % rx.rio.crs.to_epsg())
dR = int(np.abs(rx_arr.astype(np.int64) - odc_arr.astype(np.int64)).max())
rec("rioxarray_max_abs_diff_vs_odc", dR)
if dR != 0:
    die("rioxarray differs from odc-stac by %d" % dR)
rec("identity_three_readers",
    "stackstac, odc-stac and rioxarray return byte-identical pixels")

# ---- the external anchor: ESA's own published percentages --------------------------------
KEY = {0: "nodata_pixel", 1: "saturated_defective_pixel", 2: "dark_features",
       3: "cloud_shadow", 4: "vegetation", 5: "not_vegetated", 6: "water",
       7: "unclassified", 8: "medium_proba_clouds", 9: "high_proba_clouds",
       10: "thin_cirrus", 11: "snow_ice"}
props = RAW["properties"]
tot = odc_arr.size
worst = 0.0
print("  class  ESA published   loaded          diff (pp)")
for code, name in KEY.items():
    k = "s2:%s_percentage" % name
    if k not in props:
        die("the STAC item lacks %s" % k)
    pub = float(props[k])
    got = 100.0 * int((odc_arr == code).sum()) / tot
    worst = max(worst, abs(got - pub))
    print("  %5d  %13.6f  %13.6f  %+.3e" % (code, pub, got, got - pub))
rec("esa_classes_checked", len(KEY))
rec("esa_max_abs_diff_pp", "%.3e" % worst)
if worst >= 2e-6:
    die("ESA percentages differ by %.3e pp" % worst)
rec("identity_esa_percentages",
    "all %d of ESA's published class percentages reproduced to %.2e pp" % (len(KEY), worst))

# ---- 60 m band: both must honour an asset's own, different resolution --------------------
c_shape = RAW["assets"]["coastal"]["proj:shape"]
co = odc.stac.load([build()], bands=["coastal"], chunks={})["coastal"].squeeze().compute()
cs = stackstac.stack([build(restore_epsg=True)], assets=["coastal"]).squeeze().compute()
rec("coastal_odc_shape", tuple(np.asarray(co).shape))
rec("coastal_stackstac_shape", tuple(np.asarray(cs).shape))
if list(np.asarray(co).shape) != c_shape or list(np.asarray(cs).shape) != c_shape:
    die("the 60 m band came back at the wrong size; asset declares %s" % c_shape)
rec("identity_multiresolution",
    "both loaders read the 60 m band at its own %dx%d grid, not the 20 m one" % tuple(c_shape))

# ---- pystac-client: what IS checkable without a live API --------------------------------
os.makedirs("/tmp/cat", exist_ok=True)
cat = pystac.Catalog(id="pinned", description="the pinned item, wrapped for traversal")
cat.add_item(pystac.Item.from_dict(json.loads(json.dumps(RAW))))
cat.normalize_hrefs("/tmp/cat")
cat.save(catalog_type=pystac.CatalogType.SELF_CONTAINED)
client = pystac_client.Client.open("/tmp/cat/catalog.json")
got = list(client.get_items(recursive=True))
rec("pystac_client_items", len(got))
if len(got) != 1 or got[0].id != RAW["id"]:
    die("pystac-client returned %r, expected the pinned item" % [i.id for i in got])
conf = client.conforms_to("ITEM_SEARCH")
rec("pystac_client_conforms_item_search", conf)
if conf:
    die("a static catalog should not advertise ITEM_SEARCH")
try:
    client.search(collections=[RAW["collection"]])
    die("search() against a non-conformant root should raise, but did not")
except Exception as e:                                             # noqa: BLE001
    rec("pystac_client_search_raises", type(e).__name__)
    if type(e).__name__ != "DoesNotConformTo":
        die("search() raised %s, expected DoesNotConformTo" % type(e).__name__)
rec("identity_pystac_client",
    "traverses a static catalog to the pinned item, and refuses search() with DoesNotConformTo")
rec("observation_no_api_search",
    "REPORTED -- /search against a live STAC API is a runtime network dependency this recipe "
    "refuses, so CQL2 filtering and paging are not covered")

dump()
print("EO-STAC OK")
