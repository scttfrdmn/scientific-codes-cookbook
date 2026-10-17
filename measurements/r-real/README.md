# R on a year of NYC taxi trips — Graviton4 and x86

> **A full year of yellow-cab trips — 38.3 M rows read, aggregated three ways, and a 34 M-row `lm()` —
> takes 77–84 s of R and about $0.035 billed on either chip.** Every exact number agrees bit for bit
> across chips and with DuckDB. The regression coefficients agree to 1e-10.

Workload: the **complete** 2023 NYC TLC yellow-taxi trip records (12 monthly Parquet files, 606 MiB,
38,310,226 trips), not a sample month. It is the size an analyst loads into memory: it fills a 64 GiB
box to half, and the next step up (all years, ~1.7 B rows) would no longer be an in-memory R session.
That would be `arrow::open_dataset`, a different shape of job.

Data tier: RODA lists this dataset as `s3://nyc-tlc`, but that bucket now returns 403 to anonymous
reads (checked 2026-10-17). So the files come from the TLC's own CloudFront distribution, pinned by
sha256 as fetched. The TLC has re-issued months before, and a re-issue fails staging rather than
quietly changing the year.

## Run it

```sh
export AWS_PROFILE=aws COOKBOOK_BUCKET=<your bucket>
bash measurements/r-real/stage-inputs.sh "$COOKBOOK_BUCKET"     # 12 files, sha256-pinned, + the 2 scripts
sed "s|\${COOKBOOK_BUCKET}|$COOKBOOK_BUCKET|g" measurements/r-real/tlc2023.task.json > /tmp/r.json
spawn task run --spec /tmp/r.json --wait                                    # r8g.2xlarge
spawn task run --spec "$(python3 scripts/spec_arch.py r x86_64 "$COOKBOOK_BUCKET" x1 \
  measurements/r-real/tlc2023.task.json)" --wait                            # r8i.2xlarge
```

The analysis is [`analysis.R`](analysis.R): `arrow::read_parquet` → `data.table`, the same group sums
in data.table, dplyr and base `rowsum()`, then
`lm(fare_amount ~ trip_distance + duration_min + hour + wday)` on the 34,184,942 standard-rate trips.

## Results

8 vCPU / 64 GiB on both, same R 4.5.3 versions, same input bytes, default threads (data.table 4,
arrow 8):

| chip | instance | R wall | billed | peak RSS | $/hr | compute $/result | billed $/result |
|---|---|---|---|---|---|---|---|
| Graviton4 | `r8g.2xlarge` | **77.3 s** | 287 s | 31.66 GiB | 0.4713 | **0.0101** | 0.0376 |
| Intel Xeon 6 | `r8i.2xlarge` | 84.4 s | **226 s** | 31.49 GiB | 0.5557 | 0.0130 | **0.0349** |

| phase | read 12 files | data.table agg | dplyr agg | base `rowsum` | `lm()` (QR) | `crossprod` + `solve` |
|---|---|---|---|---|---|---|
| Graviton4 | 4.8 s | 0.61 s | 2.30 s | 5.03 s | **15.3 s** | 17.5 s |
| x86 | 6.3 s | 0.90 s | 2.61 s | 5.41 s | 28.0 s | **12.8 s** |

- **Graviton4 does the R work 1.09× faster and 22% cheaper.** `lm()`'s LINPACK Householder QR runs 1.8×
  faster on it. The BLAS-3 normal equations (`crossprod`) run 1.4× faster on x86. Neither chip wins
  every phase.
- **The x86 run bills less, and that is packaging, not the chip.** The arm64 task pulls the 0.86 GB
  aarch.science `r` image (80 s). The x86 twin pulls a small micromamba image and installs the
  same versions offline from staged packages (2 s pull, 10 s stage-in, 4 s install). Swap the
  routes and the billed column would flip.
- **Peak RSS 31.5–31.7 GiB on both.** `lm(x = TRUE)` keeps the 34 M × 32 design matrix *and* its QR
  (8.8 GB each). That is why this is `r8g` and not `c8g`. The staged input is under 1 GiB of the
  32 GiB tmpfs.
- n = 1 per cell. Every exact output is deterministic and identical across chips (below), so a second
  run would add a timing sample and nothing about correctness.

## What held at scale, on both chips

All asserted inside the task by [`check.R`](check.R) against references computed from the same bytes
by DuckDB 1.5.6 ([`duckdb-reference.py`](duckdb-reference.py)), an independent Parquet reader and
engine:

| identity | Graviton4 | x86 |
|---|---|---|
| rows decoded == Parquet footers == DuckDB | 38,310,226 | 38,310,226 |
| total in integer cents == DuckDB | 109,038,341,189 | 109,038,341,189 |
| group sums (62 month×payment, 263 zones) add back to N and to the total | exact | exact |
| data.table == dplyr (tolerance 0) == base `rowsum()` | TRUE | TRUE |
| both aggregate tables, sha256 == DuckDB's | match | match |
| model rows under the filters == DuckDB | 34,184,942 | 34,184,942 |
| `lm` rank | 32 of 32 | 32 of 32 |
| normal equations, max cos(Xⱼ, r) < √N·ε (1.3e-12) | 6.5e-15 | 3.3e-16 |
| QR vs `solve(XᵀX, Xᵀy)`, rel. < √N·κ²·ε (4.0e-7, κ = 553.8) | 3.0e-10 | 4.2e-13 |

Money is summed as `round(total_amount*100)`, so every partial sum is an exact integer below 2^53
whatever the order. That is what lets three R engines and DuckDB agree bit for bit, with no
tolerance. Every fare is a whole number of cents to within 1.9e-9, so the rounding is unambiguous.
The √N in both linear-algebra bounds is the rounding of an N-term inner product, and the run earned
it: arm64's 3.0e-10 is 4.4× past the bare κ²ε (6.8e-11).

## What differed across chips — path, not fixed point

- **The integer outputs are identical.** Both aggregate CSVs have the same sha256 on both chips
  (`results/agg-*.sha256`), as do the counts, totals and rank.
- **The coefficients differ in the 10th–11th digit** (largest absolute difference 1.1e-10, intercept
  2.1904801166 vs …1167; [`results/coef-*`](results/)). A direct least-squares solve is not an
  iterated fixed point. `lm`'s QR calls OpenBLAS level-1 kernels (`ddot`, `dnrm2`), which are chosen
  per host, so the rounding path differs. It is reported, not asserted. The economics are the same
  on both chips: $2.66/mile plus $0.485/minute against the TLC meter's $3.50/mile *or* $0.70/minute.
- **arm64's own consistency checks are looser**: residual orthogonality is 20× larger and QR-vs-normal-
  equations is 700× larger. Both stay ≥ 200× inside their bounds. The OpenBLAS core name was not
  captured, so which kernel is responsible is unmeasured.

## Spend

Two launches, one per chip, both first-attempt green: $0.0376 + $0.0349 ≈ **$0.07**.
