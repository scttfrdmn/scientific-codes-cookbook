# PDAL on a whole USGS 3DEP project: Lassen Volcanic NP, 4.64 G points, three chips

> **A whole 3DEP project goes through PDAL in 8–14 minutes on 16 vCPU, for $0.12–0.17 of compute.**
> AMD is 1.33× faster than Graviton4 and Intel 1.27× slower. Graviton4 is cheapest per result,
> billed and on compute. Every count, histogram, bound and DTM cell is identical on all three chips.
> The written LAZ bytes are not, and that is a path: they also change between two runs on one box.

Workload: **every point of `CA_LassenNP_2020`**, read in place from the USGS 3DEP Entwine Point Tiles
on RODA (`s3://usgs-lidar-public`, us-west-2). That is 4,642,900,288 points in 107,501 EPT nodes,
26.7 GB of LAZ, from 128 delivered source tiles. A 3DEP project is the unit USGS delivers and the unit
its EPT indexes. Of the 2,284 projects on RODA ([`projects.tsv`](projects.tsv), from
[`survey.py`](survey.py)) the median holds 14.7 G points and the quartiles 3.3 G and 47.8 G, so
Lassen is larger than 30% of them. The job is what a researcher does with a project: cut it into
tiles, export each tile as LAZ, and grid the ground returns into a DTM. Here that is 2 km tiles and a
2 m DTM, one PDAL job per tile, 16 at a time ([`real.py`](real.py)):

```text
A  readers.ept(bounds=tile) -> filters.stats -> writers.las   (LAZ 1.4, PDRF 6, the EPT's scale + offset)
B  readers.las(the tile)    -> filters.stats -> filters.range(Classification[2:2]) -> writers.gdal (min, mean, count; binmode)
```

Then laspy, with the lazrs decoder, reads all 107,501 EPT nodes again, each checked against its sha256 pin.

## Run it

```sh
export AWS_PROFILE=aws B=<your bucket>
bash measurements/pointcloud-real/stage-inputs.sh "$B"     # scripts + the pin; the cloud is never copied
for a in arm64 x86_64 amd; do
  spawn task run --wait --spec "$(python3 scripts/spec_arch.py pointcloud $a "$B" r2 measurements/pointcloud-real/real.task.json)"
done
bash measurements/pointcloud-real/collect.sh "$B" r2        # results/<chip>/, results/compare.txt
spawn task run --wait --spec "$(python3 scripts/spec_arch.py pointcloud arm64 "$B" c1 measurements/pointcloud-real/03-compare.task.json)"
```

