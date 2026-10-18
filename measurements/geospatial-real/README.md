# GDAL on a real workload: the whole state of Colorado at 30 m, Graviton4 vs Intel vs AMD

> **A state-wide DEM is reprojected, sloped and hillshaded in under 30 s on every chip, and
> every reprojection identity holds at 299 million pixels on all three.** The one that does not
> is `gdalwarp -r sum`: on a lat/lon source, Intel and AMD lose **0.27% of the total**. Graviton4
> conserves it only by floating-point luck. The cause is GDAL's, and one option fixes it on all
> three chips. Graviton4 is the cheapest per result billed, AMD the fastest.

Workload: the 28 Copernicus GLO-30 one-degree tiles covering Colorado's lat/lon rectangle
(37–41 °N, 103–109 °W; 362,880,000 px, 1.05 GB of COGs). The run is the first thing a terrain study
does with them, in four steps:
1. mosaic (`gdalbuildvrt`);
2. reproject to UTM 13N at 30 m with bilinear resampling (20,766 × 15,054 px);
3. `gdaldem slope`;
4. `gdaldem hillshade`.

A state or a river basin is the unit people prepare a DEM for. A continent is the same steps
fanned out by tile, not a different workload.

## Run it

```sh
export AWS_PROFILE=aws
bash measurements/geospatial-real/stage-inputs.sh "$COOKBOOK_BUCKET"
for a in arm64 x86_64 amd; do
  spawn task run --spec "$(python3 scripts/spec_arch.py geospatial $a "$COOKBOOK_BUCKET" "$(date +%s)" measurements/geospatial-real/colorado.task.json)" --wait
done
```

One task per chip runs `real.py`, which is pinned by sha256. The Intel and AMD legs use the
recipe's `geospatial-07484bd0bf42` linux-64 twin: GDAL 3.12.4, PROJ 9.8.1, numpy 2.5.2 on all
three. `sumdiag.py` (`sumdiag*.task.json`) is the `-r sum` diagnosis below.

## Results

16 vCPU, one `c8?.4xlarge` per chip, 16 GDAL threads, same versions, same input bytes:

| chip | instance | warp / slope / hillshade | real.py wall | billed | $/hr | compute $/result | **billed $/result** | peak RSS |
|---|---|---|---|---|---|---|---|---|
| Graviton4 | `c8g.4xlarge` | 3.9 / 11.7 / 13.9 s | 158 s | 286 s | 0.6381 | 0.0280 | **0.0507** | 18.7 GiB |
| Xeon 6975P-C | `c8i.4xlarge` | 5.4 / 9.9 / 12.2 s | 166 s | 286 s | 0.7497 | 0.0346 | **0.0596** | 18.7 GiB |
| EPYC 9R45 | `c8a.4xlarge` | 3.1 / 8.4 / 10.5 s | 123 s | 222 s | 0.8622 | 0.0295 | **0.0532** | 18.9 GiB |

A *result* is the workload plus every check below. *real.py wall* is all of it; *billed* runs
from launch to terminate. Threads per core: 1 on Graviton4 and AMD, 2 on Intel (its 16 vCPU are 8
cores). OpenBLAS: `neoversev2` on Graviton4, and `Cooperlake` on both x86 chips (0.3.34 has no
Zen 5 kernel, so AMD falls back to it).

- **The GDAL workload is about 30 s on every chip**, and AMD is fastest at 22 s. The multi-threaded
  warp takes 3–5 s. The two `gdaldem` steps are single-threaded and take the rest, 8–14 s each.
  Intel's 2-threads-per-core layout costs it on the threaded warp (5.4 s) but not on the serial
  steps.
- **Graviton4 is cheapest per result**, billed ($0.051 vs $0.053 AMD and $0.060 Intel) and on
  compute alone. AMD's 1.35× hourly price is mostly repaid by its 1.28× faster run, which leaves it
  5% dearer. Intel is 18% dearer.
