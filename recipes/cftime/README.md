---
tool: cftime
tool_version: "1.6.5"
env: climate
image: quay.io/aarchsci/climate@sha256:7c6b07a02ca6e5b8907c19b0a930d177f137a58783b89135d84ae4d9b9a3a63b
env_x86_64: climate-8c7f1e0cc15d
spawn_version: 0.127.0
last_verified: 2026-10-11
last_verified_x86_64: 2026-10-17
last_verified_amd: 2026-10-18
---
# cftime — CF calendar arithmetic against published constants

Checks the time axis itself on Graviton4: every CF calendar, two published astronomical constants, and 292,194 days cross-validated against two unrelated implementations. For anyone whose netCDF time coordinate has to be right.

## Run it

```bash
make stage RECIPE=cftime     # once: the checks only — the references are published constants
spawn task run --spec "$(make -s spec RECIPE=cftime)" --wait
make ls RECIPE=cftime
PG = "proleptic_gregorian"   # next: J2000.0 is Julian Day 2451545.0 by convention, not cftime's opinion
cftime.date2num(cftime.datetime(2000, 1, 1, 12, calendar=PG), "days since -4713-11-24 12:00:00", calendar=PG)
cftime.num2date(np.arange(292194.0), "days since 1600-01-01", calendar=PG)   # == stdlib datetime, == numpy
```

## Make it yours

| In the recipe | Swap for | What to know |
|---|---|---|
| `proleptic_gregorian` for the stdlib check | `standard` | **not interchangeable.** stdlib and numpy are *proleptic* Gregorian; CF `standard` is mixed Julian/Gregorian with a 10-day hole at 1582. Comparing against `standard` shows a 10-day "bug" that is a calendar difference ([match the modes](../../practices/cross-checks.md)). |
| the `standard` control | the agreement alone | **keep it.** A three-way match across 292,194 days would also hold for code that ignored `calendar=` entirely. The control is what makes the agreement mean something. |
| 1600–2400 | your own span | stdlib `datetime` cannot represent year < 1, and `numpy.datetime64[D]` tops out near ±9999 — so a wider span loses one or both referees. |
| `noleap` / `360_day` | `standard` | model output often uses these, and they are **not** approximations to fix up: a `360_day` year is 12 exact 30-day months, so day-of-year arithmetic that assumes 365 is wrong by 5 days by December. |
| these calendars | a real file's axis | decode with `use_cftime=True`: xarray's default `datetime64[ns]` path silently wrapped three CMIP6 axes past 2262 ([measured](../../measurements/cftime-real/README.md)). [climate](../climate/README.md) and [cdo-nco](../cdo-nco/README.md) move real fields. |

**Leave the fixture.** Nothing is fetched and no file is read: the references are published constants (JD 2451545.0, MJD 51544), the CF calendar definitions, and two implementations that ship with Python. **Scale it** by running the same cross-check over your own span — the method transfers, but mind the referees' range limits above.

## Shape, size, cost

One task on `m8g.large` (2 vCPU / 8 GiB), TTL 30m as a **backstop** with `cost_limit` $0.12 as the real guard. All checks, including 292,194 days converted three ways, are **6 s** of a 1m10s window; 39 s is installing Docker and 24 s pulling the image ([layout](../../patterns/layout-and-effective-cost.md)).

**Which box — [measured](../../measurements/cftime-real/README.md)** on every time axis in CMIP6 on AWS:

| chip | instance | decode wall | $/hr | **billed $/result** |
|---|---|---|---|---|
| Graviton4 | `c8g.8xlarge` | 2,085 s | 1.2762 | **0.781** |
| Intel | `c8i.8xlarge` | 2,781 s | 1.4994 | 1.196 |
| AMD | `c8a.8xlarge` | **1,716 s** | 1.7243 | 0.860 |

All 7.29 billion time values in 512,171 zarr stores, 88 models, seven calendar spellings: cftime matches an exact-integer referee on every one, and the results are byte-identical on all three chips. **Graviton4 is cheapest, AMD fastest.** The run also found 236 stores whose time bytes are float64 under an int64 label, and three where xarray's default (non-cftime) decode silently wraps 1,395 dates by 584 years; `use_cftime=True` gets them right.

