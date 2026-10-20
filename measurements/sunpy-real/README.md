# sunpy on a whole day of SDO/AIA — Graviton4, Intel and AMD

> **Every AIA 171 Å image of 2017-09-06, the day of the X9.3 flare: 343 full-disk maps, all 8.6
> billion pixels through sunpy's coordinate frames and back, plus a derotated datacube of the
> flaring region.** Graviton4 does it fastest and cheapest: $0.091 billed, against $0.159 on AMD and
> $0.192 on Intel. All 22 assertions hold on all three chips. Intel and AMD are bit-identical, and
> Graviton4 differs from them only in the last few ulps of transform residuals.

Workload: NASA HelioCloud's `aia.lev2_hc` product, JSOC's level-1 AIA registered to 0.5″/px on a
5000 × 5000 grid with Sun centre on the reference pixel. One channel for one whole day at 4-minute
cadence is the unit a solar physicist loads when tracking a flare or an active region. For each
image `real.py`:

- **Checks the bytes JSOC wrote.** It recomputes each file's FITS `DATASUM` and `CHECKSUM` from the
  raw bytes, independently of astropy, and checks JSOC's header statistics against the pixels
  `sunpy.map.Map` loads.
- **Checks the disc geometry in JSOC's own keywords** (`RSUN_OBS`, `R_SUN`) and sunpy's observer
  against JSOC's position vector.
- **Sends all 25,000,000 pixels** through pixel → Helioprojective → Stonyhurst → Helioprojective →
  pixel, and the on-disc ones on to Carrington.
- **Derotates the flaring region**, NOAA 12673 (a 600″ × 600″ cutout, 1202 × 1201 px), onto the
  12:00 frame with `propagate_with_solar_surface` + `reproject_to`. That is sunpy's datacube
  workflow, over ±12 h.

## Run it

```sh
export AWS_PROFILE=aws B=<your bucket>
bash measurements/sunpy-real/stage-inputs.sh "$B"      # 343 server-side copies, each checked against inputs.tsv
for a in arm64 x86_64 amd; do
  spawn task run --wait --spec "$(python3 scripts/spec_arch.py sunpy $a "$B" v3 measurements/sunpy-real/real.task.json)"
done
bash measurements/sunpy-real/collect.sh "$B" v3          # results/<chip>/ and results/compare.txt
```

## Results

`m8g/m8i/m8a.4xlarge` (16 vCPU, 64 GiB). One `real.py` runs 16 single-threaded worker processes
(`OMP_NUM_THREADS=OPENBLAS_NUM_THREADS=1`), one image each at a time. Same staged bytes.

| chip | instance | cores × threads | tool wall | billed | peak RSS (worker / box) | $/hr | compute $/result | billed $/result |
|---|---|---|---|---|---|---|---|---|
| **Graviton4** | `m8g.4xlarge` | 16 × 1 | **330 s** | **454 s** | 1.91 / 25.2 GiB | 0.7181 | **0.0659** | **0.0906** |
| Xeon 6975P-C | `m8i.4xlarge` | 8 × 2 | 722 s | 816 s | 1.92 / 27.4 GiB | 0.8467 | 0.1698 | 0.1919 |
| EPYC 9R45 | `m8a.4xlarge` | 16 × 1 | 501 s | 587 s | 1.92 / 28.0 GiB | 0.9738 | 0.1356 | 0.1588 |

A *result* is the whole day, checked. *Tool wall* is `real.py`. *Billed* is spawn's task window,
wrapper start to stage-out, at spawn's printed price. It includes the Docker install and, on x86,
the env install from its lock.
*Worker* peak RSS is the largest `ru_maxrss` of any worker. *Box* is the cgroup's `memory.peak`.

- **Graviton4 is 2.2× faster than Intel and 1.5× faster than AMD, and the cheapest per result:
  43% below AMD and 53% below Intel.** Per worker, an image takes 15.1 s on Graviton4, 23.0 s on
  AMD (also full cores) and 33.1 s on Intel, where 16 vCPUs are 8 SMT cores, so two workers share
  each core.
- **The coordinate transforms are the workload.** The per-pixel frame round trip is 83–85% of the
  CPU on every chip (4,268 of 5,166 CPU-s on Graviton4). The derotation is 10–12%. Decompressing and
  loading 343 RICE-compressed maps is 5%, and the byte-level checksums 1%. This is numpy
  trigonometry and astropy's frame graph, not BLAS. OpenBLAS is loaded (`neoversev2` on Graviton4,
  `Cooperlake` on both x86 chips) but nothing here reaches a kernel it dispatches.
- **The box is sized by memory per worker.** Each worker peaks at 1.9 GiB, because the transforms
  run in 2.5 M-pixel chunks, so 16 workers fit in ~25–28 GiB. A 32 GiB `c8g.4xlarge` would leave
  no headroom. The 5.7 GB of input is staged to the root disk (`disk_gib` 40), not tmpfs.
- **Sizing came from a measured run.** Six images on an `m8g.xlarge` took 14.5 s each and peaked
  at 1.9 GiB per worker. That predicted 5.2 min on 16 workers, and the run took 5.5.
- n = 1 per chip. Every asserted value is identical across chips (below), so a repeat would only
  measure timing noise.

## What held, on all three chips