- **Most of the bill is not GDAL.** About 2 minutes per chip is boot, Docker, image pull (28 s for
  the arm64 aarch.science image, 3 s for micromamba plus a 1–2 s offline env install on x86) and
  staging 1.05 GB (18–23 s). About 2 minutes is the checks, which read every output pixel back in
  numpy and pyproj. **These timings are not compute cost**
  ([layout](../../patterns/layout-and-effective-cost.md)).
- **The 18.7 GiB peak is the checks, not GDAL.** They hold the float32 mosaic and a float64 copy
  for the block sums. GDAL itself streams its chunks within `-wm 1024`. On a real terrain job
  without the numpy checks, a `c8g.2xlarge` would hold the 1 GB of staged COGs in its 8 GiB tmpfs
  easily ([data-movement](../../patterns/data-movement.md)). That size was not run.
- n = 1 per cell. Every output is deterministic: a repeat of the `-r sum` warp on each chip gave
  the same bytes (`sumdiag1.json`).

## What held at scale, on all three chips

| claim | why it must hold | Graviton4 | Intel | AMD |
|---|---|---|---|---|
| 28 tiles match sha256 pins | provenance | 28/28 | 28/28 | 28/28 |
| PROJ 9.8.1 `test/gie`: 6,191 vectors, 0 failed, 0 skipped | upstream's committed reference | ok | ok | ok |
| 28 tiles → 14,400 × 25,200, each on a whole-pixel offset | the mosaic is a concatenation, not a resample | ok | ok | ok |
| `-r near -et 0`: valid exactly where the pyproj inverse lands in the mosaic | coverage | 299,129,688 px, 0 disagree | same | same |
| `-r near -et 0`: each output px == the source px pyproj's inverse predicts | nearest is a lookup | **299,129,688 of 299,129,688** exact | same | same |
| 32613 → 4326 → 32613 round-trip, every 8th chunk | inverse ∘ forward = identity | 3.7e-09 m | 3.7e-09 m | 3.7e-09 m |
| `gdaldem slope` == Horn's formula in float64 numpy, all interior px | cross-code identity | 9.70e-04° over 299,058,669 px | same | same |
| slope nodata exactly where a 3×3 neighbour is invalid | edge handling | 0 disagree | 0 | 0 |
| control: Zevenbergen–Thorne instead of Horn | the slope check has teeth | misses by 10.26° | same | same |
| **`-r sum` on an exact 10× grid == block sums (< 1e-9 relative)** | **sum-preserving** | **3.9e-12** | **1.6e-02: FAIL** | **1.6e-02: FAIL** |
| **sum(out)/sum(in) = 1 to 1e-9** | **conservation** | **1.000000000000** | **0.997324: FAIL** | **0.997324: FAIL** |

Bounds:
- **`< 1e-9` relative on `-r sum`.** 1/3600° is not representable in binary, so honest overlap
  weights are 1 ± ~1e-13. A wrong kernel is off by O(1).
- **`< 5e-3°` on slope.** Float32 elevations near 4,400 m carry a ~5e-4 m ulp, so six float32 adds
  over Horn's 240 m baseline can reach ~1.4e-3° if GDAL works in float32. The control is 2,000× the
  bound.
- **`< 1e-6 m` on the round-trip**, the recipe's own bound.

The mosaic spans 999.6–4,395.7 m. Mt Elbert, the state's highest point, is 4,401 m; this is a
surface model at 30 m.

## `-r sum` is not sum-preserving on a geographic source

The recipe's fixture (a Sentinel-2 band in UTM) conserved the total exactly on both chips. At state
scale on lat/lon tiles, 1,802,620 of 3,628,800 output cells come back short on Intel and AMD, by
0.5, 1.0 or 1.5 source pixels' worth (of 100). Intel and AMD are **byte-identical**, so this is the
linux-64 build's arithmetic, not the CPU. `sumdiag.py` changes one setting at a time:

