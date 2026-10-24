---
tool: rasterio
tool_version: 1.5.0
env: earth-observation
image: quay.io/aarchsci/earth-observation@sha256:8e011f38aea1fa3ad1ce6b40208bf1e6afd69a0848f212b05236e01fbf1a5337
env_x86_64: earth-observation-51a38824432a
spawn_version: 0.140.0
last_verified: 2026-10-23
last_verified_x86_64: 2026-10-23
last_verified_amd: 2026-10-23
---
# Sentinel-2 L2A — recompute ESA's scene classification, and NDVI over the whole tile

Reads a real Sentinel-2 tile (120.6M pixels at 10 m, 30.1M at 20 m) and reproduces the depositors' published class percentages from the pixels. For anyone doing raster EO analysis on ARM.

## Run it

```bash
make stage RECIPE=earth-observation       # once: 450 MB, pinned by sha256
spawn task run --spec "$(make -s spec RECIPE=earth-observation)" --wait   # 7 s of compute
```

```python
with rasterio.open("SCL.tif") as s: scl = s.read(1)   # 5490² scene classification
counts = np.bincount(scl.ravel(), minlength=12)
100.0 * counts[4] / counts.sum()   # vegetation: 38.914997 — ESA published 38.914996
```

## Make it yours

| In the recipe | Swap for | What to know |
|---|---|---|
| scene `S2B_11SKA_20240704_0_L2A` (central California, 0.0005% cloud) | any Sentinel-2 L2A scene in `sentinel-cogs` | **stage its own `item.json` too** — the assertions read ESA's percentages from that file, so the reference travels with the scene. |
| B04 + B08 (red, NIR) | any band pair | NDVI's `[-1, 1]` bound is algebra, so it survives; the vegetation separation is specific to red/NIR. |
| reflectance = DN × 1e-4 | the item's `raster:bands` | **apply its `-0.1` offset only if `earthsearch:boa_offset_applied` is false.** When true (150 of this tile's 153 in 2024) the offset is already out of the DNs; applying it again puts 77.5% of this scene's vegetation at or below zero red reflectance. |
| the whole tile | a window (`rasterio.windows`) | windowing breaks the class percentages — they are defined over the full granule. |

**Leave the tile.** ESA computed those percentages over all 30,140,100 pixels and
`high_proba_clouds` is **3 of them**, so cropping trades the catalogue's strongest reference for a
number with nothing to check it against.

## Which box — [measured](../../measurements/earth-observation-real/README.md) (the tile's whole 2024: 153 scenes, 31.9 GB, 16 workers)

| chip | instance | wall | compute $ | **billed $/result** |
|---|---|---|---|---|
| Graviton4 | `r8g.4xlarge` | **100 s** | **0.026** | 0.075 |
| Intel | `r8i.4xlarge` | 140 s | 0.043 | **0.070** |
| AMD | `r8a.4xlarge` | 102 s | 0.036 | 0.078 |

The one-scene recipe itself fits `c8g.xlarge` (7 s of compute in a 102 s window). At scale **RAM picks the box** (~5 GiB per worker; an `m8g.4xlarge` hit its ceiling); boot leaves billed costs within 12%
([not compute cost](../../patterns/layout-and-effective-cost.md)); every output is byte-identical on all three.

<details>
<summary>As shipped: reproducing published percentages, two independent derivations, pins, run + verify</summary>

### The checks

| observable | assertion | Graviton4 | Intel, AMD |
|---|---|---|---|
| grid vs STAC item | shape + EPSG equal the item's `proj:shape` / `proj:epsg` | 10980² / 5490², EPSG:32611 | same |
| grids nest | 10 m is exactly 2× the 20 m grid, shared origin | yes (199980, 4100040) | same |
| SCL pixels | exactly 30,140,100 (5490²) | 30,140,100 | same |
| **class % vs ESA's own** | **every class ≤ 1× the float32 bound, 5e-7 + 4·2⁻²⁴·value** | **0.54×** (water) | same |
| **offset used is the right one** | **median red reflectance over ESA vegetation > 0** | **+0.0689** (−0.0311 with the −0.1 applied again) | same |
| NDVI range | within [−1, 1] by construction | [−0.9972, +0.9997] | same |
| **classifier vs index** | **p10(vegetation) > p90(not_vegetated)** | **+0.4788 > +0.3696** | same |
| water NDVI | mean < 0 (NIR absorbed) | **−0.2409** | same |

Intel (`c8i.xlarge`) and AMD (`c8a.xlarge`) ran the env's linux-64 twin: 256 of 261 versions
identical, rasterio 1.5.0 / GDAL 3.12.4 among them; c-blosc2, charls, deprecated and wrapt
differ by a patch or minor release, none on the GeoTIFF read path. Every line of the smoke
check, all eleven class counts and every NDVI statistic included, is identical on all three
chips; only the wall times differ.

