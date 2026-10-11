---
tool: stackstac-odc-stac
tool_version: "stackstac 0.5.1 / odc-stac 0.5.3"
env: earth-observation
image: quay.io/aarchsci/earth-observation@sha256:e5e8125b307e66301efe15fc3e4340462a112968e299b30fe9421f873688b91f
spawn_version: 0.126.1
last_verified: 2026-10-10
---
# stackstac + odc-stac — two STAC loaders, one pinned item, and a silent 5% grid error

Loads the same pinned Sentinel-2 STAC item with both libraries on Graviton4 and checks they return the same pixels. For anyone building STAC pipelines on ARM.

## Run it

```bash
make stage RECIPE=eo-stac     # once: the checks only — the scene is already staged
spawn task run --spec "$(make -s spec RECIPE=eo-stac)" --wait
make ls RECIPE=eo-stac

item = pystac.Item.from_dict(raw)          # raw["assets"]["scl"]["href"] = "file:///tmp/SCL.tif"
item.properties["proj:epsg"] = 32611       # pystac renamed it to proj:code; stackstac reads epsg
stackstac.stack([item], assets=["scl"])    # 5490x5490 @ 20 m — WITHOUT that line, 5491 @ 21.14 m
odc.stac.load([item], bands=["scl"], chunks={})     # 5490x5490 @ 20 m, always
```

## Make it yours

| In the recipe | Swap for | What to know |
|---|---|---|
| `properties["proj:epsg"] = 32611` | nothing | **do not drop this.** Without it stackstac silently resamples to 21.138368 m with 3.2 M NaNs and raises nothing. The single most important line here. |
| `file:///tmp/...` hrefs | a bare path | a path without a scheme is resolved **against the item's base href** — `/tmp/SCL.tif` became `s3://sentinel-cogs/tmp/SCL.tif` and failed with `ObjectNotFound`. Clear `links` too. |
| the pinned local scene | `s3://sentinel-cogs/...` | the hrefs point there already; rewriting them to the staged copies makes the test offline and sha256-verified, and exercises the same code path. |
| `dtype`/`fill_value`/`rescale` | — | fiddly: `rescale=True` refuses an integer dtype, then `fill_value=0` is refused for `uint16`. See below. |
| pystac-client | a live STAC API | **`/search` is not covered** — it needs a network dependency this recipe refuses. What *is* checked is that it traverses a static catalog and correctly refuses `search()`. |

**Leave the fixture.** It is the same scene two other recipes verify against ESA's published numbers, so this one inherits an external anchor for free. **Scale it** to more items by passing a list — both libraries take one; nothing else changes.

## Shape, size, cost

One task on `m8g.xlarge` (4 vCPU / 16 GiB), TTL 40m as a **backstop** with `cost_limit` $0.20 as the real guard. The analysis is **11 s** of a 1m11s window — 37 s installing Docker, 21 s pulling the image, 2 s staging 8.8 MB ([layout](../../patterns/layout-and-effective-cost.md)).

<details>
<summary>As shipped: three readers byte-identical on 30.1 M pixels, a silent resample as the control, and three sharp edges where the error blames the wrong thing</summary>

### The checks

