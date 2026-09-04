# R — ordinary least squares on the `cars` dataset, two independent ways

One task. R fits a linear model to a bundled dataset, and the smoke check confirms the
coefficients match the textbook values **and** that `lm()`'s QR fit agrees with the
closed-form normal equations.

> **What this recipe does and does not cover.** It runs one OLS fit on 50 observations —
> enough to prove R 4.5.3 and its numerical stack (LAPACK/openblas via `lm()`, plus the
> tidyverse/data.table ecosystem) work correctly on Graviton4. It is not a benchmark and
> does not exercise a large model, the geo stack (sf/terra), or Rcpp compilation, though
> those all ship in the env.

## Why one task, and why nothing is staged

R is one tool and this is one `Rscript` invocation. The `cars` dataset (speed vs stopping
distance, 50 rows) ships **inside** r-base's `datasets` package, so there is **no input
to stage** and no `stage-inputs.sh`; the image digest is the only pin.

## The check: a reference identity plus an internal cross-validation

`lm(dist ~ speed, data = cars)` is the canonical first example in essentially every R
introduction, and its fitted coefficients are a fixed, citable result:

| | intercept | slope |
|---|---|---|
| textbook `cars` OLS | −17.579095 | 3.932409 |

So the recipe asserts those directly — a reference identity, like Psi4's textbook energy.
And it recomputes the same fit through the **closed-form normal equations**
(`solve(XᵀX, Xᵀy)`), a completely separate code path from `lm()`'s QR decomposition, and
requires the two to agree. That is an internal cross-validation — two independent
computations of the same fit — in the same spirit as `recipes/lammps` (serial vs 2-rank):
it confirms R's linear-algebra numerics, not just that `lm()` returned *something*.

## Pins

| | |
|---|---|
| image | `quay.io/aarchsci/r@sha256:8a6a9624c56ddfc72242eea520335ba7fe8965332d52250c52f0486f1d51cc68` |
| | tag `2026.09.04`, R 4.5.3, cosign-signed, index has one `linux/arm64` manifest |
| input | `datasets::cars`, **bundled in r-base** — nothing staged |

**Data tier: bundled in the image.** `cars` ships with r-base, so the image digest is the
input pin.

**Why R 4.5.3 and not 4.6.1** (the version this recipe was first scoped against): the
entire conda-forge CRAN layer is built against R 4.5 (`r45` build strings) — **zero**
`r-*` packages have an `r46` build — so `r-base` 4.6.1 gives an interpreter with no
ecosystem behind it. aarch.science pinned `r-base >=4.5,<4.6` for exactly this reason, and
the recipe asserts 4.5.3 so a silent drift to a bare 4.6 (which can't co-install any CRAN
package) fails the check. The `r` env is aarch.science's 10th and its first without
Python — so this smoke check is written in R, not the Python used by the other recipes.

## Smoke check

Measured in this image, on this input, before any launch.

| observable | assertion | observed |
|---|---|---|
| R version | exactly 4.5.3 (the `r45` CRAN layer) | 4.5.3 |
| R arch | aarch64 | aarch64 |
| observations | exactly 50 (`cars`) | 50 |
| **OLS intercept** | **−17.579095 ± 1e-4** (textbook `cars` OLS) | **−17.579095** |
| **OLS slope** | **3.932409 ± 1e-4** (textbook) | **3.932409** |
| **`lm()` == normal equations** | **max coefficient diff < 1e-8** (QR vs `solve`) | **7.1e-14** |
| ecosystem loaded | `library(tidyverse)` + `library(data.table)` attach | TRUE |

The two OLS coefficient checks are the reference identity; the `lm`-vs-normal-equations
check is the internal cross-validation (agreement to 7e-14 is machine precision — the two
methods are algebraically identical and this confirms the arithmetic). The ecosystem row
proves the env is more than bare r-base: `r-essentials`/`r-tidyverse` are `noarch`
metapackages that install on arm64 (a subdir listing is not an availability check), so the
tidyverse and data.table are really here.

The check avoids R's `tryCatch(warning=…)` idiom deliberately — a warning handler there
unwinds at the first warning and leaves the rest of a block unrun while still reporting
success, the same vacuous-pass shape worth avoiding in any assertion.

## Resources, and what the timings mean

2 vCPU / 4 GiB, `c8g` (resolves to `c8g.large`), TTL 5m, cap $0.02. The fit is
**~1 second**; `lm()` on 50 rows needs no cores or memory to speak of, and `c8g.large` is
the smallest compute box.

**These timings are not compute cost.** Boot, the Docker install, and pulling the R image
are the whole task; the science is a second. The pull is **~0.86 GB compressed**, and the
`r` env extracts to **~3.5 GB on disk** — the largest env in the catalog (R's compiler
toolchain, needed for `Rcpp`, is most of it), still well within the ~6.1 GiB root. The
recorded run's command window was **83s** (22:45:45 → 22:47:08 UTC), and the OLS
coefficients came back −17.579095 / 3.932409 — matching the textbook fit; the larger
extraction did not inflate the window over the other envs. TTL was **retightened from
that first real run**: 10m → **5m** (~2.3× the ~2-minute instance life), `cost_limit`
$0.03 → $0.02. A loose TTL is a larger blast radius, not caution; the recorded run used
the original 10m.

## Running it

No `stage-inputs.sh` — the dataset is in the image.

```sh
spawn task run --spec recipes/r/01-lm.task.json --wait
```

Then **check the bucket**, every time:

```sh
aws s3 ls s3://scicookbook-942542972736-us-east-1/runs/r/r1/
```

`--wait` exiting 0 does **not** prove the outputs exist (spore-host/spawn#561): the smoke
check runs *inside* the task, and the bucket listing is the second half of it. Expect two
objects (`fit.txt`, `smoke-check.txt`).

**Re-running.** `task_id` is fixed, so a re-run overwrites the previous records. Bump the
`-r1` suffix in both `task_id` and the output prefix to keep both. The script overwrites
its own outputs, so there is no checkpoint guard to defeat.

**Note on parallel launches.** If launched alongside other tasks and it dies with an AWS
`Invalid IAM Instance Profile name` error, that is a transient IAM-propagation race
(spore-host/spawn#572), not a recipe fault — no instance was created, so just re-run it.
