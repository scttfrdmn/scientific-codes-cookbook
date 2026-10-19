---
tool: datashader
tool_version: "0.19.1"
env: geo-ml
image: quay.io/aarchsci/geo-ml@sha256:7414b1df7c155b7d820cc062d88b61e5081b3d2ba4685c57a29b591cd67748de
env_x86_64: geo-ml-32594bd95edd
spawn_version: 0.127.0
last_verified: 2026-10-11
last_verified_x86_64: 2026-10-17
last_verified_amd: 2026-10-18
---
# datashader — 50M points rasterized, counted exactly

Aggregates 50 million points into a 1024² raster on Graviton4 and checks every cell against `np.histogram2d`. For anyone rasterizing point clouds too big to plot.

## Run it

```bash
make stage RECIPE=datashader    # once: the checks only — the cloud comes from a pinned seed
spawn task run --spec "$(make -s spec RECIPE=datashader)" --wait
make ls RECIPE=datashader

cvs = ds.Canvas(plot_width=1024, plot_height=1024, x_range=(0, 1), y_range=(0, 1))
agg = cvs.points(df, "x", "y", ds.count())    # dims ('y','x'), y ASCENDING
np.asarray(agg).T == H                        # vs np.histogram2d — note the transpose
```

## Make it yours

| In the recipe | Swap for | What to know |
|---|---|---|
| `np.asarray(agg).T` for the numpy check | comparing as-is | **the transpose is required.** datashader is `('y','x')` with y ascending; `histogram2d` is `[x,y]`. Compared as-is, 916,968 of 1,048,576 cells disagree — a loud failure, but it is *your* bug, not datashader's. |
| a cloud spanning `[-1,2]²` on a `[0,1]²` canvas | points that all fit inside | **keep it outside.** 89% of this cloud is out of range, so the conservation check is load-bearing; on a cloud that fits, it would pass for code that ignored `x_range` entirely. |
| `ds.count()` | `ds.sum`/`mean`/`max`/`std` | `mean` is `NaN` in empty cells, not 0 — which is what makes empty distinguishable from a true zero. Check how your downstream colormap treats it. |
| 50M points in pandas | a dask or GPU frame | this is the shape datashader exists for; at 50M the aggregation is **0.18 s warm** and the box is idle the rest of the time ([effective cost](../../patterns/layout-and-effective-cost.md)). |
| this synthetic cloud | your own | [geo-ml](../geo-ml/README.md) has the real-geometry recipe in this env (3,235 counties, 8.2M vertices); this page is about the aggregation being exact. |

**Leave the fixture.** Nothing is fetched: the cloud is drawn from a pinned seed, so the reference inside-count is derivable and staging checks it against the `1/9` the geometry implies. **Scale it** by raising `N` — the identities are integer counts and hold at any size.

## Shape, size, cost

One task on `m8g.xlarge` (4 vCPU / 16 GiB), TTL 30m as a **backstop** with `cost_limit` $0.15 as the real guard. The whole check — generating 50M points, three aggregations, and a 1024² `histogram2d` — is **20 s** of a 1m26s window; 40 s is installing Docker and 24 s pulling the image. The aggregation itself is **0.18 s**, so these timings are not compute cost ([layout](../../patterns/layout-and-effective-cost.md)).

<details>
<summary>As shipped: bit-identical to np.histogram2d over 1,048,576 cells, two exact invariances, and the orientation asserted rather than assumed</summary>

### The checks

| observable | assertion | observed | x86_64 (m8i.xlarge) |
|---|---|---|---|
| staged bytes | match pin | 1 of 1 | identical |
| datashader / numba / pandas / numpy | read from the running install | **0.19.1** / 0.68.0 / 3.0.6 / 2.5.4 | identical |
| **orientation**, 3 known corners | **exact array indices** | `[0,0]`, `[3,0]`, `[0,3]` | identical |
| **bin-edge rule vs `histogram2d`** | **identical binning** | `2,2,0,2` both | identical |
| points inside the canvas | numpy's count | 5,556,971 of 50,000,000 | identical |
| outside fraction | fixture must be adversarial | **0.8889** | identical |
| **raster total** | **`==` numpy inside-count** | **5,556,971, diff 0** | identical |
| **`sum('w')` with w≡1** | **`==` the count** | **5,556,971** | identical |
| **`mean − sum/count`** | **`== 0` exactly** | **0.000e+00** | identical |
| empty cells | `mean` is NaN, not 0 | 5,278 cells, all NaN | identical |
| **vs `np.histogram2d`** | **every cell equal** | **0 of 1,048,576 differ** | identical |
| control: as-is / flipud / fliplr | must *not* match | 916,968 / 919,386 / 918,598 differ | identical |
| **invariance: input row order** | **bit-identical raster** | identical | identical |
| **invariance: canvas resolution** | **total unchanged** | 5,556,971 at 128, 256, 512, 1024 | identical |

### Two implementations, 1,048,576 cells, no tolerance

Rasterizing is counting, so nothing here needs a bound. datashader's numba-compiled aggregation
and `np.histogram2d` are unrelated code, and over a 1024² grid of 50M points they agree on **every
single cell**:

```text
cells compared                1048576
max |difference|                    0
cells differing                     0
```

The conservation check is separate and stronger than it looks, because of how the fixture is
built: the cloud spans `[-1,2]²` while the canvas covers `[0,1]²`, so **89% of the input is out of
range**. The raster total must equal the 5,556,971 points numpy says are inside — an
implementation that ignored `x_range`/`y_range` would land on 50,000,000 instead. Staging confirms
that fraction independently against the `1/9` the geometry implies, so the fixture cannot quietly
stop being adversarial.

