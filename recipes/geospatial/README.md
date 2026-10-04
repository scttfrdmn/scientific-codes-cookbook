---
tool: gdal
tool_version: 3.12.4
env: geospatial
image: quay.io/aarchsci/geospatial@sha256:1827aeb547547b3e7ced8ede5cde82563635a765c2d5455221347ee2c10a74fb
spawn_version: 0.115.0
last_verified: 2026-10-03
---
# GDAL + PROJ — 6,191 upstream reference coordinates, and a warp checked against arithmetic

Runs PROJ's own committed test vectors, then warps a real 120M-pixel Sentinel-2 band and checks GDAL's resampler against a two-line reduction. For anyone whose results depend on a reprojection being right.

## Run it

```bash
make stage RECIPE=earth-observation && make stage RECIPE=geospatial   # once
spawn task run --spec "$(make -s spec RECIPE=geospatial)" --wait      # ~30 s of warping
```

```bash
gie *.gie                                   # 6191 tests succeeded, 0 failed
gdalwarp -r sum -ovr NONE -tr 60 60 B04.tif out.tif   # -ovr NONE is load-bearing
```

## Make it yours

| In the recipe | Swap for | What to know |
|---|---|---|
| PROJ 9.8.1's `test/gie` | the tag matching **your** image's PROJ | expected values from another release are a different reference — `pyproj.proj_version_str` tells you which. |
| 6× downsample on the band's own bounds | any integer factor, aligned | a non-integer factor makes partial pixel overlaps, and then GDAL's answer and the obvious answer are not the same question. |
| `-r sum` / `-r average` | `bilinear`, `cubic`, … | those interpolate, so no reduction reproduces them; the conservation check only works for the aggregating kernels. |

**Leave the fixture.** Both halves are already exact at this size — more pixels buys a longer run,
not a stronger claim. The raster is [earth-observation](../earth-observation/README.md)'s staged
band on purpose: a second copy of the same bytes is a second thing to keep true.

## Which box

`c8g.xlarge` (4 vCPU / 8 GiB). Measured: 6,191 reference vectors in under a second, the numpy
reduction over 120,560,400 pixels in **0.6 s**, and `gdalwarp -r sum -ovr NONE` in **29 s** — which
is the whole job. Docker install and the image pull are ~70 s of the 111 s window, so **these
timings are not compute cost** ([layout](../../patterns/layout-and-effective-cost.md)).

The warp is single-threaded in this form and bound by reading every pixel; the 225 MB band stages
into `/tmp` = [tmpfs at half the instance's RAM](../../patterns/data-movement.md), which with the
Float64 output is what sets 8 GiB.

<details>
<summary>As shipped: upstream's own vectors, a cross-code warp identity, two traps that pass quietly</summary>

### The checks

| observable | assertion | observed |
|---|---|---|
| gie completed | the `Grand total` line is present | present |
| **reference vectors** | **exactly 6191, 0 failed, 0 skipped** | **6191 / 0 / 0** |
| aligned grid | 10980² → 1830² at an exact integer factor | yes |
| **`-r sum` vs numpy** | **max\|diff\| < 1e-5 over 3,348,900 cells** | **3.242e-07** |
| total conserved | `sum(out)/sum(in)` = 1 to 1e-9 | **1.0000000000** |
| **`-r average` vs nodata-aware numpy** | **max\|diff\| < 1e-5** | **3.908e-08** |
| reprojection round-trip | corners recovered to < 1e-6 m | **1.11e-09** |

**The conda `proj` package ships `gie` but not the tests, so the tests are staged** from the PROJ
source at the tag matching the image — the same sourcing move as SIESTA's pseudopotentials
([the practice](../../practices/reference-from-tests.md)). That turns "reproject a coordinate and
get a number" into reproducing **6,191 coordinates upstream committed as correct**, across every
builtin projection, the ellipsoid models, axis-order handling and unit conversion. The `Grand
total` line is written only after every file runs, so it doubles as a completion sentinel: a run
killed part-way cannot produce it, however many tests had already passed.

`more_builtins.gie` is deliberately **not** staged. Nine of its 174 tests need proj-data grid
files the conda package strips, so including it would ship a recipe with nine expected failures —
and a recipe that expects a red line teaches readers to ignore red lines. Knowing which upstream
tests need data the package drops is part of the sourcing.

### The warp identity, and the two ways it quietly gives the wrong answer

On a grid aligned to the band's own bounds at an exact integer factor (10980 / 6 = 1830), every
output pixel covers exactly 36 input pixels and the right answer is *defined*. So GDAL's C++
warper and a two-line numpy reduction are two implementations of one piece of arithmetic, and
they agree to **3.2e-07** on values reaching 360,972 — 1e-12 relative, which is float64
accumulation order. That is a cross-code identity, not a band.

Getting there meant fixing the comparison twice, and both failures look like a tool bug:

- **`-ovr AUTO` reads the pyramid.** The COG carries overviews at 2/4/8/16×. Asked to downsample
  6×, GDAL's warper is entitled to read the 4× overview — whose pixels are *averages* — and
  summing those gives **0.062514 of the true total**, almost exactly 1/16. It is also 8.6× faster
  (3.4 s vs 29.2 s), and *that* is the only tell, because the output is a plausible-looking raster
  of the right shape. `-ovr NONE` forces full resolution. Reported as an observation rather than
  asserted: pinning the factor would make a future improvement to GDAL's overview selection fail
  this check for a good reason, and a check that fails for a good reason is still flaky.
- **`-r average` is not immune to nodata; `-r sum` is.** The band declares `nodata = 0`, so GDAL
  excludes those pixels from an average while a naive numpy mean divides by 36 regardless. Just
  **23 blocks of 3,348,900** contain a zero, and they move the comparison to `max|diff| = 32.1` on
  a mean of at most 10,027. Dividing by the valid count instead gives 3.9e-08. Summing never had
  the problem because a zero adds nothing to a sum — which is exactly why one kernel agreed
  immediately and the other did not, and why the fix is the metric, not either tool
  ([the rule](../../practices/cross-checks.md)).

### Pins

| | data tier |
|---|---|
| image | `quay.io/aarchsci/geospatial@sha256:1827aeb54754…` — GDAL 3.12.4, PROJ 9.8.1, rasterio 1.5.0, pyproj 3.7.2 |
| 12 `.gie` files | PROJ `9.8.1` tag, each pinned by sha256, tarred to one flat file |
| B04 (10 m red) | `sha256:8786ece1f55d…` — staged by [earth-observation](../earth-observation/README.md), read not copied |

The `.gie` files travel as a tar because a directory cannot be a spawn output on the container
path and twelve inputs would be twelve manifest entries
([why](../../practices/container-path.md)). `stage-inputs.sh` also fails loudly if B04 is not
staged, rather than letting the box discover it.

### Run + verify

```sh
make stage RECIPE=earth-observation   # B04, if not already staged
make stage RECIPE=geospatial          # the gie vectors
spawn task run --spec "$(make -s spec RECIPE=geospatial)" --wait
make ls RECIPE=geospatial
```

The smoke check runs inside the task and the bucket listing is the second half of it
([exit 0 isn't proof](../../practices/container-path.md)). Expect `smoke-check.txt`, `gie.out` and
`warp.json`. Re-running overwrites the prefix; no spec edit needed.

</details>
