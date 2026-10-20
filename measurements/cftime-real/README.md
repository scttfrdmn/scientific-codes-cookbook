# cftime on a real workload — every time axis in CMIP6 on AWS, three chips

> **cftime decodes every decodable one of the 7.29 billion time values in the Pangeo CMIP6 archive on AWS — 983,590
> time and bounds variables, 88 models, all seven CF calendar spellings — and agrees with an
> exact-integer calendar implementation on every one, on all three chips, byte for byte.**
> The archive is not as clean: 236 stores carry float64 bytes under an int64 label, and
> xarray's default decode silently wraps 1,395 dates by 584 years. AMD finishes first;
> Graviton4 is cheapest per result.

Workload: the time coordinate, and its bounds, of **every one of the 512,171 zarr stores** in
the Pangeo CMIP6 catalog on AWS Open Data (`s3://cmip6-pds`), decoded the way a user meets
them — `xr.decode_cf` and `cftime.num2date` on each store's own `units` and `calendar` — and
then checked value by value. This is the honest scale for a calendar library: cftime's real
job is decoding model time axes, and the variety that breaks a decoder (`noleap`, `360_day`,
`julian`, `proleptic_gregorian`, piControl runs starting in year 1, scenarios to 2300, a GISS
run to year 7895, float and int encodings, inherited bounds units) only shows up across the
whole archive. Any one model would test one calendar.

## Run it

```sh
export AWS_PROFILE=aws B=<your bucket>
bash measurements/cftime-real/stage-inputs.sh "$B"            # the catalog, pinned, + the scripts
spawn task run --spec "$(python3 scripts/spec_arch.py cftime arm64 "$B" "$(date +%s)" measurements/cftime-real/00-fetch.task.json)" --wait
bash measurements/cftime-real/stage-inputs.sh "$B" axes       # pin what 00-fetch wrote
for a in arm64 x86_64 amd; do
  spawn task run --spec "$(python3 scripts/spec_arch.py cftime $a "$B" "$(date +%s)" measurements/cftime-real/01-decode.task.json)" --wait
done
bash measurements/cftime-real/collect.sh "$B"                # results/, results/compare.txt
```

`00-fetch` runs once (51 min on a `c8g.2xlarge`, $0.28): `fetch.py` reads each store's
`.zmetadata` and the raw, still-compressed chunks of `time` and its bounds, in-region, into 16
shards (2.1 GB of chunks, 1,099,105 objects, each with its S3 VersionId and sha256).
`01-decode` runs `decode.py` over the shards on every core and `check.py` asserts the result.
Per store, per variable, the work is the plain calls:

```python
dates = cftime.num2date(values, units, calendar=calendar)
back  = cftime.date2num(dates, units, calendar=calendar)            # == values
xr.decode_cf(ds, decode_times=xr.coders.CFDatetimeCoder(use_cftime=True))   # == dates
```

## Results

32 vCPU, one `8xlarge` (64 GiB) per chip, 32 worker processes, same versions (cftime 1.6.5,
xarray 2026.9.0, numpy 2.5.3, python 3.14.8), same staged bytes:

| chip | instance | cores × threads | decode wall | $/hr | compute $/result | billed | **billed $/result** | peak mem |
|---|---|---|---|---|---|---|---|---|
| **Graviton4** | `c8g.8xlarge` | 32 × 1 | 2,085 s | 1.2762 | **0.739** | 2,203 s | **0.781** | 13.0 GiB |
| Intel (Xeon 6975P-C) | `c8i.8xlarge` | 16 × 2 | 2,781 s | 1.4994 | 1.158 | 2,872 s | 1.196 | 16.0 GiB |
| AMD (EPYC 9R45) | `c8a.8xlarge` | 32 × 1 | **1,716 s** | 1.7243 | 0.822 | **1,796 s** | 0.860 | 15.5 GiB |

A *result* is the whole archive decoded and checked. *Decode wall* is `decode.py`; *billed* is
spawn's task Started → Ended, so it also covers staging the 2.8 GB of shards (12–17 s), Docker,
the image pull or the x86 env install, and `check.py`. Peak memory is cgroup `memory.peak`: 32
workers at ≤2.6 GiB RSS each plus the 2.8 GB staged in tmpfs.

- **AMD vs Graviton4: 1.21× faster, 10% dearer per result.** Both rent 32 full cores; the c8a
  hour costs 35% more, which outruns its speed. The work is CPython creating and comparing cftime
  objects — 8.9 µs of CPU per value on Graviton4 across all the checks — so it is per-core
  interpreter speed.
- **Intel vs Graviton4: 1.33× slower, 53% dearer per result.** `c8i`'s 32 vCPUs are 16 cores × 2
  hyperthreads, so the 32 workers share cores pairwise — the like-for-like rental, not a per-core race.
- **CPU-bound and evenly loaded**: 64,562 CPU-seconds in 2,085 s on 32 cores is 97% utilisation.
  The fixed cost (boot, Docker, pull, staging) is under 2 minutes of a 30–48 minute bill.
