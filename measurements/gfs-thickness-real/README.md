# MetPy + eccodes on a real workload — a whole GFS forecast cycle at 0.25°, three chips

> **All 209 forecast hours of one GFS cycle decode message-for-message against NOAA's own
> inventory, and MetPy's hypsometric thickness agrees with the file's geopotential within 0.75% on
> 120 million above-ground columns, on all three chips.** Graviton4 is the cheapest per result;
> AMD is fastest by 3%; about 60% of every bill is staging 113 GB.

Workload: **every forecast hour of `gfs.20251001/00`**: f000–f120 hourly, then 3-hourly to f384.
That is 209 GRIB2 files at 0.25° (113.1 GB, 155,240 messages, 1.6e11 decoded values) plus the 209
wgrib2 inventories NOAA publishes beside them. It is the same cycle
[recipes/gfs-thickness](../../recipes/gfs-thickness/README.md) reads at 1°. Per hour, eccodes
decodes every message, and cfgrib opens `t`/`r`/`gh` the recipe's way. MetPy then computes the
1000–500 hPa thickness for all 1,038,240 columns in one call, and θ over the whole 3-D temperature
field. A forecast cycle is the unit NOAA publishes and the unit a forecaster or a case study works
through. A season of cycles is this task fanned out, not a different workload.

## Run it

```sh
export AWS_PROFILE=aws B=<your bucket>
bash measurements/gfs-thickness-real/stage-inputs.sh "$B"     # server-side copy from RODA, once
for a in arm64 x86_64 amd; do
  spawn task run --spec "$(python3 scripts/spec_arch.py gfs-thickness $a "$B" "$(date +%s)" measurements/gfs-thickness-real/gfs-cycle.task.json)" --wait
done
spawn task run --spec "$(python3 scripts/spec_arch.py gfs-thickness arm64 "$B" "$(date +%s)" measurements/gfs-thickness-real/compare.task.json)" --wait
```

`gfs.py` (pinned by sha256 in the spec) runs one process per forecast hour, 32 at a time. The heart
of it is the recipe's call on a whole grid, vertical axis last:

```python
w = mpcalc.mixing_ratio_from_relative_humidity(p, T, rh)        # p (13,), T/rh (721, 1440, 13)
dz = mpcalc.thickness_hydrostatic(p, T, mixing_ratio=w)          # (721, 1440)
```

## Results

32 vCPU, one `r8.8xlarge` (256 GiB) per chip, same versions (MetPy 1.7.1, cfgrib 0.9.15.1, eccodes
2.48.0, numpy 2.5.3), same input bytes:

| chip | instance | cores × threads | gfs.py wall | $/hr | compute $/result | stage-in | billed | **billed $/result** | peak RSS (worker / container) |
|---|---|---|---|---|---|---|---|---|---|
| Graviton4 | `r8g.8xlarge` | 32 × 1 | 127.6 s | 1.8851 | **0.0668** | 477 s | 829 s | **0.4341** | 2.68 / 56.1 GiB |
| Xeon 6975P-C | `r8i.8xlarge` | 16 × 2 | 222.5 s | 2.2227 | 0.1374 | 645 s | 1007 s | 0.6217 | 2.69 / 57.7 GiB |
| EPYC 9R45 | `r8a.8xlarge` | 32 × 1 | **124.1 s** | 2.5562 | 0.0881 | 515 s | **822 s** | 0.5837 | 2.69 / 47.7 GiB |

A *result* is the whole verified cycle: every check below, on all 209 hours. **Billed** is launch
to terminate. **Stage-in** is spawn copying the cycle from the bucket into `/tmp`.

- **AMD and Graviton4 are level on compute (124 vs 128 s). Intel takes 1.74× as long**, because its
  32 vCPUs are 16 cores with SMT and the work is 32 single-threaded processes. Per result,
  Graviton4 is 24% under AMD on compute and 26% under it billed. Intel costs the most either way.
- **The work is eccodes decoding.** Of 3,804 process-seconds on Graviton4, 62% is decoding all 743
  messages per hour and 28% is cfgrib's three opens. MetPy's whole-grid thickness is 8%, about
  1.4 s per hour for a million columns, and θ is 2%.
