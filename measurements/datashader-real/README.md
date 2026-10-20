# datashader on every NYC taxi trip with coordinates — Graviton4, Intel and AMD

> **680 million taxi pickups and dropoffs are read, persisted and rasterised onto a 4096² NYC canvas
> in 18–26 s, for under a cent of compute on any chip.** Every cell of every raster equals
> `np.histogram2d` on all three chips, and the rasters are byte-identical across chips. AMD is
> fastest. Graviton4 is cheapest, on compute and billed.

Workload: **every NYC yellow-taxi trip of 2009 and 2010**, 24 monthly Parquet files from the TLC
(11.4 GB, 339,897,217 trips), with pickup and dropoff as two point sets: 679,794,434 points. Those are
the only years the TLC still publishes with coordinates; from 2011 its files carry taxi-zone IDs
instead. So this is the whole coordinate-bearing record, not a sample month. datashader's own NYC-taxi
example is one month of 2015, about 12 M trips, so this is roughly 28× that. It is what one box holds
in memory as a persisted dask frame (40.8 GiB). Billions of points would be a cluster job, which this
does not cover.

The job is the standard one, in [`real.py`](real.py): `dd.read_parquet` over all 24 files, `persist()`,
then `Canvas.points` on a 4096 × 4096 canvas over lon −74.25…−73.75, lat 40.5…41.0 (~10 m cells).
It aggregates a pickup `summary` (count, passengers, fare in integer cents, a count of fares, and
mean fare), a dropoff count, and pickup counts at 2048² and 1024².

## Run it

```sh
export AWS_PROFILE=aws B=<your bucket>
bash measurements/datashader-real/stage-inputs.sh "$B"     # fetch on AWS once (00-fetch), check pins, stage scripts
for a in arm64 x86_64 amd; do
  spawn task run --wait --spec "$(python3 scripts/spec_arch.py datashader $a "$B" r1 measurements/datashader-real/real.task.json)"
done
python3 measurements/datashader-real/collect.py <spawn results bucket> r1   # after copying each run's outputs to results/<chip>/
```

```python
ddf = dd.concat([dd.read_parquet(files_2009, ...), dd.read_parquet(files_2010, ...)]).persist()
cvs = ds.Canvas(plot_width=4096, plot_height=4096, x_range=(-74.25, -73.75), y_range=(40.5, 41.0))
pu  = cvs.points(ddf, "plon", "plat", agg=ds.summary(n=ds.count(), cents=ds.sum("cents"), mean=ds.mean("cents")))
```

[`run.sh`](run.sh) checks the staged bytes against [`pins.sha256`](pins.sha256) and records the host.
[`mkspec.py`](mkspec.py) writes the specs and pins the staged scripts by sha256.

## Results

`r8g/r8i/r8a.4xlarge` (16 vCPU, 128 GiB), 16 dask threads, the same `geo-ml` versions (aarch.science's
arm64 image; its `geo-ml-32594bd95edd` linux-64 twin on both x86 chips), the same bytes:

| chip | instance | cores × threads | tool wall | billed | peak RSS | $/hr | compute $/result | billed $/result |
|---|---|---|---|---|---|---|---|---|
| Graviton4 | `r8g.4xlarge` | 16 × 1 | 22.7 s | 165 s | 40.8 GiB | 0.9426 | **0.0060** | **0.0432** |
| Xeon 6975P-C | `r8i.4xlarge` | 8 × 2 | 26.1 s | 160 s | 42.1 GiB | 1.1114 | 0.0081 | 0.0494 |
| **EPYC 9R45** | `r8a.4xlarge` | 16 × 1 | **18.3 s** | **132 s** | 42.1 GiB | 1.2781 | 0.0065 | 0.0469 |

| phase | read + persist 680 M points | pickup `summary` (cold, incl. JIT) | dropoff count | pickup count (warm) | 2048² + 1024² |
|---|---|---|---|---|---|
| Graviton4 | 18.1 s | 3.23 s | 0.61 s | 0.49 s | 0.36 s |
| Intel | 19.9 s | 4.28 s | 0.76 s | 0.69 s | 0.47 s |
| AMD | 14.4 s | 2.71 s | 0.52 s | 0.38 s | 0.27 s |

A *result* is the whole dataset rasterised. *Tool wall* is read + persist + every aggregation. Compute
$/result is that at the hourly price. *Billed* is spawn's task window, wrapper start to stage-out, and
billed $/result is spawn's `cost_estimate` for it. *Peak RSS* is the main process's `VmHWM`.

- **Aggregation is the cheap part. Reading the Parquet is 76–79% of the tool wall.** A warm count of 340
  M points takes 0.38–0.69 s, which is 0.89 / 0.70 / 0.49 G points/s (AMD / Graviton4 / Intel). The
  rest is pyarrow decoding the columns into the persisted frame.
- **AMD is 1.24× faster than Graviton4 and Intel 1.15× slower.** Graviton4 is cheapest anyway, on
  compute ($0.0060) and billed ($0.0432), because the AMD hour costs 1.36× as much.
- **The billed column is mostly not datashader.** It is 132–165 s, of which the tool takes 18–26 s.
  Staging 11.4 GB into tmpfs takes 38–44 s, and the reference checks take 27–42 s. Docker and the
  image take the rest: a 24 s pull of the arm64 image, against 2 s for the x86 micromamba base plus
  an offline env install. These timings are not compute cost ([layout](../../patterns/layout-and-effective-cost.md)).