| claim | why it must hold | result (every chip) |
|---|---|---|
| FITS `DATASUM`, recomputed from the raw bytes (ones-complement, FITS 4.0 App. J) | JSOC wrote it in 2022 over the compressed heap | 343 / 343 |
| FITS `CHECKSUM`: the whole HDU sums to −0 | same | 343 / 343 |
| `DATAVALS` == pixels loaded, none blank or NaN | 25,000,000 per image | 343 / 343 |
| `DATAMAX` == the stored maximum | integer DN keyword | exact, 343 / 343 |
| `DATAMEDN`, `DATAMIN` within 1 DN of the stored image | integer DN keywords of a quantised image | median exact in 318, ±1 DN in 25; min **+1 DN in all 343** |
| `DATAMEAN`, `DATARMS` vs the pixels | JSOC's float sums over 25 M values; bound 1e-4 | max rel 1.44e-6 / 1.29e-6 |
| `RSUN_OBS == arcsin(RSUN_REF / DSUN_OBS)` | a sphere's tangent half-angle | max rel **1.2e-16** |
| control: `arctan(RSUN_REF / DSUN_OBS)` | a flat disc; must miss by the series' x²/2 | 1.065e-5, ratio to x²/2 0.999991 |
| `R_SUN == RSUN_OBS / CDELT1` | the same radius in pixels | max rel 9.7e-8 (`R_SUN`'s 9 printed digits) |
| sunpy `rsun_obs` == `RSUN_OBS` | the loader reads JSOC's value | exact |
| HPC → HGS finite **exactly** on the disc `arcsin(R/d)` predicts | ray–sphere intersection | **0** of 8.6 G pixels disagree |
| on-disc pixel count vs π r², r = tan(limb)/CDELT | lattice points in a circle, bound 2√2 π r | max \|N − π r²\| 216 px against 16,916 |
| pixel → HPC → HGS → HPC → pixel, 3.91 G on-disc pixels | the frames are inverses | max **8.2e-10 px** |
| Carrington lat == Stonyhurst lat | the two differ by a rotation about the solar axis | max 2.1e-14 deg |
| Carrington − Stonyhurst lon is one constant per image | same | max spread 1.7e-13 deg over 11.4 M pixels |
| self-derotation of the 12:00 frame == the 12:00 cutout | a zero-duration rotation is the identity | max rel 1.0e-10, same finite mask |
| derotated frame correlates with 12:00 better than the raw cutout, every \|Δt\| ≥ 1 h | derotation follows the surface | **313 / 313**; NCC 0.21–0.63 derotated, 0.03–0.48 raw |

**Across chips:** the decompressed pixels (`data_sha256`), both checksums, every header statistic,
every pixel count and the limb mask are identical on all three. **Intel and AMD are identical in
every field**, including the sha256 of each derotated cutout. Graviton4 differs from them only in
ulp-level residuals. The worst differences are 6.8e-14 px in the round trip, 8.5e-14 deg in the
Carrington offset spread, 2.1e-14 in a derotated NCC and 7e-15 relative in a derotated cutout's
sum. These are libm trigonometry roundings in a non-iterative chain. They are neither a fixed point
nor a trajectory. Each is at least four orders of magnitude below the bound on the quantity it
perturbs.

## What the run found

- **`HGLN_OBS` in these files disagrees with JSOC's own position vector.** sunpy's observer
  coordinate agrees with the `HAEX/Y/Z_OBS` vector, read as mean ecliptic **of J2000**, to
  2.7e-9 deg in longitude and 11 m in distance. Read as ecliptic of date, the vector is 0.249°
  away, which is about 17.7 years of precession. `HGLN_OBS` departs from both in a sinusoid with
  a 24-hour period, SDO's geosynchronous one, and amplitude 0.0107° (about 28,000 km at 1 au).
  `CRLT_OBS` sits a constant 0.0017° from the vector's latitude, and sunpy's Carrington longitude
  of the observer a constant 1.1e-4° from `CRLN_OBS`. On the photosphere, 0.0107° of observer
  longitude is about 130 km, or 0.18″. That is below AIA's 0.6″ native pixel, but a pipeline that
  builds its observer from `HGLN_OBS` instead of letting sunpy choose would carry it. Recorded,
  not asserted.
- **JSOC's integer statistics are not quite the stored image's.** `DATAMIN` is exactly 1 DN above
  the stored minimum in all 343 images. `DATAMEDN` is off by 1 DN in 25 of them, in both
  directions. `DATAMAX` is always exact. That fits statistics computed before the final rounding
  to integers. It is a property of the product, not of sunpy.

## Pins

| | |
|---|---|
| AIA 171 Å, 2017-09-06 | `s3://gov-nasa-hdrl-data1/sdac/sdo/aia/20170906/0171/`, all 343 `sdo_aia_h2_*_0171_v1.fits` (RODA tier, NASA HelioCloud, CC0) |
| all 343 objects | ETag + sha256 in `inputs.tsv`, checked at copy time (S3's server-side SHA256) and again in-task (`pins.sha256`) |
| scripts | `run.sh`, `real.py`, `pins.sha256`, each pinned by sha256 inside the spec's command (`mkspec.py`) |
| image | the recipe's `astro@sha256:c09e88de…` (arm64) and its `astro-e2ee19affec3` x86 twin |