**The headline is a reproduction, not a measurement.** The STAC item carries ESA's own
scene-classification percentages, computed by the L2A processor from the SCL band. Recomputing
them from the staged pixels reproduces all eleven to within **9.6e-07 percentage points**. The
bound is set by how ESA computes them, not by how close the run happened to land: `100·c/n` in
float32, printed to six decimals, so half the print ulp plus four float32 roundings —
`5e-7 + 4·2⁻²⁴·value`, the bound [the year's 1,764 values](../../measurements/earth-observation-real/README.md)
all satisfy (a flat 2e-6 fails 70 of its 153 scenes). This scene's worst is 0.54 of it. It is sensitive to a single pixel: `high_proba_clouds` is **3 px
of 30,140,100**, so one pixel moves the sixth decimal. The item is **staged and pinned**, so the
assertion compares against the depositors' file rather than numbers typed into a smoke check.

| | computed | ESA published | diff (pp) |
|---|---|---|---|
| vegetation | 38.914997 | 38.914996 | +9.6e-07 |
| not_vegetated | 60.315437 | 60.315436 | +9.1e-07 |
| water | 0.370875 | 0.370875 | −3.2e-07 |
| unclassified | 0.273861 | 0.273861 | +6.9e-08 |

| dark_features | 0.124359 | 0.124359 | +2.4e-07 |
| thin_cirrus | 0.000292 | 0.000292 | −3.0e-08 |
| medium_proba_clouds | 0.000169 | 0.000169 | +2.1e-07 |
| **high_proba_clouds** | **0.000010** (3 px) | **0.000010** | −4.7e-08 |
| cloud_shadow / snow_ice / nodata | 0.000000 | 0.000000 | 0 |

**R's terra reproduces these to the same digit**, from this same pinned `SCL.tif`:
[r-spatial](../r-spatial/README.md) gets `9.642e-07` pp worst over the class set, with vegetation
`+9.642e-07` and not_vegetated `+9.096e-07` — matching the rows above. It checks one class more
(code 1, `saturated_defective`, 0 px here), which the map below omits.

**Read it at the resolution it was defined at, or the comparison is about your method.** The same
band decimated 3× to 60 m gives vegetation **38.725880%** against the published 38.914996 — off by
0.19 pp, 200,000× the full-resolution residual. Nothing is wrong with either number; the
percentages are *defined* on the 20 m grid. A green check on the 60 m read would have been a
method difference wearing the reference's clothes ([the rule](../../practices/cross-checks.md)).

**The second check is a cross-validation, with no tolerance to pick.** ESA's SCL comes from a full
multi-spectral classifier; NDVI comes from two bands by a different route. Asserting that the 10th
percentile of ESA-classified vegetation is *greener than* the 90th percentile of what it called
bare — observed +0.4788 vs +0.3696, margin **+0.1092** — is an ordering, so there is no band to
tune. Mean NDVI by class, on the 20 m grid:

| class | pixels | mean | p10 | p90 |
|---|---|---|---|---|
| vegetation | 11,729,014 | +0.6609 | +0.4788 | +0.8551 |
| not_vegetated | 18,179,111 | +0.2065 | +0.0831 | +0.3696 |
| water | 111,660 | **−0.2409** | −0.7728 | +0.0083 |

NDVI is aggregated 2×2 **down** onto the classification grid rather than upsampling the classes:
averaging an index is defensible, inventing a classification for a 10 m pixel is not. A 20 m
pixel counts only where all four 10 m reflectances are positive.

**NDVI is from reflectance, and on this item that is the scale alone.** `raster:bands` declares
`scale 0.0001, offset −0.1`, but the item also carries `earthsearch:boa_offset_applied: true`:
Earth Search has already taken L2A's +1000 DN out of these COGs and left the offset in the
metadata. The task decides from the flag and then checks the choice against the bytes — vegetation
reflects some red, so its median red reflectance must be positive. It is +0.0689 here; applying
the declared −0.1 as well would make it −0.0311, with 77.5% of vegetation pixels at or below zero.

### Pins (data tier: RODA)

| | |
|---|---|
| image | `quay.io/aarchsci/earth-observation@sha256:8e011f38aea1…` (rasterio 1.5.0 / GDAL 3.12.4, cosign-signed, `linux/arm64`); x86_64: the locked twin `envs-x86_64/earth-observation-51a38824432a.linux-64.lock` |
| B04 (red, 10 m) | `sha256:8786ece1f55d…` — 224,839,341 B |
| B08 (NIR, 10 m) | `sha256:52030ffc4ba7…` — 220,607,414 B |
| SCL (20 m) | `sha256:258f36e59c6c…` — 2,571,146 B |
| `item.json` | `sha256:053ecac854fb…` — the STAC item, **the reference** |
| `scl_ndvi.py` | the task's script, pinned by sha256 in `01-cog.task.json` |

All four from `s3://sentinel-cogs/sentinel-s2-l2a-cogs/11/S/KA/2024/7/S2B_11SKA_20240704_0_L2A/`,
which is append-only, so the scene path is immutable. `stage-inputs.sh` verifies the sums *and*
re-reads the item to confirm it still declares the grids the task assumes and that its eleven
percentages still sum to 100 — a pin fixes bytes, not meaning.

### Run + verify

```sh
make stage RECIPE=earth-observation
spawn task run --spec "$(make -s spec RECIPE=earth-observation)" --wait
make ls RECIPE=earth-observation
```

The smoke check runs inside the task and the bucket listing is the second half of it
([exit 0 isn't proof](../../practices/container-path.md)). Expect `smoke-check.txt` with
`scl_reproduces_published` at or below 1× the bound (`runs/earth-observation/r3/`). Re-running overwrites the prefix; no spec edit needed.

</details>