The tile grid has its edges on half-quanta (−13530340.005 + 2000·i), so no point can sit on one, and
every tile that touches a source tile is run: 130 of the 17 × 10, of which 125 hold points. The x86
runs use the recipe's `pointcloud-792583991793` env twin: PDAL 2.10.1, laspy 2.7.0, lazrs-python
0.8.2, as on arm64, but a different PDAL build (git `660b06` against arm64's `27c27c`).

## Results

`c8g/c8i/c8a.4xlarge` (16 vCPU, 32 GiB), 16 PDAL jobs at a time, same image versions, same bytes:

| chip | instance | cores × threads | PDAL wall | task window | peak RSS (pdal / box) | $/hr | compute $/result | billed $/result |
|---|---|---|---|---|---|---|---|---|
| Graviton4 | `c8g.4xlarge` | 16 × 1 | 647 s | 1300 s | 1.40 / 8.3 GiB | 0.6381 | **0.115** | **0.230** |
| Xeon 6975P-C | `c8i.4xlarge` | 8 × 2 | 820 s | 1380 s | 1.27 / 10.1 GiB | 0.7497 | 0.171 | 0.287 |
| **EPYC 9R45** | `c8a.4xlarge` | 16 × 1 | **486 s** | **1146 s** | 1.24 / 9.8 GiB | 0.8622 | 0.116 | 0.274 |

A *result* is the whole project tiled, exported, gridded and checked. *PDAL wall* is the 130 tiles'
A and B pipelines, launch of the first to exit of the last; compute $/result is that at the hourly
price. *Task window* is spawn's, from wrapper start to command exit. Billed $/result is spawn's
`cost_estimate` for that window, which also holds the laspy pass (494–605 s), the checks, the
mosaic, Docker and the image or env install. *Peak RSS* is the largest `ru_maxrss` of any one
`pdal` process. *Box* is the container cgroup's `memory.peak`, page cache included.

- **PDAL is compute-bound here, not I/O-bound.** It used 15.5–15.7 of the 16 vCPU for the whole
  phase on every chip. That is 7.2 M points/s on Graviton4, each point decoded from the EPT, encoded
  to LAZ, decoded again and gridded. Pipeline A (EPT in, LAZ out) is 72–75% of the PDAL CPU time.
- **AMD is fastest, Graviton4 is cheapest.** AMD's 1.33× speed nearly pays for its 1.35× price, so its
  compute cost ties Graviton4's ($0.116 against $0.115). Intel's 16 vCPU are 8 cores, and it is 1.27×
  slower than Graviton4 at 1.17× the price.
- **The laspy pass is I/O-bound and is not PDAL.** It runs 107,501 sequential HTTPS GETs across 16
  Python processes and uses 2.4–3.9 cores. Fetch is 77–86% of its worker time. It is a check, not part of the
  workload, but it is in the billed column.
- **Memory is small.** PDAL streams (`--stream`), so the largest tile, 74.6 M points, peaks at 1.4 GiB. A
  `c8g.4xlarge` was picked for its 16 cores, not for its RAM. The tile LAZ files (26.8 GB in all) are
  written to the root disk and deleted once checked.
- **n = 1 per chip.** Every asserted value is the same on all three chips, and on two Graviton4 runs.
  These timings are not compute cost in general ([layout](../../patterns/layout-and-effective-cost.md)).

## What held at scale, on all three chips

Checked inside each run by `real.py`:

| claim | why it must hold | Graviton4 | Intel | AMD |
|---|---|---|---|---|
| ept.json, 1,585 hierarchy files, the sources manifest and all 107,501 nodes match their sha256 pins | provenance | yes | yes | yes |
| Σ hierarchy counts == Σ source-tile counts == ept.json `points` | three statements of one number | 4,642,900,288 | same | same |
| one data object per non-empty hierarchy node | EPT structure | 107,501 | same | same |
| laspy: every node's header count == decoded count == its hierarchy count | per-node conservation | 107,501 / 107,501 | same | same |
| **Σ PDAL points over 125 tiles == ept.json `points`** | tiling loses and duplicates nothing | yes | yes | yes |
| **PDAL (laz-perf) class and return histograms == laspy (lazrs)** | two LAZ decoders on the same bytes | 6 classes, 8 returns | same | same |
| PDAL min/max X Y Z == laspy, and inside `boundsConforming` | decode + scale/offset | Z 1107.77–2710.99 | same | same |
| PDAL mean Z == laspy's exact integer mean, within naive-summation error | decode, every point | Δ 1.2e-8 ≤ 1.2e-5 | same | same |
| written LAZ read back: counts, histograms, min/max identical; mean bit-identical | LAS round trip | 125 / 125 tiles | same | same |
| written header: count, bounds, points-by-return == decoded (by PDAL and by laspy) | header round trip | 125 / 125 | same | same |
| written header: LAS 1.4, PDRF 6, the EPT's scale and offset | what was asked for | 125 / 125 | same | same |
| **DTM: Σ count band == class-2 points, per tile** | each ground point in exactly one cell (`binmode`) | 1,835,934,506 | same | same |
| DTM: min finite exactly where count > 0; min ≤ mean | grid algebra | 85,319,845 cells | same | same |

The mean-Z bound is set before the run, not fitted to it. Summing n doubles naively errs by at most
(n−1)·2⁻⁵³·Σ|z|, so each tile's mean errs by at most (n−1)·2⁻⁵³·max|z|; the bound is that,
weighted by n over the tiles. The observed difference is 1/1000 of it.

**The data's own oddities, reported:** 57,303 points have `ReturnNumber` 0, which LAS forbids. Both
decoders agree on them, and the LAS header has no slot for them, so points-by-return is compared on
returns 1–15. The classes are 1 (2.80 G), 2 ground (1.84 G), 9 water, 7 and 18 noise, and 20.

## Across chips: fixed point or path

- **Fixed, and identical on all three:** all 17 values in `results.tsv` other than the LAZ digest and
  LAZ size, every tile's point and ground count, every DTM tile's min-band sha256, and the
  whole-project 2 m mean DTM. `03-compare.task.json` compared the mosaics: 10,001 × 17,001 cells,
  0 differ ([`compare-dtm-order.txt`](results/compare-dtm-order.txt)).
- **Path: the bytes of the written LAZ.** All 125 tiles' LAZ files differ between chips, and their
  total size moves by up to 1.5 MB in 26.8 GB. They also differ between two Graviton4 runs (26,774,277,032
  and 26,775,144,802 bytes). The compare task ran one tile's pipeline A twice on one box. The two
  files have different bytes and a different point order, and their sorted point records are
  identical. So `readers.ept` emits a tile's points in an order that varies from run to run, and the
  LAZ encoding follows the order. Nothing in the assertions depends on it. Sort first (`filters.sort`)
  if you need byte-identical tiles.

## Pins

| | |
|---|---|
| point cloud | RODA `s3://usgs-lidar-public/CA_LassenNP_2020/`, read in place. All 109,345 objects are pinned in `pins.tsv` (key, size, sha256): 26,694,580,315 B, fetched on AWS by `00-pin.task.json` ([`pin.py`](pin.py)) in 187 s, every one also MD5-checked against its ETag. `pins.tsv` is `sha256:15267268…` in [`pins.sha256`](pins.sha256) and stays in your bucket (10 MB). |
| scripts | `real.py`, `real.sh` and `pin.py` in `pins.sha256`, `compare.py` in `compare.sha256`; checked in-task |
| image | the recipe's `pointcloud@sha256:1e53023e…` (arm64) and its `pointcloud-792583991793` x86 twin |

Raw per-chip outputs are in `results/`. The 216 MB DTM COG (`dtm-2m-mean.tif`, EPSG:3857) stays in
the bucket under each chip's `runs*/pointcloud-real/r1/`.

## Spend

Nine launches, **$1.20**. The three verifying runs cost $0.79. The rest: the pin ($0.05), three
probes ($0.08), the compare task ($0.01), and a first full Graviton4 run ($0.27). That run passed every
check but the mean-Z one, whose bound had been written as n·2⁻⁵² rather than n²·2⁻⁵³.
