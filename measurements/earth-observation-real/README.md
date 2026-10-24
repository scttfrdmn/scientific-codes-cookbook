# rasterio on a real workload — a whole year of one Sentinel-2 tile, three chips

> **Every 2024 acquisition of tile 11SKA (153 scenes, 31.9 GB of COGs read in place from RODA)
> goes through rasterio in about 105 s on 16 vCPU. Every one of ESA's 1,764 published percentages
> is reproduced from the pixels, and every output is identical, byte for byte, on Graviton4, Intel
> and AMD.** The run does **not** pass its BOA-offset guard: one scene, `S2C_11SKA_20241206_1_L2A`,
> is flagged `boa_offset_applied: false` while its pixels are already offset, so the run exits 1 on
> all three chips ([what failed](#what-failed-the-offset-guard), Element84/earth-search#66).
> Graviton4 and AMD tie on wall time; Graviton4 is cheapest on compute, and billed cost is
> $0.07–0.10 because boot dominates. RAM picks the box, not vCPU.

Workload: **every** Sentinel-2 L2A item of MGRS tile 11SKA (central California) dated 2024 in the
AWS Open Data `sentinel-cogs` bucket. That is 153 scenes from both relative orbits and three
satellites (S2A, S2B, S2C), 71 of them full tiles and the rest swath edges. A 154th item,
`S2B_11SKA_20241025_0`, is 100% nodata and publishes no COGs. For each scene the run reads red
(B04), NIR (B08) and the scene classification (SCL), and the year ends in a **greenest-pixel
composite**: per-pixel maximum NDVI over clear-sky observations, at 10 m, written as a COG. A
season or a year of one tile is the unit of a phenology or compositing study. A region is the same
job fanned out by tile, not a different workload. The recipe's fixture is one of these 153 scenes.

## Run it

```sh
export AWS_PROFILE=aws B=<your bucket>
bash measurements/earth-observation-real/stage-inputs.sh "$B"   # copies the 153 STAC items S3-to-S3; scripts + pins
for a in arm64 x86_64 amd; do
  spawn task run --spec "$(python3 scripts/spec_arch.py earth-observation $a "$B" r2 \
    measurements/earth-observation-real/real.task.json)" --wait
done
bash measurements/earth-observation-real/collect.sh "$B" r2   # results/<chip>/, results/compare.txt
```

The bands are **never copied**. Each worker pulls an object once into memory over HTTPS, hashes it
against [`pins.sha256`](pins.sha256), and hands rasterio a `MemoryFile`. The pins came from
`00-pin.task.json` ([`pin.py`](pin.py)), which hashed the 153 items and streamed the 459 band
objects on AWS in 31 s. Only the
STAC items are copied into the bucket, because they are the reference and an item can be rewritten
in place (`S2B_11SKA_20241104_0`'s item was last written in 2026-08, its bands in 2024-11). The
task runs [`real.sh`](real.sh) → [`real.py`](real.py) with 16 worker processes, one GDAL thread each.
Its core, per scene:

```python
with MemoryFile(blob) as mf, mf.open() as s:          # blob: the pinned bytes of B04.tif
    red = s.read(1)
off = 0.0 if props["earthsearch:boa_offset_applied"] else band["offset"]   # per item
ref = red.astype(np.float32) * band["scale"] + off
ndvi = (nir_r - red_r) / (nir_r + red_r)              # where both reflectances are > 0
np.maximum(comp, ndvi, out=comp, where=clear & valid) # SCL in {vegetation, bare, water}
```

## Results

16 vCPU, one `r8?.4xlarge` (128 GiB) per chip, 16 workers, same versions (rasterio 1.5.0, GDAL
3.12.4, numpy 2.5.2), same input bytes. All three runs exit 1 on the guard below, after every
output is written and staged:

| chip | instance | real.py wall | task window | billed | $/hr | compute $/result | **billed $/result** | peak memory |
|---|---|---|---|---|---|---|---|---|
| Graviton4 | `r8g.4xlarge` | 106.1 s | 203 s | 286 s | 0.9426 | **0.0278** | **0.0749** | 59.1 GiB |
| Xeon 6975P-C | `r8i.4xlarge` | 148.1 s | 193 s | 291 s | 1.1114 | 0.0457 | 0.0898 | 59.7 GiB |
| EPYC 9R45 | `r8a.4xlarge` | **104.6 s** | 142 s | 281 s | 1.2781 | 0.0371 | 0.0998 | 51.6 GiB |

A *result* is the whole year plus every check below. *real.py wall* is all of it: fetch, decode,
checks, composite and both COGs. The *task window* runs from spawn's wrapper start to command exit,
which is what spawn's `cost_estimate` prices; $/hr is that estimate over that window. *Billed* is EC2
launch to terminate. Peak memory is the container cgroup's `memory.peak`. Threads per core: 1 on
Graviton4 and AMD, 2 on Intel, whose 16 vCPU are 8 cores. OpenBLAS selects `neoversev2` on Graviton4
and `Cooperlake` on both x86 chips; nothing here calls BLAS.

| per-worker seconds, summed over 153 scenes | fetch + hash | decode (rasterio + tifffile) | science |
|---|---|---|---|
| Graviton4 | 605 | 385 | **494** |
| Intel | 604 | 508 | 976 |
| AMD | **574** | **326** | 613 |

- **A year of a tile is about 105 s of 16 vCPU.** That is 31.9 GB through rasterio at ~300 MB/s,
  with 41.5 G pixels decoded twice (rasterio, then tifffile). Graviton4 and AMD finish within 1.5 s
  of each other. Intel is 40% slower: the numpy science runs on 8 physical cores under 16 workers
  and takes twice Graviton4's time.
- **Graviton4 is the cheapest on compute** ($0.028 vs $0.037 AMD, $0.046 Intel) **and billed**.
  49–63% of each billed window is not rasterio: Docker install, the image pull (the arm64
  aarch.science image; micromamba plus an offline env install on x86) and spawn's self-terminate
  tick. **These timings are not compute cost** ([layout](../../patterns/layout-and-effective-cost.md)).
- **Fetch is the same 574–605 worker-seconds on every chip** (29–41% of worker time): reading in
  place from the bucket is the data path, and the chip does not move it
  ([data-movement](../../patterns/data-movement.md)).
- **RAM picks the box.** Each worker holds two 10980² bands, their float32 reflectances and its share
  of the composite, peaking at 4.7–5.2 GiB. An earlier run on an `m8g.4xlarge` reached a cgroup peak
  of 58.8 GiB of the 61.7 GiB its kernel reports, so the measurement moved to `r8g`. Fewer workers on
  `m8g` is the cheaper alternative and was not run.
- n = 1 per chip. Every output is deterministic and identical across the three chips (below).

## What held at scale, on all three chips

| claim | why it must hold | Graviton4 | Intel | AMD |
|---|---|---|---|---|
| 459 band objects + 153 STAC items match sha256 pins | provenance | 612/612 | 612/612 | 612/612 |
| every raster's shape, EPSG and transform == its item's `proj:*` | the grid the reference was computed on | 459/459 | same | same |
| one CRS and transform for the whole year | a composite needs one grid | 1 | 1 | 1 |
| **rasterio == tifffile + imagecodecs, every pixel** | two independent TIFF parsers and codecs on the same bytes | **459 rasters, 41.5 G px** | same | same |
| block-windowed reads conserve the full read (SCL histogram, B04 sum) | windowed I/O is the large-area idiom | 306/306 | same | same |
| **ESA's 1,764 published percentages, from the pixels** | the depositors' own numbers | **worst 0.99 of the bound** | same | same |
| **median red reflectance over ESA vegetation > 0, offset chosen per item from the flag** | vegetation reflects some red | **fails: 129 of 130** | same | same |
| NDVI from reflectance within [−1, 1] | algebra, both reflectances > 0 | 153/153 | same | same |
| Σ composite count == Σ per-scene clear observations | conservation through the 16-way reduction | 6,074,075,564 | same | same |
| composite finite exactly where the count is > 0 | no observation, no value | yes | yes | yes |
| both COGs read back equal by rasterio and by tifffile | rasterio's writer, checked by an independent reader | every px | same | same |

**The percentages are reproduced to ESA's own float32 precision, not to six decimals.** The recipe
asserts `< 2e-6` pp on its one scene, justified as "their printed precision". At scale that bound
fails on 70 of 153 scenes. The residuals scale with the value, at a relative 2^-24, not with the print
ulp. So ESA's processor computes the percentage in float32 and then prints six decimals. The bound
here is half the print ulp plus every rounding a float32 evaluation of `100·c/n` can make: converting
`c`, converting `n`, the divide, the scale. That is `5e-7 + 4·2^-24·value`. The worst of the 1,764 is
0.99 of it. That value is a tiny class, where the print half-ulp alone reaches the bound by
construction. **1,478 round to the published six decimals exactly.** Two exceed a single float32
rounding, and both have a count above 2^24 (18,766,743 and 30,122,259), where converting the count to
float32 rounds as well. The bound is narrower than one pixel for 1,441 of the 1,611 class values, so
those counts are pinned to the pixel. On the largest values of a full tile it admits up to 7.3
pixels, because float32 cannot resolve one pixel in 30 million, and so neither can the reference. ESA's
`nodata_pixel_percentage` is SCL == 0 over the whole 5490² grid, to the same precision. The 72 items at
processing baseline 05.11 publish no `s2:dark_features_percentage` (the 81 at 05.10 do), which
leaves 1,764 values rather than 12 × 153.

## What failed: the offset guard

Every item declares `scale 0.0001, offset −0.1` in `raster:bands`, and 150 of 153 also carry
`earthsearch:boa_offset_applied: true`: Earth Search has already taken L2A's +1000 DN out of those
COGs. So the run takes the offset per item from the flag, and checks the choice on the bytes. The
flag is right almost everywhere: applying the declared −0.1 to every item instead would pass the
guard on **3 of 130** scenes with vegetation, and following the flag passes on **129**.

The exception is `S2C_11SKA_20241206_1_L2A` (baseline 05.11). It says `false`, so the run applies
the −0.1. Its median vegetation red is then **−0.0572**, and **+0.0428** without the offset. The same
tile on the same day from S2A, `S2A_11SKA_20241206_0_L2A`, says `true` and measures **+0.0434**. The
pixels are offset-applied and the flag says they are not. Two checks fail on it: the guard itself,
and `every_scene_clean` (152 of 153), which counts the same per-scene failure. The other two items
flagged `false`, `S2C_11SKA_20241209_1` and `S2C_11SKA_20241216_1`, have no vegetation pixels, so
the guard cannot test them. The same mismatch is reported upstream on a 2025 tile, and our case is
added there: Element84/earth-search#66.

The check is left asserting and failing, because it is the claim we mean: the offset a reader would
apply from the metadata has to give physical reflectance. Neither excluding the scene nor letting
the pixels pick the offset would keep that claim. **What it costs the outputs:** the scene enters
the year with the flag's offset, so 0.1 is subtracted from its red and NIR twice. 86,944,090 of its
10 m pixels stay positive, against 120,560,213 on its S2A twin, and its vegetation's mean NDVI is
+0.9076 against the twin's +0.6947. Its 53,426,979 clear observations are in the composite's
count and its NDVI enters the maximum; how many composite pixels it wins was not measured. The other
152 scenes' rows are unaffected, and the percentages do not read the offset at all.

**Reported, not asserted: the recipe's two NDVI claims.** On this year they are tendencies:

| | recipe's method (raw DN) | reflectance (per-item offset) |
|---|---|---|
| p10(vegetation) > p90(not_vegetated) | 127 of 129 scenes | 127 of 129 |
| mean NDVI(water) < 0 | 66 of 76 scenes | 65 of 76; defined on 98.31% of ESA-water 20 m px |
| NIR < red on ESA-water pixels (offset-free) | 18,701,243 of 26,074,266 (71.7%) | same |

With the offset left out, reflectance is DN × 1e-4 and the scale cancels in NDVI, so on the 150
flagged-true items the recipe's raw-DN NDVI *is* reflectance NDVI. The two columns differ only in
masking (reflectance aggregates a 20 m pixel only where all four 10 m values are positive) and on
the three flagged-false items. Both orderings have the same two exceptions: a 99%-cloud scene
(`S2A_11SKA_20241027_0`) and a 10,068-pixel swath edge (`S2A_11SKA_20240213_0`).

## Across chips: fixed point or path

**Nothing differed.** All 24 fixed values in `score.tsv` are identical on Intel and on AMD against
Graviton4 ([`results/compare.txt`](results/compare.txt)). That includes the composite array's
sha256, the count array's, the guard's 129/130, and the **NDVI COG file's bytes** (478,961,858 B,
`2abbfc62…`). All 153 rows of `scenes.tsv` are byte-identical too: every float64 sum, mean and
percentile of every class, printed with `repr`. The composite covers 120,560,168 px, up to 99
observations each, mean NDVI 0.722943117. Nothing here is a path. NDVI is an elementwise IEEE
divide, the 2×2 aggregation is four elementwise adds, and the composite is a max, so each value is
one correctly rounded operation, the same on any chip. libdeflate at one version is deterministic,
so even the compressed bytes agree. The timings are the only thing the chip moved.

## Pins

| | |
|---|---|
| image | `quay.io/aarchsci/earth-observation@sha256:8e011f38aea1…` (arm64), the recipe's; x86: the `earth-observation-51a38824432a` linux-64 twin, 261 packages installed offline |
| data (tier: RODA) | 612 objects of `s3://sentinel-cogs/sentinel-s2-l2a-cogs/11/S/KA/2024/`, sha256 in [`pins.sha256`](pins.sha256), 31.94 GB, read in place |
| reference | the 153 STAC items, copied to the bucket, sha256-pinned beside the bands |
| scripts | `scenes.txt`, `sizes.tsv`, `pin.py`, `real.sh`, `real.py`, pinned in the same file and checked in-task |

## Caveats

- The `sentinel-cogs` path is not immutable. Seven S2C scenes' bands are dated 2026, and at least one
  item was rewritten after its bands. A pin that stops matching fails the scene loudly. The remedy is
  a mirror of the 32 GB, which was not made.
- rasterio 1.5.0 sets `ndarray.shape` on every read, which numpy 2.5 deprecates ("Setting the shape
  on a NumPy array has been deprecated"). It is harmless today and will break when numpy removes it.
  `real.py` filters that one message.