- **The fetch is the expensive part to repeat, so it is done once.** 1.6 million anonymous
  in-region GETs at ~530/s from one Python client took 51 minutes; staging the pinned shards
  into each chip's box takes 12–17 s. `cmip6-pds` is Payer: BucketOwner, so the GETs cost this
  account nothing; in a bucket you paid for they would be ~$0.65 ([request charges](../../patterns/data-movement.md)).
- n = 1 per chip. Every output is deterministic and identical across chips (below), so a repeat
  would only re-measure timing.

## What held, on all three chips

Over **7,291,094,172 values** in 983,590 variables (`check.py`, every bound asserted):

| claim | why it must hold | all three chips |
|---|---|---|
| every catalog store accounted for exactly once; no store fails to process | provenance | 512,171 of 512,171 |
| `num2date` succeeds wherever the values fit cftime's int64 µs | decode | 983,590 of 983,590 |
| **`date2num(num2date(v)) == v` exactly**, integer encodings | round trip | 2,585,993,119 of 2,585,993,119 |
| float encodings back within cftime's 1 µs resolution, and stable on re-decode | round trip | 4,705,101,053; max 0.999998 µs |
| **cftime == an exact-integer referee** (`refcal.py`, no shared code), (Y, M, D, h, m, s, µs) elementwise | independent implementation | **0 differ** |
| `xr.decode_cf(use_cftime=True)` == `cftime.num2date` | the path users take | 0 differ |
| consecutive datetimes subtract (cftime's calendar arithmetic) to the stored step | second code path | 0 differ |
| control: the same values under a different calendar decode differently | the referee check has teeth | 972,743 of 972,743 year-long axes |
| every cftime refusal is an exact int64 overflow, and xarray refuses too | refusals are right, not luck | 236 of 236 |

The referee is ~170 lines of integer arithmetic from the CF calendar definitions and Julian Day
Numbers; for float encodings it multiplies the binary value exactly in Python integers, so it
does not share cftime's `longdouble` step (80-bit on x86, 128-bit on Graviton). Agreement on
4.7 billion float values, on both widths, says that step never mis-rounds a CMIP6 time.

## What the archive says about itself (reported, not asserted)

| | stores |
|---|---|
| calendars: `noleap` 292,612 · `gregorian` 105,794 · `proleptic_gregorian` 63,344 · `360_day` 18,988 · `365_day` 8,756 · `julian` 3,324 · `standard` 68 | 493,122 with a time axis |
| **time bytes are float64, `.zarray` says int64** (IPSL-CM6A-LR, CNRM-CM6-1, CNRM-ESM2-1): reading as labelled overflows, so cftime and xarray both refuse — loudly | 236 |
| absent time chunk with no `fill_value` / chunk bytes larger than the declared shape | 4 / 10 |
| `.zmetadata` missing | 7 |
| time not strictly increasing / with duplicate times | 541 / 101 |
| a time outside its own bounds / bounds not contiguous | 830 / 4,198 |
| `climatology` bounds with no units (CF says: inherit the coordinate's); xarray leaves them as numbers | 1,650 |
| float times with sub-µs noise that cftime rounds away (706.9999999999995 h → 707 h) | 20,121 variables, 449,328 values |
| distinct decoded time axes among 493,122 stores | 2,289 |

**xarray's default decode (numpy `datetime64[ns]`, not cftime) silently wraps three MRI-ESM2-0
ssp585 axes.** They run 2015–2300 but are stored out of order, first value 2101 and last 2100;
both ends fit `datetime64[ns]` (which ends in 2262), so xarray takes the fast path, and the 465
values after 2262 in each overflow to 1677–1715 — off by 2⁶⁴ ns (584.5 years), with no warning.
`use_cftime=True` decodes all 1,395 correctly. Every other `datetime64` value (3.16 billion) lies
within xarray's 1 ns resolution of the exact time; 2.40 million float encodings are truncated to
the nanosecond below (largest gap 215 ns), which is resolution, not error.

## Across chips: all fixed points, all identical

Calendar arithmetic is integer, so nothing here is a path. `summary.tsv` — 70 rows, including
the sha256 of the full per-store (159 MB) and per-variable (486 MB) results, which hold a digest
of every decoded date array — is **byte-identical on Graviton4, Intel and AMD**
(`results/compare.txt`). That includes the float encodings, where cftime's `longdouble` differs
by chip. No OpenBLAS is on the path.

## Pins

| | |
|---|---|
| catalog | `s3://cmip6-pds/pangeo-cmip6.csv.gz` VersionId `KCB936FiD1GjuGHQxrzBPr.RL.MsTp8U`, sha256 `48afd7ec…78db88` (RODA tier; the bucket is versioned) |
| data | `inputs/cftime-real/axes/`: 16 shards, sha256s in `axes.sha256`; every chunk inside carries its source key, S3 VersionId and sha256, re-checked on read |
| scripts | `fetch.py`, `decode.py`, `refcal.py`, `check.py`, in `pins.sha256`, checked in-task |
| image | the recipe's `climate@sha256:7c6b07a0…` (arm64) and its `climate-8c7f1e0cc15d` x86 twin |

Raw summaries per chip are in `results/`. The staged copy is 2.8 GB (~$0.06/month); delete
`inputs/cftime-real/` when done.