- **Staging decides the bill, not compute.** Copying 113 GB into tmpfs took 477–645 s, about 60%
  of every billed window, against about 2 minutes of work. A cycle-sized job is
  [data-movement-bound](../../patterns/data-movement.md). The cheaper shape for a stream-once
  read like this one would overlap fetching with decoding. If you only need the thickness,
  range-get its 40 messages (5% of each file's 743) through the `.idx` offsets. Most of the 477 s
  is the CLI's default concurrency. With the CRT client the same copy takes 63 s ($0.128 a cycle),
  and lith on a 128 GiB box ties with that
  ([lith-vs-copy-real](../lith-vs-copy-real/README.md)).
- **Memory picks the family.** tmpfs is half the RAM, so 113 GB of staged input needs `r8.8xlarge`
  (124 GiB `/tmp`, 87% full). The 32 workers add only about 50 GiB on top (2.7 GiB each). Nothing
  smaller holds the cycle.
- n = 1 per cell. Decoding is deterministic and identical across chips (below), so a repeat would
  only re-measure timing and staging noise.

## What held at scale, on all three chips

| claim | why it must hold | all three chips |
|---|---|---|
| 418 staged files == the MD5 that S3 serves at source (single-part ETags); sha256 recorded | provenance | 418 / 418, identical `gfs-cycle.sha256` |
| eccodes decodes **155,240** messages == **155,240** `.idx` records | decode vs the publisher's manifest | equal, every hour |
| every message starts at the `.idx` byte offset; messages tile each file exactly to its last byte | nothing skipped, nothing extra | 209 / 209 |
| every message's `endStep` == the `.idx` forecast end-hour == the file's hour; reference time 2025-10-01 00Z | right file, right step | 155,240 / 155,240 |
| every TMP/RH/HGT isobaric record is the eccodes field the inventory names, at that pressure | names agree across two decoders | 0 mismatches |
| 1,038,240 points per message; missing values == the bitmap count wherever one exists | full decode | 5,260,523,634 bitmap-missing points, all accounted for |
| cfgrib's `t`, `r`, `gh` share one 33-level vector, and every level is bit-identical to eccodes' own decode | the recipe's cfgrib trap, at scale | 209 × 99 fields identical |
| MetPy's grid thickness == an independent numpy trapezoid in ln p | same formula, separate code; would catch an axis error | max rel 8.6e-16 |
| MetPy's grid call == its documented 1-D profile call on 205 columns per hour | MetPy documents thickness as 1-D only | max rel 3.9e-16 |
| **hypsometric thickness vs the file's own Z500 − Z1000, every column with surface pressure ≥ 1000 hPa** | **the recipe's identity, ≤ 2% (discretisation)** | **max 0.745%** (f015, 38.0°N 61.25°W); p99 ≤ 0.051%; median 0.0066% |
| θ(p,T) → T(p,θ) over the whole 3-D field | involution | max 5.7e-14 K over 7.16e9 values |

**The 2% band holds only where 1000 hPa is real air.** Below ground, where the surface pressure is
under 1000 hPa (45% of columns: continents, ice sheets), GFS's 1000 hPa temperature and height are
extrapolated. There the file disagrees with itself by up to **4.43%** (median 0.016%). That is
outside the recipe's bound. It is a property of the extrapolation, not of MetPy. So the mask
is part of the claim: the recipe's five fixture points all sit above ground.

## Across chips: the decode is identical, the thickness differs in the last bits

- **Decode (fixed point): byte-identical on all three.** One sha256 over all 1.6e11 decoded
  values is `2f03901b…` on Graviton4, Intel and AMD. eccodes' unpacking is integer arithmetic
  plus one scale, and every count and offset agrees too.
- **Thickness: Intel == AMD bit for bit, both ≠ Graviton4 in the last bits.** Of 216,992,160
  thickness values, **162,675 (0.075%)** differ between Graviton4 and either x86 chip. The largest
  difference is **2.7e-12 m, 3 ulp** (relative 4.7e-16). Intel and AMD agree on every value. This is
  a closed-form expression, not a path: `log` and `exp` (in the saturation vapour pressure) feed a
  13-term sum, and no difference grows. Every per-hour statistic above prints identically on all
  three. The pattern (x86 against ARM, not vendor against vendor) points at the vectorised `exp`/`log`
  numpy dispatches per ISA, not at OpenBLAS: no BLAS call is on this path (corename `neoversev2`
  on Graviton4, `Cooperlake` on both x86 chips). That cause is unverified.

## Pins

| | |
|---|---|
| data | `s3://noaa-gfs-bdp-pds/gfs.20251001/00/atmos/gfs.t00z.pgrb2.0p25.f{000…384}` and `.idx`, 418 objects, 113,141,207,953 B (NOAA Open Data Dissemination on the AWS Open Data Registry, us-east-1; RODA tier). Source MD5s in `gfs-cycle.md5` (every object is single-part, so its ETag is its MD5); sha256s in `gfs-cycle.sha256`, computed in-task after the MD5 check and identical on all three chips |
| scripts | `gfs.py` sha256 `791d2081…`, `compare.py` `d8f420b1…` (both checked in-task) |
| image | the recipe's `climate@sha256:53ce76bc…` (arm64) and its `climate-b7b5178faf93` x86 twin |

Raw outputs per chip are in `results/` (`run.log`, `per-hour.tsv`, `summary.json`, `host.txt`, `pin.log`; each chip wrote a `gfs-cycle.sha256` identical to the committed one);
`results/compare.log` is the elementwise comparison. The staged cycle is 113 GB in your bucket
(~$2.60/month in S3 Standard). Delete `inputs/gfs-thickness-real/cycle/` when done.
