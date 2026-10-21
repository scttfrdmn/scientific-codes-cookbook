---
tool: scikit-learn
tool_version: "1.9.1"
env: geo-ml
image: quay.io/aarchsci/geo-ml@sha256:83ca6331cd84cbae0f60987e9536704ce201904bdfe98d3398aff57e8061aae9
env_x86_64: geo-ml-ffc4dc5282aa
spawn_version: 0.127.0
last_verified: 2026-10-13
last_verified_x86_64: 2026-10-17
last_verified_amd: 2026-10-18
---
# scikit-learn — its own 47,445 tests, then closed forms you can lift

Runs scikit-learn's own test suite on Graviton4 with every test accounted for, then checks a logistic MLE against statsmodels, PCA against an eigensolver, and KMeans against planted clusters. For anyone doing machine learning on ARM.

## Run it

```bash
make stage RECIPE=scikit-learn    # once: 5 pinned pytest wheels + the checks (the suite ships in the image)
spawn task run --spec "$(make -s spec RECIPE=scikit-learn)" --wait
make ls RECIPE=scikit-learn

python -m pytest --pyargs sklearn -n "$PHYSICAL_CORES"            # sklearn's own installed-package model
LogisticRegression(C=np.inf).fit(X, y)                            # unpenalised -- NOT penalty=None
PCA().fit(A).explained_variance_  ==  eigvalsh(np.cov(A.T))[::-1] # to 5e-14
```

## Make it yours

| In the recipe | Swap for | What to know |
|---|---|---|
| `LogisticRegression(C=np.inf)` | `penalty=None` | **`penalty=` is deprecated since 1.8 and removed in 1.10** — this pin warns. An unpenalised fit is the `C → ∞` limit, so `C=np.inf` is the form that keeps working. |
| counting outcomes per test | adding up pytest's summary line | **the summary over-counts by one**: its `skipped` includes a skip raised at *collection* time (`test_docstrings.py`), which is not a test. 47,446 in the summary, 47,445 tests. |
| pytest staged as 5 wheels | `pip install pytest` in the task | the image ships sklearn's tests but **not pytest**. The wheels are pure Python (`py3-none-any`), pinned by sha256 and installed offline — staging, not fetching at run time. |
| `-n <physical cores>` | serial | sklearn's own [`test_wheels.sh`](https://github.com/scikit-learn/scikit-learn/blob/1.9.1/build_tools/wheels/test_wheels.sh) model. 127–151 s on 8 cores. |
| synthetic data with a planted `beta` | your data | the logistic check needs an MLE that **exists** — perfectly separable data has none, and two solvers then "agree" on whatever iterate they happen to stop at. |

**Leave the fixtures.** Every check here is a fixed point or an exact identity, so the data only needs to be well-posed, not realistic — and synthesising it keeps the run offline. **Scale it** by running the suite's slow and float32 tests (`SKLEARN_RUN_FLOAT32_TESTS=1`), which this run leaves skipped.

## Shape, size, cost

One task on `c8g.2xlarge` (8 vCPU / 16 GiB), TTL 60m as a **backstop** with `cost_limit` $0.60 as the real guard. The suite takes **127–151 s** across five runs and the identities a few seconds, inside a ~4-minute window ([layout](../../patterns/layout-and-effective-cost.md)).

## Which box — [measured](../../measurements/scikit-learn-real/README.md) (all 581,012 Covertype rows, 8 threads)
| chip | instance | fit: logreg / HGB / RF | **billed $/result** |
|---|---|---|---|
| Graviton4 | `c8g.2xlarge` | 49.0 / 85.6 / 10.6 s | 0.0732 |
| Intel | `c8i.2xlarge` | 63.1 / 88.4 / 10.6 s | 0.0859 |
| AMD | `c8a.2xlarge` | 29.6 / 65.2 / 8.3 s | **0.0695** |

**AMD fits fastest (1.3–1.7× Graviton4) and bills 5% less per run; Graviton4 is cheapest per tree-ensemble fit, Intel dearest throughout.** The logistic optimum agrees across chips to 1.7e-12 and the forest is bit-identical; boosting differs ARM vs x86 in the last bits (Intel == AMD) at the same accuracy (0.9698). Set `l2_regularization` for multiclass boosting — the default 0 diverges on this data.

