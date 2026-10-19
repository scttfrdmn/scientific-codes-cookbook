# Why is R's `cor()` so slow on Graviton?

> **On arm64 Linux, R's `long double` is 113-bit quad precision with no hardware behind it.** Every R
> routine that accumulates in it (`stats::cor`, `cov`, `sum`, `mean`, `colSums`) runs 9–55× slower than on
> same-generation x86. Nothing errors and the answer is right; it only takes much longer.

**Fix:** use a correlation that goes through BLAS ([below](#what-to-use-instead)). On a 1,083 × 15,045
matrix that is 3.4 s against two hours.

## What's happening

R accumulates inner products and sums in `LDOUBLE`, its alias for C `long double`:
`src/library/stats/src/cov.c` for `cor`/`cov`, `src/main/summary.c` for `sum`/`mean`, and `src/main/array.c`
for `colSums`/`rowSums`. The width of `long double` is set by the platform ABI:

| platform | `long double` | `.Machine$longdouble.digits` |
|---|---|---|
| x86-64 Linux | 80-bit extended, x87 hardware | 64 (measured) |
| arm64 Linux (Graviton) | 128-bit IEEE quad, no hardware support | 113 (measured) |
| arm64 macOS | the same as `double` (Apple ABI) | not measured here |

The last row is why a laptop doesn't warn you. On Apple Silicon these loops run at `double` speed, and the
same script slows down only once it reaches Graviton.

## How big it is

One seeded 1,083 × 15,045 matrix on 8-vCPU `c8g`/`c8i`/`c8a.2xlarge`, using the wgcna recipe's images
([measurement](../measurements/r-longdouble/README.md)):

| | Graviton4 | Intel Xeon 6975P-C | AMD EPYC 9R45 |
|---|--:|--:|--:|
| `stats::cor(X)`, 15,045 columns | **7,478 s** | 253 s | 162 s |
| `crossprod(scale(X)) / (n − 1)` | 3.4 s | 2.7 s | 1.6 s |
| `WGCNA::cor(X)` | 2.2 s | 1.4 s | 1.2 s |
| `sum` / `mean` / `colSums`, ×10 over 2e8 values | 28 / 75 / 29 s | 2.3 / 7.1 / 3.2 s | 2.7 / 5.4 / 2.7 s |
| control: a BLAS `double` dot product, ×10 | **7.9 s** | 12.3 s | 6.6 s |

- **`stats::cor` is 29× slower than Intel and 46× slower than AMD.** `sum`, `mean` and `colSums` are 9–14×
  slower, even though they are memory-bound passes.
- **The control rules out a slow box.** On the same Graviton4 instance, a reduction that accumulates in
  `double` is faster than Intel's. The gap follows the accumulator type, which fits software quad-precision
  arithmetic. That reading is inferred, not profiled: the images carry no profiler.
- **Two effects are mixed in the 7,478 s against 3.4 s.** One is the accumulator. The other is the
  algorithm: a scalar triple loop against a blocked BLAS call. The second exists on x86 too (253 s against
  2.7 s), so at this size `stats::cor` is the wrong tool on any chip.

## What to use instead

- **Complete data, base R:** `crossprod(scale(X)) / (nrow(X) - 1)`. It dispatches to BLAS, so it is only
  fast with an optimised BLAS. Check that the `BLAS:` line of `sessionInfo()` names OpenBLAS or similar,
  not R's reference BLAS.
- **Missing values:** `WGCNA::cor(X, use = "pairwise.complete.obs")`. Its default is `use = "all.obs"`,
  which **errors** on NA. With pairwise, it matched `stats::cor(use = "pairwise.complete.obs")` to 3e-16 to
  9e-16 on a 500 × 200 matrix with 10% missing.
- **A packaged alternative:** `Rfast::cora()`, which conda-forge builds for linux-aarch64. `coop` has no
  linux-aarch64 build on conda-forge.
- **Rebuild R with `--disable-long-double`.** `configure.ac` declares `--enable-long-double`, defaulting to
  yes. This removes the cost everywhere, but it lowers accumulation precision in `sum`, `mean`, `cov` and
  more across the whole installation. It suits a deliberately built environment, not a default to impose on
  others.

## The answers still agree

The BLAS forms match `stats::cor` to ≤ 6.7e-16 on Graviton4 and ≤ 1.4e-15 on x86. `identical()` between
methods is `FALSE` on every chip, because summation order differs, so compare with `all.equal()` and a
stated tolerance. Across chips, a weighted digest of the full `stats::cor` agreed to 17 printed digits on
all three. That is not a proof that the matrices are bit-identical, so this page claims no cross-chip
difference either way.

Related: [cross-architecture](cross-architecture.md), for the other arm64 builds where the toolchain, not
the core, sets the speed.