<details>
<summary>As shipped: two published constants, a three-way cross-check over 292,194 days, and the control that stops it being vacuous</summary>

### The checks

| observable | assertion | observed |
|---|---|---|
| staged bytes | match pin | 1 of 1 |
| cftime / numpy / python | read from the running install | **1.6.5** / 2.5.3 / 3.14.8 |
| **Julian Day of J2000.0** | **`== 2451545`** (published) | **2451545** |
| **MJD of 2000-01-01** | **`== 51544`** (published) | **51544** |
| **vs stdlib `datetime`** | **all 292,194 days** | **292194 of 292194** |
| **vs `numpy.datetime64`** | **all 292,194 days** | **292194 of 292194** |
| **`num2date`→`date2num`** | **`== 0` exactly** | **0** |
| control: `standard` pre-reform | **must diverge** | +6 d (1000), +10 d (1500, 1582) |
| control: `standard` post-reform | must agree | 0 d (1600, 1900, 2000, 2400) |
| **1582 reform, `standard`** | **Oct 4 → Oct 15 is 1 day** | **1** |
| same in proleptic / julian | 11 days (no reform) | 11 / 11 |
| the 10 deleted dates | unconstructible | **10 of 10 rejected** |
| julian Feb 29 | exists 1900, 2000, 2100 | all three |
| standard / proleptic Feb 29 | 2000 only | 2000 only |
| year length, 9 calendars | 360 / 365 / 366 exactly | all exact |
| `360_day` month starts | `0, 30, …, 330` | exact |
| units round-trip, 5 resolutions | exact | days→µs all exact |
| determinism | two conversions agree | identical |

### Two numbers that are not cftime's to choose

Most checks in this catalog are internal identities or sibling-code agreements. These two are
neither — they are fixed by astronomical convention and were published long before any of this
software:

```text
J2000.0                 JD 2451545.0     cftime: 2451545
MJD of 2000-01-01          51544         cftime: 51544
```