<details>
<summary>As shipped: 36,879 passing tests with every one accounted for, a cross-code MLE to 2e-13, and two exact identities</summary>

### The checks

| observable | assertion | observed | x86_64 (c8i.2xlarge) |
|---|---|---|---|
| staged wheels / checks | match pins | 5 of 5 / 2 of 2 | identical |
| sklearn / statsmodels / numpy | read from the running install | **1.9.1** / 0.15.0 / 2.5.4 | identical |
| BLAS | recorded | OpenBLAS 0.3.34 `neoversev2` | OpenBLAS 0.3.34 `Cooperlake` |
| **suite: failed / errors** | **`== 0`** | **0 / 0** | identical |
| suite: passed / skipped / xfailed / xpassed | recorded | 36,879 / 10,222 / 223 / 122 | 36,879 / 10,222 / **227** / **118** |
| **every collected test has one outcome** | **exact: 0 missing, 0 uncollected, 0 multiple** | **47,445 of 47,445** | identical |
| **summary == items + collection-time skips** | **exact** | **47,446 = 47,445 + 1** | identical |
| collected | ≥ 47,000 (a floor against a shrinking collection) | 47,445 | identical |
| **logistic MLE, sklearn vs statsmodels** | **`< 1e-6`** | **2.232e-13** | identical |
| **score equation `max |X'(y−p)|`** | **`< 1e-6`** | **4.707e-11** | **4.711e-11** |
| control: L2 penalty (`C=0.01`) | norm must shrink and coefficients move > 1e-3 | 2.4542 → 1.8016 | identical |
| **PCA vs `eigvalsh` (SVD vs symmetric eig)** | **rel `< 1e-10`** | **5.290e-14** | **2.086e-13** |
| PCA components orthonormal | `< 1e-12` | 1.443e-15 | 1.998e-15 |
| **RSS/(n−1) == dropped eigenvalues**, k = 5, 10, 15 | **rel `< 1e-9`** | **1.9e-16, 0, 8.1e-16** | **5.8e-16, 1.2e-15, 5.3e-15** |
| **KMeans, separated planted clusters** | **ARI `== 1.0`** | **1.000000** | identical |
| control: overlapping clusters | ARI `< 0.9` | 0.288 | identical |

### The suite is the verdict — and the count needed care

scikit-learn ships its tests inside the package, which makes this the
[reference-from-tests](../../practices/reference-from-tests.md) shape at full scale: 259 test
files, run with the project's own installed-package model rather than one invented here.

"0 failed" is not enough on its own, because a missing optional import that drops a whole module
shows up as *fewer tests*, not as a failure. So the run counts collection in a **separate**
`--collect-only` process and records every report from inside pytest with a 20-line plugin
(`account.py`), then asserts that each collected test produced exactly one outcome. The summary
line cannot be used as that count: its `skipped` includes a skip raised while **collecting**
(`tests/test_docstrings.py`, which needs `numpydoc`), and that is not a test — so it reads 47,446
against 47,445 tests. The run asserts both exact statements: per-test accounting, and summary =
tests + collection-time skips.

The 10,222 skips are reported by reason, not waved through. They are almost all **optional
backends this env does not carry**: array-API checks for `torch` (3,605), `dpnp`, `array_api_strict`
and `cupy`, opt-in float32 tests (811), `SCIPY_ARRAY_API` unset, and `polars`/`numpydoc` absent.
None is a skip that hides an arm64 problem.

### A fixed point two codes must share

An unpenalised logistic regression is an MLE, so it is defined by the score equation
`X'(y − p) = 0` — and any two correct solvers must land on the same point:

```text
sklearn (newton-cholesky)   [-0.3649634583  0.7351511802 -1.1395743305  0.5556707130 -0.0244012035  1.9684255307]
statsmodels (Newton)        [-0.3649634583  0.7351511802 -1.1395743305  0.5556707130 -0.0244012035  1.9684255307]
max |difference|            2.232e-13          score equation at sklearn's solution  4.707e-11
```

The bound is `1e-6`, set loose against both solvers' `1e-12` tolerance because Newton converges
quadratically — a correct fit sits orders inside it. Because the MLE is a fixed point and not a
path, kernel choice cannot move it past convergence, so this agreement does not depend on which
OpenBLAS kernel the host selected. The control shows the comparison can see a different
estimator: the same data under an L2 penalty (`C=0.01`) moves the coefficients by **0.52** —
five orders of magnitude past the 1e-6 bound — and shrinks the norm from 2.454 to 1.802, as a
ridge penalty must.