### The orientation, asserted rather than assumed

**datashader returns `dims=('y','x')` with y ascending; `np.histogram2d` returns `[x, y]`.** The
numpy comparison therefore needs a transpose, and that was read off a fixture before it was relied
on — single points dropped at known corners of a 4×4 canvas:

```text
point (0.5, 0.5)  lower-left   -> index [0, 0]
point (0.5, 3.5)  upper-left   -> index [3, 0]
point (3.5, 0.5)  lower-right  -> index [0, 3]
```

Those indices are now **assertions**, so a future release that flipped the convention fails here
rather than silently producing an upside-down raster that still conserves mass. The controls show
the elementwise check can actually tell: compared without the transpose, or flipped either way,
**87–88% of cells disagree**. That is a far louder signal than the ~26% a flip produced in
[yt](../yt/README.md), because a dense random cloud makes every cell an independent witness.

**The bin-edge rule is checked first, and that ordering is the point.** Fed points at `0.0, 0.999,
1.0, 1.001, 3.999, 4.0, 4.001`, both tools return `[2, 2, 0, 2]` — both include the upper edge,
both drop `4.001`. Had the conventions differed, the 1,048,576-cell comparison would have failed
for a reason that is not a bug in either tool, and the recipe asserts the edge rule separately so
that case reports itself as a convention mismatch ([compare like with
like](../../practices/cross-checks.md)).

### Two invariances, both exact

Neither of these has a reference value, and both are stronger than a band on one run:

```text
input row order     50M rows -> 5M subset permuted -> raster bit-identical
canvas resolution   total 5556971 at 128, 256, 512 and 1024 -- unchanged
```

Row order cannot matter because a count is a count; resolution cannot change the total, only how
it is distributed. An aggregation that double-counted a boundary point, or dropped one, breaks the
second while still producing a plausible picture.

`mean` is also checked against `sum/count` cell by cell, exactly (`0.000e+00`) — three aggregations
that must cohere — and empty cells are asserted to be `NaN` rather than `0`, which is the only
reason an empty cell is distinguishable from a cell whose points sum to zero.

### Pins

| | |
|---|---|
| data | **none.** 50M points from `default_rng(20261011)`; the reference is an exact count, checked in staging against the `1/9` geometry |
| checks | `identities.py`, pinned by sha256 |
| image | `quay.io/aarchsci/geo-ml@sha256:7414b1df7c155b7d82…` — datashader 0.19.1, numba 0.68.0, pandas 3.0.6, numpy 2.5.4, geopandas 1.2.0, python 3.14 |

cosign-verified against `playgroundlogic/aarchsci`; the signature covers the **manifest-list**
digest, so verify the tag and pin the arm64 digest. The env lock records numpy 2.5.3 while the
running install reports **2.5.4** — the versions above are what the run read, not what the lock
says.

`stage-inputs.sh` reads `SEED`, `N` and `RES` out of `identities.py` rather than restating them,
so the staging validation cannot drift from the run.

**x86_64.** `make stage RECIPE=datashader ARCH=x86_64` then the same `spawn` line with `ARCH=x86_64`
runs on `m8i.xlarge` through the `geo-ml-32594bd95edd` twin (`envs-x86_64/`: the pinned image's
versions as linux-64 builds, installed offline in 7 s). datashader, numba, pandas and numpy are the
same versions; 15 of 367 packages differ (none in the computation: aiohttp, charset-normalizer,
executing, fonttools, jedi, libcurl, libglib, libsqlite/sqlite, libzip, open-radar-data,
platformdirs, soupsieve, tifffile, wrapt). Every value is an integer count, so every row is
**identical** across chips; only the timing moves (aggregation 0.36 s warm vs 0.18 s on Graviton4).

### Run + verify

```sh
make stage RECIPE=datashader
spawn task run --spec "$(make -s spec RECIPE=datashader)" --wait
aws s3 cp "s3://$(make -s print-bucket)/runs/datashader/r1/score.tsv" -
```

Fails on a pin mismatch, a corner point landing at the wrong index, a bin-edge rule that diverges
from `histogram2d`, a raster total that is not exactly numpy's inside-count, a single differing
cell out of 1,048,576, a control orientation that *also* matches, `mean != sum/count`, an empty
cell that is 0 rather than NaN, a raster that changes under row permutation, or a total that
changes with resolution — but check the bucket regardless
([exit 0 isn't proof](../../practices/container-path.md)).

### Not covered

**Rendering.** `tf.shade`, colormapping and the spreading/dynspread operators are the half of
datashader people actually look at, and verifying an image needs image comparison. Only the
aggregation is checked here.

**Everything but points.** `Canvas.line`, `.area`, `.polygons`, `.raster` and `.trimesh` are
untouched, and the antialiasing path with them — line rasterization has its own edge rules that
none of the above exercises.

**dask and out-of-core.** 50M rows fit in memory, so the dask-backed and partitioned paths are
not run, and nothing here speaks to the chunked aggregation most large jobs use. **GPU
(cudf/cupy)** likewise — Round Two. **Categorical aggregation** (`ds.count_cat`), **`ds.std`/`var`**
and the `where`/`first`/`last` reductions. **Non-linear axes** (`ds.Canvas(x_axis_type="log")`),
where the bin-edge agreement asserted above would need re-deriving rather than re-using.

</details>