- **Memory picked the box, not cores.** The persisted frame peaks at 40.8–42.1 GiB, beside 10.6 GiB of
  staged Parquet in the tmpfs (half the RAM), and the box's cgroup peaked at 59–63 GiB in all. A
  2xlarge's 64 GiB would be too close.
- **n = 1 per chip.** Every output is an integer count or an exact integer sum, and the rasters are
  byte-identical on all three chips. A second run would add a timing sample and nothing about
  correctness.

## What held at scale, on all three chips

All asserted inside the task. The extent is **dyadic**: each axis spans 0.5° = 2⁻¹ in 2¹² cells, and
both ends are multiples of 2⁻¹³. So the map from a coordinate to its cell is exact in floating point,
in datashader (a scale of 2¹³ and an exactly representable offset) and in numpy (`searchsorted` on
exactly representable edges). No point can be binned
differently by a rounding at an edge, and that is what allows "every cell equal" with no tolerance.

| identity | value (identical on all three) |
|---|---|
| rows read by dask == Parquet footers == the numpy pass | 339,897,217 |
| **pickup raster total == numpy boolean inside-count** | **333,451,537** |
| **dropoff raster total == numpy boolean inside-count** | **333,422,546** |
| passengers / fare-cents raster totals == numpy inside-sums | 560,947,025 / 328,796,517,140 |
| **pickup count, dropoff count, passengers, cents == `np.histogram2d`ᵀ, every cell** | **0 of 16,777,216 differ, ×4** |
| control: the pickup count compared without the transpose | 3,601,078 cells differ |
| dask persisted (8 partitions) == Σ of pandas rasters over 2 M-row batches | 0 cells differ |
| 2048² and 1024² rasters == the 4096² one block-summed 2×2 and 4×4 | 0 cells differ |
| `sum` and `mean` are NaN exactly where the count is 0 | 14,496,006 empty cells |
| `mean == sum / count`, every non-empty cell | max \|diff\| 0 |
| warm repeat == the `summary` count | bit-identical |

The block-sum row is the dyadic extent paying twice. The edges of a coarser canvas are a subset of
the finer one's, so changing resolution can only regroup points, and it must regroup them exactly.
The conservation rows use an independent count. A numpy boolean mask decides what is inside, and
6,445,680 finite pickups fall outside the canvas, so a raster that ignored `x_range` would be wrong by
that many. 110 dropoffs have NaN coordinates. No passenger count or fare is NaN. Money is summed as
`round(fare·100)`, and the largest absolute sum (3.3e11) is far below 2⁵³, so every partial sum is
exact in any order.

The data: the busiest pickup cell is at −73.99408, 40.75116 (by Penn Station, 244,829 pickups).
2,281,210 cells have a pickup and 2,950,829 a dropoff. The mean fare over the 333 M inside pickups is
$9.86.

## Across chips: fixed point or path

**Everything is fixed, and identical on all three chips.** All 49 non-timing records in `results.json`
match ([`results/crosschip.txt`](results/crosschip.txt)). That includes the sha256 of each of the four
4096² rasters, every count and sum, and the empty-cell counts. Nothing here takes a path: counting is
order-free, and integer sums below 2⁵³ are exact in any order. There was no path to report. OpenBLAS
selects `neoversev2` on Graviton4 and `Cooperlake` on both Intel and AMD, but nothing here calls it at a size that
matters ([`results/*/check.txt`](results/)).

## Pins

| | |
|---|---|
| data | NYC TLC yellow-taxi trip records 2009-01 … 2010-12, from the TLC's CloudFront origin (`d37ci6vzurychx.cloudfront.net/trip-data/`). RODA's `s3://nyc-tlc` refuses every request ([r-real](../r-real/README.md)). The files were fetched **on AWS** by [`00-fetch.task.json`](00-fetch.task.json) ([`fetch.sh`](fetch.sh)), and each byte count was checked against Content-Length. [`pins.sha256`](pins.sha256) is each file's sha256 as fetched on 2026-10-20. [`rows.tsv`](rows.tsv) holds the bytes, rows and row groups per file. A re-issued month fails staging, and fails again in-task. |
| scripts | `run.sh`, `real.py`, `pins.sha256`, pinned by sha256 in the spec's command |
| image | the recipe's `geo-ml@sha256:7414b1df…` (arm64) and its `geo-ml-32594bd95edd` x86 twin: datashader 0.19.1, numba 0.68.0, dask 2026.8.0, pandas 3.0.6, pyarrow 25.0.0, numpy 2.5.4 on all three chips |

The per-chip outputs are in `results/<chip>/`. The rasters themselves (`rasters.npz`, pickup and
dropoff counts as uint32) stay in the bucket under each chip's `runs*/datashader-real/r1/`.

## Spend

Seven launches, **$0.23**. The three verifying runs cost $0.14. The rest was the fetch ($0.02), two
2-file probes ($0.03), and a full-dataset sizing run on `r8g.4xlarge` at 8 threads ($0.04). The
probes found that datashader names a raster's dims after its columns, and that its `sum` is NaN, not 0,
in an empty cell. Both are now assertions.
