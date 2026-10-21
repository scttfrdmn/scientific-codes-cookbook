# r-arrow on 378 million NYC taxi trips: Graviton4, Intel and AMD

> **36 Parquet files of NYC taxi trips (12.0 GB, 378 M rows) are queried with `open_dataset()` and
> dplyr, rewritten as one partitioned dataset, and queried again. Arrow's part takes 62–93 s.** All four
> result tables match polars (a separate Parquet reader and engine) and data.table line for line, and
> they are byte-identical on all three chips. AMD is fastest and cheapest. Graviton4 is the slowest
> here, and still cheaper than Intel on compute.

Workload: **every NYC yellow-taxi trip of 2009, 2010 and 2023**. That is 36 monthly Parquet files from
the TLC, 12,007,738,158 bytes and 378,207,443 rows, in three schema eras. 2009 and 2010 name their
columns differently from each other and store pickup times as strings. 2023 changes column types from
month to month. This is the job Arrow's dataset API exists for: many files, more rows than you want in
R. Projected to seven numeric columns, the rows would need at least 21 GB in memory, against Arrow's
6 GiB peak here. Arrow's dataset vignette uses this dataset; its full record (~1.7 B rows) is the same
job at 4.5× the size.

## Run it

```sh
export AWS_PROFILE=aws B=<your bucket>
bash measurements/r-arrow-real/stage-inputs.sh "$B"     # reuses datashader-real's 2009-2010 and r-real's 2023, + scripts
spawn task run --wait --spec "$(python3 scripts/spec_arch.py r-arrow arm64 "$B" r1 measurements/r-arrow-real/00-reference.task.json)"  # polars, once
for a in arm64 x86_64 amd; do
  spawn task run --wait --spec "$(python3 scripts/spec_arch.py r-arrow $a "$B" r1 measurements/r-arrow-real/real.task.json)"
done
AWS_PROFILE=aws python3 measurements/r-arrow-real/collect.py "$B" <spawn results bucket> r1
```

```r
d <- open_dataset(files_2023)            # NOT unify_schemas = TRUE (below)
d |> mutate(file = add_filename(), fare_c = as.integer64(floor(fare_amount * 100 + 0.5))) |>
  group_by(file) |> summarise(n = n(), fare = sum(fare_c, na.rm = TRUE)) |> collect()
q |> mutate(month = 12L) |> write_dataset("rw", partitioning = c("year", "month"))
open_dataset("rw") |> filter(year == 2023, month == 12) |> count() |> collect()
```

[`analysis.R`](analysis.R) is the whole job. It runs four queries per era (per-file totals, payment
type, pickup hour, a filtered trip-distance histogram), then `write_dataset()`s each file into one
`year=/month=` hive dataset, then runs the same four queries over that one dataset, then a
partition-pruned count. [`reference.py`](reference.py) computes the same four tables in polars.
[`run.sh`](run.sh) checks the staged bytes against [`pins.sha256`](pins.sha256), and
[`mkspec.py`](mkspec.py) writes the specs.

## Results