### Two LAPACK routes, and an identity instead of a tolerance

PCA is the eigendecomposition of the sample covariance. sklearn gets there by SVD of the centred
data; `numpy.linalg.eigvalsh` by the symmetric eigensolver — they agree to **5.3e-14**. The
truncation check is stronger than a tolerance on reconstruction error: the residual sum of squares
over `n − 1` **equals** the sum of the discarded eigenvalues, and it does — to **0 and 2e-16**
relative at 5, 10 and 15 of 20 components.

### Pins

| | |
|---|---|
| data | **none staged** — logistic data with a planted β, a correlated Gaussian for PCA, and four planted clusters, all from `default_rng(20261013)` |
| test runner | `pytest 9.1.1`, `pluggy 1.6.0`, `iniconfig 2.3.1`, `pytest-xdist 3.8.0`, `execnet 2.1.2` — pure-Python wheels from PyPI by exact version, sha256-pinned in `stage-inputs.sh`; pytest clears sklearn 1.9.1's own floor of 7.1.2 |
| checks | `identities.py` and `account.py`, pinned by sha256 |
| image | `quay.io/aarchsci/geo-ml@sha256:83ca6331cd84…` — scikit-learn 1.9.1, statsmodels 0.15.0, numpy 2.5.4, scipy 1.18.1, python 3.14 |

cosign-verified against `playgroundlogic/aarchsci`; the signature covers the **manifest-list**
digest, so verify the tag and pin the arm64 digest.

**x86_64.** The same spec with `ARCH=x86_64` runs on `c8i.2xlarge` through the `geo-ml-ffc4dc5282aa`
twin (`envs-x86_64/`: the pinned image's versions as linux-64 builds, installed offline). sklearn,
statsmodels, numpy and scipy are the same versions; 11 of 367 packages differ, none in the
computation (charset-normalizer, fonttools, libcurl, libglib, libsqlite/sqlite, libzip,
open-radar-data, platformdirs, tifffile, wrapt). The suite collects the same 47,445 tests and
passes with **0 failed, 0 errors** on 4 workers (`joblib`'s physical-core count; 170 s); four
non-strict xfails that pass on Graviton4 fail on x86 (xfailed 223 → 227, xpassed 122 → 118), which
the run records and does not assert. The logistic MLE is a fixed point and its cross-code agreement
is **identical** (2.232e-13), as are both KMeans ARIs and the L2 control. The rounding-level values
move — score-equation residual, PCA vs `eigvalsh`, the RSS identity — with OpenBLAS on
`Cooperlake` here and `neoversev2` on Graviton4; every one stays orders inside its bound.

### Run + verify

```sh
make stage RECIPE=scikit-learn
spawn task run --spec "$(make -s spec RECIPE=scikit-learn)" --wait
aws s3 cp "s3://$(make -s print-bucket)/runs/scikit-learn/r1/suite-summary.txt" -
```

Fails on a pin mismatch, pytest not installing from the staged wheels, any failed or erroring
test, a collected test with no outcome or more than one, a report for a test that was never
collected, a summary that does not equal tests plus collection-time skips, fewer than 47,000 tests
collected, an MLE that disagrees with statsmodels or does not satisfy the score equation, an L2 fit
that fails to shrink, PCA eigenvalues off the eigensolver's, a truncation that breaks the RSS
identity, or KMeans missing a planted cluster — or recovering an overlapping one, which would mean
the check cannot tell the difference. Check the bucket regardless
([exit 0 isn't proof](../../practices/container-path.md)).

### Not covered

**Model selection.** Nothing here tunes hyperparameters or cross-validates; the
[real-workload measurement](../../measurements/scikit-learn-real/README.md) fits three model
families on full Covertype, but at fixed settings. **The optional backends**: the array-API path
through torch/cupy/dpnp is 6,000+ of the skipped tests, and GPU execution is Round Two.
**float32**, opt-in and skipped (811). **Threading and parallel scaling**: the suite runs on 8
workers, but nothing measures `n_jobs` or BLAS-thread behaviour, which is where an arm64 difference
would show up as cost rather than correctness.

</details>