| observable | assertion | observed |
|---|---|---|
| staged bytes | match pin | 1 of 1 |
| shared scene | sha256 equals earth-observation's pins | 3 of 3 |
| stackstac / odc-stac | read from the running install | **0.5.1** / 0.5.3 |
| pystac / pystac-client / rioxarray / xarray | — | 1.15.2 / 0.9.0 / 0.23.0 / 2026.9.0 |
| **`proj:epsg` after pystac parses** | **is `None`** | `None`; only `proj:code` survives |
| odc-stac grid | equals the asset's own `proj:shape`/`proj:transform` | 5490², `(20,0,199980,0,-20,4100040)` |
| **stackstac, unfixed** | **must DIFFER from odc-stac** | **5491² @ 21.138368 m** |
| route A — restore `proj:epsg` | 0 NaN, and max abs diff **== 0** | 5490² @ 20 m, **0** |
| route B — pin the grid explicitly | max abs diff **== 0** | **0** |
| rioxarray on the same file | EPSG 32611, max abs diff **== 0** | **0** |
| **three readers** | **byte-identical** | **30,140,100 pixels** |
| **ESA's class percentages** | **max\|diff\| < 2e-6 pp over 12** | **9.642e-07 pp** |
| 60 m band, both loaders | its own 1830², not the 20 m grid | 1830² / 1830² |
| pystac-client on a static catalog | returns exactly the pinned item | 1 item, id matches |
| `conforms_to("ITEM_SEARCH")` | **False** | False |
| `search()` | **raises `DoesNotConformTo`** | DoesNotConformTo |
| live-API `/search` | *reported, not covered* | — |

### The headline is a disagreement, and the wrong answer is the quiet one

Handed this item as it ships, the two libraries return different grids. Every link in the chain is
a component behaving reasonably:

1. the STAC projection extension v2 renamed `proj:epsg` to `proj:code`
2. **pystac 1.15.2 migrates on parse**, so `item.properties["proj:epsg"]` is `None` and only
   `proj:code = 'EPSG:32611'` remains
3. **stackstac 0.5.1 reads `proj:epsg`**, finds nothing, and reports
   `Cannot pick a common CRS, since asset 'scl' of item 0 ... does not have one` — which points at
   the data. The data is fine.
4. supplying the `epsg=` it asks for answers that complaint **without** restoring the asset's
   *transform*, so stackstac derives a grid from the item's lat/lon geometry and **resamples**
5. odc-stac reads `proj:transform` directly and is unaffected

```text
                      shape          resolution              origin                dtype
asset declares     5490 x 5490        20 m            199980, 4100040            uint8
odc-stac           5490 x 5490        20 m            199980, 4100040            uint8
stackstac, epsg=   5491 x 5491   21.138368 m      196121.78, 4103230.10   float64, 3.2M NaN
```

A **5% resolution error and a 3.9 km origin shift, with no warning.** A reader hitting step 3 would
reasonably conclude their item was malformed and start editing data that is correct.

Restoring one key fixes it: `item.properties["proj:epsg"] = 32611` gives 5490² at 20 m, **zero
NaN**, and **max abs diff 0** against odc-stac across all 30,140,100 pixels.

**The unfixed configuration is kept as the discrimination control.** The run asserts that it
*differs* — so if stackstac later learns `proj:code`, the control stops discriminating and the
recipe fails loudly rather than quietly asserting a tautology.

### Three readers, and why that is not as strong as it sounds

route A (stackstac with the key restored), route B (stackstac with `epsg`/`resolution`/`bounds`
pinned and no metadata touched), odc-stac, and rioxarray all return **byte-identical** pixels. Two
independent stackstac configurations reaching the same answer is what shows route A is not an
artifact of one call signature.

But all four decode through **GDAL**, so this is not four independent implementations of the format
— it is four independent *grid-assembly* paths over one decoder, which is exactly where these
libraries differ and exactly where the bug above lives. The external anchor is what makes it mean
something: all four reproduce **ESA's own published class percentages** from the STAC item to
**9.642e-07 pp**, the same value [earth-observation](../earth-observation/README.md) gets with
rasterio and [r-spatial](../r-spatial/README.md) gets with terra. The bound is set by ESA printing
6 decimals, not by where the run landed.

### Two more sharp edges, both worth the words

**A bare local href is resolved against the item's base.** Rewriting `assets["scl"]["href"]` to
`/tmp/SCL.tif` produced `ObjectNotFound: s3://sentinel-cogs/tmp/SCL.tif` — the loader joined the
path to the item's remote root while the file sat on local disk. The fix is a `file://` URI, and
clearing `links` so nothing can resolve relatively.