| `-r sum` variant | Graviton4 | Intel | AMD |
|---|---|---|---|
| as run (`-et` default, 16 threads, `-wm 1024`) | exact (3.9e-12) | 0.997324, 1,802,620 cells off | same bytes as Intel |
| same, repeated | identical bytes | identical bytes | |
| 1 thread | exact | 0.997324, 1,802,620 off | |
| default `-wm` | exact | 0.997541, 1,889,468 off | |
| `-et 0` (exact transformer) | **0.999951, 35,346 cells off** | 0.999958, 23,040 off | |
| `GDAL_WARP_USE_AFFINE_OPTIMIZATION=NO` | identical bytes to "as run" | | |
| **`-to INSERT_CENTER_LONG=FALSE`** | **exact**, 11.9 s (was 15.7) | **exact**, 16.6 s (was 23.8) | **exact**, 11.2 s (was 14.7) |
| **`-to INSERT_CENTER_LONG=FALSE -et 0`** | **exact** | **exact** | **exact** |

"Exact" is 0 cells off at 1e-9 and a total ratio of 1 to 1e-15. Blank cells were not run.

So this is **not Graviton4 right and x86 wrong: every chip runs a kernel that drops weight**.
Graviton4 happens to land clean under the defaults, and fails under `-et 0`. The chain, in GDAL 3.12.4:
- For a **geographic** source, `GDALCreateGenImgProjTransformer2` installs a reprojection to insert
  `CENTER_LONG` even when source and target CRS are the same (`gdaltransformer.cpp` ~2689–2691,
  `oSrcSRS.IsGeographic() && bMayInsertCenterLong`).
- That non-null `pReproject` makes `GDALTransformIsAffineNoRotation` false (~5090). So
  `GWKSumPreservingThread` skips its exact rectangle-overlap path (`gdalwarpkernel.cpp` 8586) and
  clips every source quad as a polygon.
- The polygon path drops a quad it judges self-intersecting (~8874). Where it judges a quad
  non-convex, it splits it into two triangles (~9011). On an aligned grid every quad is a
  rectangle whose edges coincide with target edges, so last-bit noise in the transformed corners
  decides both tests. That fits losses in whole and half pixels. This last step is inferred from
  the code and the loss pattern, not instrumented.

The measured part is the contrast. The affine flag changes nothing, so the fast path was already
off. Removing the `CENTER_LONG` reprojection restores exactness on all three chips and is 24–30% faster.
**On a lat/lon source, pass `-to INSERT_CENTER_LONG=FALSE` to `gdalwarp -r sum`** when the target
is the same CRS. In UTM (projected) data the fast path is already taken, which is why the fixture
never saw this.

## Across chips: fixed point or path

Compared on every pixel's sha256 and on a fixed 1-in-1,009 sample of each product
(`samples.npz` in each run's prefix):

- **Identical on all three:** the mosaic, the `-r near -et 0` reprojection, the hillshade (Byte),
  every count above, the round-trip, and the slope check's residual (9.7013e-04° to all printed
  digits).
- **Path, Intel and AMD against Graviton4 (Intel == AMD byte for byte):** the bilinear UTM DEM
  differs in 1 of 309,823 sampled pixels by one float32 ulp (2.4e-4 m), and so its slope raster's
  digest differs too. That is bilinear interpolation's last bit, inside every check's bound, and
  reported, not asserted.
- **Not a fixed point at all:** `-r sum`, above. The total should be conserved by construction,
  and the chip decides whether it is.

## Pins

| | |
|---|---|
| data | Copernicus DEM GLO-30 Public, 28 COGs `Copernicus_DSM_COG_10_N{37..40}_00_W{103..109}_00_DEM.tif` from `s3://copernicus-dem-30m/` (RODA, eu-central-1, 2022-05-09 objects). Copied server-side with `copy-object --checksum-algorithm SHA256`, so S3 computes each full-object sha256; checked against `tiles.sha256`, and again in-task with `sha256sum -c` |
| reference vectors | PROJ `9.8.1` `test/gie`, the recipe's staged `gie.tar` |
| script | `real.py`, sha256 `6da49292…6589d1`, checked in-task |
| image | the recipe's `geospatial@sha256:1827aeb5…` (arm64) and its `geospatial-07484bd0bf42` x86 twin |

Raw outputs per chip are in `results/`. The staged copy is 1.05 GB in your bucket (~$0.02/month);
delete `inputs/geospatial-real/` when done.
