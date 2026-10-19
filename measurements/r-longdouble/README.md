# R's `long double` accumulators on arm64 Linux, measured

> **On Graviton, R's `long double` is 113-bit IEEE quad. `stats::cor`, `sum`, `mean` and `colSums`,
> which all accumulate in it, run 9–55× slower than on same-generation x86.** On the same box, a BLAS
> call that accumulates in `double` is as fast as x86's.

One seeded 1,083 × 15,045 matrix (the [wgcna-real](../wgcna-real/README.md) shape) was run on 8-vCPU
`c8g/c8i/c8a.2xlarge` with the [wgcna](../../recipes/wgcna/README.md) recipe's images. On arm64 that is
aarch.bio R 4.5.3 with OpenBLAS 0.3.34; on x86 it is bioconda R 4.5.2 with 0.3.30. n = 1 per chip.

## Run it

```bash
for a in arm64 x86_64 amd; do ...; done   # probe.task.json, resolved per chip by scripts/spec_arch.py wgcna <arch>
spawn task run --spec "<resolved spec>" --wait
```

[`probe.R`](probe.R) and [`run.sh`](run.sh) are pinned in [`pins.sha256`](pins.sha256). Per-chip output
is in [`results/`](results/).

## Results

| | Graviton4 | Intel Xeon 6975P-C | AMD EPYC 9R45 |
|---|--:|--:|--:|
| `.Machine$longdouble.digits` | **113** (quad) | 64 (x87) | 64 (x87) |
| `stats::cor`, 3,000 columns | 297.2 s | 5.4 s | 6.4 s |
| `stats::cor`, full 15,045 | **7,478 s** | 253 s | 162 s |
| `crossprod(scale(X))/(n−1)`, full | 3.4 s | 2.7 s | 1.6 s |
| `WGCNA::cor`, full | 2.2 s | 1.4 s | 1.2 s |
| `sum(x)` ×10, 2e8 doubles | 27.8 s | 2.3 s | 2.7 s |
| `mean(x)` ×10 | 74.8 s | 7.1 s | 5.4 s |
| `colSums` ×10, same data | 28.8 s | 3.2 s | 2.7 s |
| **control:** BLAS `crossprod(x, 1)` ×10, a `double` dot product | **7.9 s** | 12.3 s | 6.6 s |
| billed | $0.711 | $0.040 | $0.032 |

- **`stats::cor` is 29.5× slower than Intel and 46× slower than AMD** on the full matrix, and 46–55× slower
  on 3,000 columns.
- **`sum`, `mean` and `colSums` are 9–14× slower too,** even though these are O(n) passes over memory.
  Their cost is the per-element arithmetic, not memory bandwidth.
- **The control rules out a slow box.** The same data, reduced by BLAS in `double`, is faster on Graviton4
  than on Intel. The gap follows the accumulator type.
- **The mechanism is inferred, not instrumented.** These images have no profiler. `stats.so` contains none
  of libgcc's quad-routine names (`__addtf3`, `__multf3`, …) on any chip, which suggests the helpers are
  statically linked, so that check cannot decide either way. The evidence is the 113-bit `long double`, the
  `LDOUBLE` accumulators in R's `cov.c`, `summary.c` and `array.c`, and the accumulator-dependent gap with its control.
- **The two builds also differ in R version and channel.** The `double` BLAS control is what separates the
  arithmetic from the box.

## What held

- **`stats::cor` agrees with the BLAS forms to round-off.** Against `crossprod(scale(X))/(n−1)` it is
  ≤ 6.7e-16 on arm64 and ≤ 1.4e-15 on x86. `identical()` is `FALSE` on every chip: summation order differs.
- **One cross-chip digest of the full `stats::cor`** (Σ entry × column index) is equal to 17 printed digits
  on all three chips (111125851.66533279). That does not show the matrices are bit-identical.
- **Missing data:** `WGCNA::cor` defaults to `use = "all.obs"` and **errors** on NA. With
  `use = "pairwise.complete.obs"` it matches `stats::cor(use = "pairwise.complete.obs")` to 3.3e-16 (arm64) and
  8.9e-16 (x86) on a 500 × 200 matrix with 10% NA.
- **Alternatives:**
  - conda-forge `r-coop` has **no linux-aarch64 build** (0.6_1–0.6_3: linux-64, osx-64, win-64 only).
  - `r-rfast` 2.1.5.2 ships linux-aarch64.
  - Neither is in the wgcna images.
- **`--disable-long-double`** is R's configure switch. `configure.ac` declares `--enable-long-double`,
  which defaults to yes.