**stackstac's `dtype`/`fill_value`/`rescale` triad rejects reasonable requests, twice.** Asking for
an integer dtype gives `safe casting cannot be completed between asset scale value 1 and output
dtype uint16`, because the raster extension's scale/offset is applied by default; adding
`rescale=False` then gives `The fill_value 0 is incompatible with the output dtype uint16`, though
0 is obviously representable. Its own suggestion — `dtype='int64'` — is accepted.

### What pystac-client can and cannot be held to

Its distinctive feature is `/search` against a live STAC API with CQL2 filtering, and that is a
**runtime network dependency this recipe refuses**. So the page does not pretend to cover it.

What is checked is real and passes: `Client.open()` on a static catalog traverses to exactly the
pinned item, `conforms_to("ITEM_SEARCH")` is `False`, and `search()` **raises
`DoesNotConformTo`** rather than attempting a request against a root that cannot serve one. A
client that silently tried would be the bug; that it refuses is the assertion. It also warns
`NoConformsTo` and `FallbackToPystac` on the way, which is correct and appears in the log.

### Pins

| | |
|---|---|
| SCL (20 m) | `inputs/earth-observation/SCL.tif`, `sha256:258f36e59c6c…` (2,571,146 B) — **read, not copied** |
| B01 (60 m) | `inputs/earth-observation/B01.tif`, `sha256:8626d4bb645a…` (6,192,186 B) — read, not copied |
| STAC item | `inputs/earth-observation/item.json`, `sha256:053ecac854fb…` (22,880 B) — **the reference** |
| checks | `identities.py`, pinned by sha256 |
| image | `quay.io/aarchsci/earth-observation@sha256:e5e8125b307e…` — stackstac 0.5.1, odc-stac 0.5.3, pystac 1.15.2, pystac-client 0.9.0, rioxarray 0.23.0, rasterio 1.5.2 / GDAL 3.13.3 |

Staging confirms the item still carries `proj:epsg` **in its raw JSON** — the recipe's premise is
that pystac renames it on parse, so if upstream ever ships `proj:code` directly the premise has
changed and staging says so rather than the check quietly passing. It also confirms both asset
transforms and all 12 class percentages are still present, since those are the grid the loaders are
held to and the anchor they are measured against.

cosign-verified against `playgroundlogic/aarchsci`; the signature covers the **manifest-list**
digest, so verify the tag and pin the arm64 digest.

### Run + verify

```sh
make stage RECIPE=eo-stac
spawn task run --spec "$(make -s spec RECIPE=eo-stac)" --wait
aws s3 cp "s3://$(make -s print-bucket)/runs/eo-stac/r1/score.tsv" -
```

Fails on a pin mismatch, a `proj:epsg` that survives parsing, an odc-stac grid that is not the
asset's own, an unfixed stackstac that no longer differs, any reader disagreeing by a single count,
an ESA percentage off by 2e-6 pp, a 60 m band loaded at the wrong size, or a `search()` that does
not refuse — but check the bucket regardless
([exit 0 isn't proof](../../practices/container-path.md)).

The log ends with a bare `Error in sys.excepthook:` after `EO-STAC OK`. That is interpreter
teardown after a successful exit — `score.tsv` is complete and the exit code is 0 — not a failure.

### Not covered

**`/search` against a live STAC API**, with CQL2 filtering, paging and datetime queries — the
reason most people install pystac-client. Covering it needs either a recorded API-response fixture
or a runtime network dependency; the second is refused, and the first is worth doing only if the
fixture can be pinned meaningfully.

Also: **multi-item mosaicking and temporal stacks**, which is stackstac's and odc-stac's actual
purpose — one item exercises the grid logic but not compositing, `groupby`, or cloud-masked medians.
**Reprojection to a different CRS**, which here would mean asserting a resampling result rather
than an identity. **Dask at scale** — `chunks={}` and a 2048 chunksize on one box says nothing about
distributed reads. **`odc-geo`'s geobox algebra**, and the 10 m bands, which are staged but 440 MB
and would need a bounded window rather than a full-scene load.

</details>