Staging closes both *without* cftime, from stdlib arithmetic and the defined offset
`JD = MJD + 2400000.5` (plus half a day, because J2000.0 is noon and MJD 0 is midnight) — so the
assertion is against an external fact, not against the library's own view of itself.
([Reaching for a code's own committed reference](../../practices/reference-from-tests.md) is the
related move when no external constant exists.)

### Three implementations, 292,194 days, and why the calendar had to match first

Every day from 1600-01-01 to 2400-01-01 is converted by cftime and compared, elementwise, against
Python's stdlib `datetime` and against `numpy.datetime64`. Three unrelated codebases:

```text
cftime 1.6.5  vs  stdlib datetime      292194 of 292194 agree
cftime 1.6.5  vs  numpy.datetime64     292194 of 292194 agree
num2date -> date2num round trip        max |diff| = 0
```

**Getting there required picking the right calendar, and the wrong choice looks exactly like a
bug.** stdlib and numpy are *proleptic* Gregorian — the Gregorian rule extended backwards forever.
CF's `standard` is the historical mixed calendar: Julian before October 1582, Gregorian after. Run
the same comparison on `standard` and cftime appears to be 10 days out in 1582 and 6 days out in
the year 1000, with no bug anywhere. The fix is to compare like with like, the same discipline the
[cross-checks](../../practices/cross-checks.md) page sets out.

### The control, which is what makes the agreement worth anything

A three-way match over 292,194 days is a weak result on its own: an implementation that **ignored
the `calendar=` argument entirely** and always did proleptic Gregorian would pass it perfectly. So
the discarded comparison is kept as the control, and its divergence is asserted:

```text
standard vs proleptic stdlib, same dates
  year 1000    +6 days      diverges  (Julian drift at that date)
  year 1500   +10 days      diverges
  year 1582   +10 days      diverges  (the reform)
  year 1600     0 days      agrees
  year 1900     0 days      agrees
  year 2000     0 days      agrees
  year 2400     0 days      agrees
```

The run **fails if that divergence disappears**, with the message that the cross-check has gone
calendar-blind. It also fails if `standard` diverges *after* the reform, which it must not. The
offset is 6 days in the year 1000 rather than 10 because the Julian/Gregorian gap grows by three
days every four centuries — so this pair of numbers is itself a check on the drift, not a single
fact repeated.

### The calendars, as definitions rather than observations

The reform and the leap rules are not measurements with error bars; they are what the calendars
*are*, so they can be asserted flat:

```text
1582-10-04 -> 1582-10-15     standard 1 day      proleptic 11      julian 11
1582-10-05 .. 10-14          rejected by standard: 10 of 10
Feb 29                       julian: 1900, 2000, 2100     gregorian: 2000 only
year length                  360_day 360   noleap 365   all_leap 366
360_day months               start at 0, 30, 60, ... 330
```

The 1900/2100-versus-2000 split is the sharpest of these: Julian makes every fourth year a leap
year, Gregorian drops century years not divisible by 400, so the two calendars agree on 2000 and
disagree on both 1900 and 2100. A single year could not tell them apart.

### Pins

| | |
|---|---|
| data | **none.** References are published constants (JD 2451545.0, MJD 51544), the CF calendar definitions, and stdlib + numpy as independent referees |
| checks | `identities.py`, pinned by sha256 |
| image | `quay.io/aarchsci/climate@sha256:7c6b07a02ca6e5b890…` — cftime 1.6.5, xarray 2026.9.0, netcdf4 1.7.4, numpy 2.5.3, python 3.14.8 |

cosign-verified against `playgroundlogic/aarchsci`; the signature covers the **manifest-list**
digest, so verify the tag and pin the arm64 digest.

### On x86_64

The same task on `m8i.large`, through the `climate` env's linux-64 twin
(`envs-x86_64/climate-8c7f1e0cc15d.linux-64.lock`: 251 of 262 versions identical; the 10 that
differ are libraries off cftime's path — `libcurl`, `libsqlite`/`sqlite`, `libglib`, `libzip`,
`magics`, `fonttools`, `platformdirs`, `charset-normalizer`, `backports.zstd`). cftime, numpy and
python are the same versions, and `score.tsv` is **byte-identical** to arm64: both published
constants, 292,194 of 292,194 days against stdlib and numpy, every control offset. Calendar
arithmetic is integer, so nothing here may differ.

### Run + verify

```sh
make stage RECIPE=cftime
spawn task run --spec "$(make -s spec RECIPE=cftime)" --wait
aws s3 cp "s3://$(make -s print-bucket)/runs/cftime/r1/score.tsv" -
```

Fails on a pin mismatch, a Julian Day or MJD that misses its published value, a single disagreeing
day against either referee, an inexact round-trip, a `standard` calendar that stops diverging from
proleptic before 1582 or starts diverging after it, a reform gap that is not exactly one day, a
deleted date that can be constructed, a leap-year rule that contradicts its own calendar, a wrong
year length or month structure, or any lossy unit conversion — but check the bucket regardless
([exit 0 isn't proof](../../practices/container-path.md)).

### Not covered

**Reading a real time axis — in this recipe.** No netCDF file is opened here. File-backed
`xarray.decode_cf` and `num2date`, with real `units`, `calendar` and bounds attributes, are covered
at archive scale by [cftime-real](../../measurements/cftime-real/README.md) (zarr, not netCDF).

**`date2index` and `time2index`**, the search functions, including their `select="before"/"after"/
"nearest"` modes. **Leap seconds and UTC/TAI**, which CF does not model and this does not raise.
**Sub-microsecond resolution** and the `has_year_zero` flag, where cftime's own semantics changed
across versions. **Timezone-aware input** — everything here is naive, as CF requires. **Arithmetic
on cftime objects** (`timedelta` addition, comparison across calendars), exercised only incidentally
by the round-trip.

</details>
