---
tool: r
tool_version: 4.5.3
env: r
image: quay.io/aarchsci/r@sha256:8a6a9624c56ddfc72242eea520335ba7fe8965332d52250c52f0486f1d51cc68
spawn_version: 0.104.0
last_verified: 2026-09-10
---
# R (r env) — ordinary least squares on `cars`, two independent ways

`Rscript` fits an ordinary-least-squares linear model to the bundled `cars` dataset — R's numerical and statistical stack (LAPACK via `lm()`, plus the tidyverse).

## Run it

```bash
Rscript -e 'fit <- lm(dist ~ speed, data = cars); coef(fit)'
# (Intercept) -17.579095   speed 3.932409
```

One task, one `Rscript` invocation. `cars` (speed vs stopping distance, 50 rows) ships inside r-base's `datasets` package, so nothing is staged.

## Make it yours

| In the recipe | Swap for | What to know |
|---|---|---|
| `datasets::cars` (50 rows, bundled) | your own data frame | the canonical first R example — its coefficients are a fixed, citable result, so a tiny bundled dataset gives a hand-checkable reference (that's the point, not a limit). |
| `lm(dist ~ speed)` | your model / a CRAN package's fit | the tidyverse, data.table, sf/terra and Rcpp all ship in the `r` env; scale the analysis freely. |
| the R **4.5.3** pin | leave it | **load-bearing:** the entire conda-forge CRAN layer is built against R 4.5 (`r45` build strings) — *zero* `r-*` packages have an `r46` build, so a bare R 4.6 is an interpreter with no ecosystem. The check asserts 4.5.3 so a silent drift to 4.6 fails. |

Deterministic — **nothing is determinism scaffolding**. **Leave the fixture:** 50 observations give an exact textbook identity and a machine-precision cross-check; a bigger model is a longer run, not a more legible one. Leave-it.

## Shape, size, cost

One task, `c8g.large` (2 vCPU / 4 GiB), TTL 5m, cap $0.02. The fit is ~1 s. Recorded command window **83s** — boot, Docker install, and the R image pull are the whole task ([why](../../practices/what-this-does-not-cover.md)). The `r` env is the catalog's largest, **~0.86 GB compressed → ~3.5 GB extracted** (R's compiler toolchain for `Rcpp`), still well within the ~6.1 GiB root; the extraction did not inflate the window. **These timings are not compute cost.**

<details>
<summary>As shipped: the reference identity, the internal cross-validation, pins, smoke check, run + verify</summary>

### The checks — a reference identity plus an internal cross-validation

`lm(dist ~ speed, data = cars)` has fixed, citable coefficients (intercept −17.579095, slope 3.932409), asserted directly — a [reference identity](../../practices/reference-from-tests.md), like Psi4's textbook energy. The recipe also recomputes the fit through the **closed-form normal equations** (`solve(XᵀX, Xᵀy)`), a separate code path from `lm()`'s QR decomposition, and requires the two to agree — an internal cross-validation (agreement to 7e-14 is machine precision), the same spirit as [lammps](../lammps/README.md)'s serial-vs-2-rank. It confirms R's linear-algebra numerics, not just that `lm()` returned something.

The check avoids R's `tryCatch(warning=…)` idiom deliberately: a warning handler unwinds at the first warning and leaves the rest of a block unrun while still reporting success — the same vacuous-pass shape worth avoiding in any assertion.

### Pins (data tier: bundled in the image)

| | |
|---|---|
| image | `quay.io/aarchsci/r@sha256:8a6a9624c56ddfc72242eea520335ba7fe8965332d52250c52f0486f1d51cc68` (tag `2026.09.04`, R 4.5.3 `r45` CRAN layer, cosign-signed, `linux/arm64`) |
| input | `datasets::cars`, bundled in r-base — nothing staged |

The `r` env is aarch.science's first without Python, so this smoke check is written in R.

### Smoke check (inside the task; measured before launch)

| observable | assertion | observed |
|---|---|---|
| R version | exactly 4.5.3 (the `r45` CRAN layer) | 4.5.3 |
| R arch | aarch64 | aarch64 |
| observations | exactly 50 (`cars`) | 50 |
| **OLS intercept** | −17.579095 ± 1e-4 (textbook) | −17.579095 |
| **OLS slope** | 3.932409 ± 1e-4 (textbook) | 3.932409 |
| **`lm()` == normal equations** | max coefficient diff < 1e-8 (QR vs `solve`) | 7.1e-14 |
| ecosystem loaded | `library(tidyverse)` + `library(data.table)` attach | TRUE |

The ecosystem row proves the env is more than bare r-base: `r-tidyverse`/`data.table` are `noarch` metapackages that install on arm64 (a subdir listing is not an availability check).

### Run + verify

`r` builds its input in the task, so there's nothing to stage:

```sh
make run RECIPE=r      # substitutes your COOKBOOK_BUCKET, runs on a Graviton4 box
make ls  RECIPE=r      # the outputs: fit.txt, smoke-check.txt
```

The smoke check runs *inside* the task (a bad run fails the task); the bucket listing is the second half — an exit code says the command ran, never that its output is real. A re-run overwrites `runs/r/r1/` — `make run` launches a fresh task each time, no spec edit needed.

</details>