`m8g/m8i/m8a.4xlarge` (16 vCPU, 64 GiB), `set_cpu_count(16)`, the same `r` env versions on all three
(aarch.science's arm64 image, and its `r-d98eb9641259` linux-64 twin on both x86 chips), the same
bytes:

| chip | instance | cores × threads | tool wall | billed | peak RSS | $/hr | compute $/result | billed $/result |
|---|---|---|---|---|---|---|---|---|
| Graviton4 | `m8g.4xlarge` | 16 × 1 | 93.1 s | 668 s | 6.01 GiB | 0.7181 | 0.0186 | 0.1332 |
| Xeon 6975P-C | `m8i.4xlarge` | 8 × 2 | 88.7 s | 582 s | 6.10 GiB | 0.8467 | 0.0209 | 0.1369 |
| **EPYC 9R45** | `m8a.4xlarge` | 16 × 1 | **62.3 s** | **438 s** | 6.01 GiB | 0.9738 | **0.0168** | **0.1185** |

| phase | 4 queries × 3 eras, raw files | `write_dataset`, 36 files | 4 queries, rewrite | pruned count | data.table check |
|---|---|---|---|---|---|
| Graviton4 | 14.1 s | 74.3 s | 4.5 s | 0.04 s | 454 s |
| Intel | 16.6 s | 67.4 s | 4.6 s | 0.05 s | 404 s |
| AMD | 10.0 s | 49.0 s | 3.2 s | 0.03 s | 295 s |

A *result* is the whole job: every query, the rewrite, and the queries on the rewrite. *Tool wall* is
those phases (footers included). Compute $/result is that at the hourly price. *Billed* is spawn's task
window, wrapper start to stage-out, and billed $/result is spawn's `cost_estimate` for it. *Peak RSS*
is the R process's `VmHWM`, data.table check included.

- **Writing is 76–80% of Arrow's time; querying is cheap.** Four grouped queries over 378 M rows take
  10–17 s. Over the rewritten dataset they take 3–5 s, because the rewrite already holds integer cents
  and parsed hours. Writing 2.38 GB of snappy Parquet takes 49–74 s.
- **AMD is 1.49× faster than Graviton4, and Intel 1.05× faster.** Graviton4 still beats Intel on
  compute $/result ($0.0186 vs $0.0209), because its hour costs less. AMD's hour costs 1.36× Graviton4's,
  but its speed more than pays for that.
- **The billed column is mostly the checks.** On Graviton4, 454 s of the 668 s window is the data.table
  cross-check, which reads every file back into R. Staging 12 GB took 52 s, Docker and the image 43 s,
  and Arrow 93 s. A user's own run would bill about a third of this. These timings are not compute cost
  ([layout](../../patterns/layout-and-effective-cost.md)).
- **The tmpfs set the box, not Arrow.** Arrow's peak was 6 GiB. But the staged 12.0 GB and the 2.3 GB
  rewrite reached 14.0–14.6 GB of `/tmp`, and `/tmp` is half the RAM. A 32 GiB box would leave 16 GiB
  of tmpfs, which is too close, so this is a 64 GiB `m8g` and not a `c8g`.
- **n = 1 per chip, plus one Graviton4 repeat.** Every asserted output is an integer, and identical
  across chips. The repeat gave 92.1 s against 93.1 s.

## What held at scale, on all three chips

All of it is asserted inside the task by `analysis.R`.

| identity | value (identical on all three) |
|---|---|
| rows per file == Parquet footer, 36 files | 378,207,443 |
| rewrite's 36 footers sum to the input's | 378,207,443 |
| partition-pruned `year=2023/month=12` count == that file's footer | 3,376,567 |
| **rewrite == raw**, every line of all four tables | 36 + 23 + 72 + 252 lines |
| **Acero == data.table**, the same decoded columns, file by file | all four tables |
| **Acero == polars 2.0.0**, its own Parquet reader and engine, line for line | all four tables |
| payment and hour groups add back to the rows and to the per-file cents totals | exact |
| `total_amount` over every row, in integer cents | 487,662,901,880 |
| 2009–2010 pickup strings `strptime` could not parse | 0 |

Money is `floor(x·100 + 0.5)` as int64 in all three engines, using the same IEEE operations, so every
sum is exact in any order and no table needs a tolerance. Integer cents also keep the sums out of
R's `long double` accumulators, which are software quad precision on Graviton
([r-long-double-on-arm64](../../patterns/r-long-double-on-arm64.md)). data.table sums `integer64`.

The polars comparison is the strong one. data.table checks Acero's grouping, but it reads its columns
through Arrow, so a decoder bug would hit both. polars reads the same bytes with its own Rust Parquet
reader. It works file by file, so the 2023 schema drift never reaches a dataset scanner, and it parses
the pickup strings with its own datetime parser. It ran once on Graviton4 ([`00-reference.task.json`](00-reference.task.json),
28.8 s for all four tables) and its four CSVs are pinned in [`ref.sha256`](ref.sha256). The runs on
every chip must reproduce those bytes. polars comes from aarch.science's `neuroimaging` env, as pinned
by `recipes/nilearn`: no env carries DuckDB, and `r` has no second Parquet reader.

## Across chips: fixed point or path

**Every asserted output is fixed.** The four tables have the same sha256 on all three chips, and equal
the polars reference ([`results/crosschip.txt`](results/crosschip.txt)). 14 of the 15 non-timing
records match.

**The one that differs is a path, and it varies between runs on one chip too.** The rewrite's total
size was 2,384,133,940 bytes on Graviton4, 2,384,133,947 on the Graviton4 repeat, 2,384,133,931 on
Intel and 2,384,133,921 on AMD. That is ≤ 26 bytes of 2.38 GB. The tables read back from it are
identical every time, so the rows are the same and their byte layout is not. The files stayed on the
instance, so the cause is not established. A likely one is that `write_dataset()` does not promise to
keep row order when it writes with many threads. Nothing should assert on the bytes of a multithreaded
`write_dataset()`.

## Sharp edges hit on the way

- **`open_dataset(files_2023, unify_schemas = TRUE)` fails**:
  `Unable to merge: Field VendorID has incompatible types: int64 vs int32`. January 2023 stores
  `VendorID` and `passenger_count` with other types than the rest of the year. The default works:
  Arrow takes the first file's schema and casts the later files to it. That is the opposite of what
  the option's name suggests.
- **Grouping on `add_filename()` works; the two expressions tried on it did not.** Both
  `as.integer(substr(file, …))` and `case_when(file == … ~ …)` stopped with "Expression not supported in
  Arrow". So the rewrite sets `year` and `month` as constants, one `write_dataset()` per file, and the
  per-file table groups on the path itself.

## Pins

| | |
|---|---|
| data | NYC TLC yellow-taxi trip records, 2009-01 … 2010-12 and 2023-01 … 2023-12, from the TLC's CloudFront origin. RODA's `s3://nyc-tlc` refuses every request ([r-real](../r-real/README.md)). 2009–2010 were fetched on AWS by [datashader-real](../datashader-real/README.md) and 2023 by [r-real](../r-real/README.md). [`pins.sha256`](pins.sha256) concatenates their two pin lists, and every staged file is checked against it in-task. |
| reference | polars 2.0.0 tables, sha256-pinned in [`ref.sha256`](ref.sha256); the CSVs are in [`results/reference/ref/`](results/reference/ref/) |
| scripts | `run.sh`, `analysis.R`, `pins.sha256`, `ref.sha256`, pinned by sha256 in the spec's command |
| images | the recipe's `r@sha256:136bf063…` (arm64) and its `r-d98eb9641259` x86 twin: arrow / libarrow 25.0.0, dplyr 1.2.1, data.table 1.18.6.1, R 4.5.3 on all three chips. The reference uses `neuroimaging@sha256:f57c5cc3…` (polars 2.0.0). |

The per-chip outputs are in `results/<chip>/`.

## Spend

Twelve launches, **about $0.60**. The three verifying runs cost $0.39 and the Graviton4 repeat $0.13.
The polars reference cost $0.03. Two schema probes and seven 4-file wiring probes cost $0.06. The
wiring probes found the two sharp edges above.
