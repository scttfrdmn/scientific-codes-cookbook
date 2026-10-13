---
tool: nilearn
tool_version: "0.14.1"
env: neuroimaging
image: quay.io/aarchsci/neuroimaging@sha256:a1216a8657c15061e7d3b7aaf93909194bee8233f60e0f065afd37a39b3a8ba5
gap_x86_64: the aarch.science `neuroimaging` env is arm64-only (manifest list has a linux/arm64 entry and no amd64, checked 2026-10-13) and aarch.* builds ARM only; no x86 build of the same env exists
spawn_version: 0.127.0
last_verified: 2026-10-13
---
# Nilearn — masking, a GLM, connectivity and cleaning, each against a closed form

Holds four of Nilearn's main APIs to exact identities on Graviton4: a masker round trip, a first-level GLM against least squares, connectivity against Pearson, and detrending against a projection. For anyone doing fMRI analysis on ARM.

## Run it

```bash
make stage RECIPE=nilearn     # once: the checks only — every image and time series is synthesised
spawn task run --spec "$(make -s spec RECIPE=nilearn)" --wait
make ls RECIPE=nilearn

FirstLevelModel(noise_model="ols", signal_scaling=False).fit(img, design_matrices=[dm])  # == lstsq(X, Y)
ConnectivityMeasure(kind="correlation", cov_estimator=EmpiricalCovariance())             # == np.corrcoef
```

## Make it yours

| In the recipe | Swap for | What to know |
|---|---|---|
| `cov_estimator=EmpiricalCovariance()` | the default | **the default `kind="correlation"` is not Pearson.** Its estimator is Ledoit–Wolf, which shrinks toward the identity — here up to **0.085** off, off-diagonals at **0.923** of Pearson on average. Fine for noisy short scans, but say so if you report "correlation". |
| `signal_scaling=False` | the default | the default rescales each voxel to **percent signal change**, so effect sizes are not in your data's units. Leave it on for group analysis; turn it off to compare against anything else. |
| `noise_model="ols"` | `"ar1"` (the default) | AR(1) is generalised least squares — a **different estimator**, here 0.037 away from OLS on the same data. Only OLS equals `lstsq`. |
| synthetic NIfTI images | your scans | nothing is fetched. [neuroimaging](../neuroimaging/README.md) covers the file side (DIPY, AFNI, nibabel); [nipype](../nipype/README.md) the workflows. |

**Leave the fixtures.** Each check is an identity, so the data only has to be well-posed — a masker hole, a full-rank design, a correlated mixture — and synthesising it keeps the run offline. **Scale it** to real data: all four identities hold at any size.

## Shape, size, cost

One task on `m8g.large` (2 vCPU / 8 GiB), TTL 30m as a **backstop** with `cost_limit` $0.12 as the real guard. The checks take seconds of a ~1-minute window ([layout](../../patterns/layout-and-effective-cost.md)).

<details>
<summary>As shipped: an exact masker round trip, a GLM equal to least squares to 6e-15, Pearson to 2e-16 — and why Nilearn's own suite is not here yet</summary>

### The checks

| observable | assertion | observed |
|---|---|---|
| staged bytes | match pin | 1 of 1 |
| nilearn / nibabel / numpy / sklearn | read from the running install | **0.14.1** / 5.4.2 / 2.5.4 / 1.9.1 |
| **NiftiMasker round trip, in-mask** | **`== 0` exactly** | **0.000e+00** over 215 voxels × 50 volumes |
| **inverse_transform, out-of-mask** | **`== 0` exactly** | **0.000e+00** |
| **GLM, noise-free: effects vs planted β** | **`< 1e-10`** | **7.272e-15** (64 voxels × 7 regressors) |
| **GLM (OLS) vs `numpy.linalg.lstsq`, noisy** | **`< 1e-10`** | **5.995e-15** |
| control: AR(1) noise model | must differ by > 1e-3 | 3.681e-02 |
| **correlation + `EmpiricalCovariance` vs `np.corrcoef`** | **`< 1e-12`** | **2.220e-16** |
| control: the default (Ledoit–Wolf) | must differ by > 1e-4, and shrink | 8.528e-02, ratio 0.923 |
| **`signal.clean(detrend=True)` ⟂ [1, t]** | **rel `< 1e-10`** | **2.050e-16** |
| **a planted pure trend, cleaned** | **`< 1e-9`** | **3.553e-14** |

### Why these bounds

Each bound is what the mechanism supports, not a margin over the observed value. A masker is a
gather and a scatter with float64 kept end to end, so the claim is **equality**. Two codes solving
one least-squares problem can differ only by round-off, so that bound is conditioning × machine
epsilon, set at `1e-10` — orders above the measured `6e-15`, orders below any real miss.

The GLM comparison is a genuine two-route check. Nilearn's OLS forms the pseudo-inverse of the
whitened design with `scipy.linalg.pinv` (`nilearn/glm/regression.py:78`); `numpy.linalg.lstsq`
goes through LAPACK's SVD solver. Same design matrix — Nilearn's own, from
`make_first_level_design_matrix` (Glover HRF, cosine drift, 7 regressors) — so the two can only
disagree if one is wrong.

### The default that is not what its name says

`ConnectivityMeasure(kind="correlation")` reads like Pearson correlation and is not, by default:

```text
with EmpiricalCovariance()   vs np.corrcoef   2.220e-16     <- Pearson
default (Ledoit-Wolf)        vs np.corrcoef   up to 8.528e-02, off-diagonals 0.923 of Pearson
```

Shrinkage is a sound default for short, noisy time series, which is why Nilearn uses it. But a
connectivity matrix reported as "correlation" from the default is a regularised estimate, and
comparing it to anything computed with `np.corrcoef` compares two different quantities. The recipe
uses the default as its control, so the Pearson identity is shown to discriminate.

### Nilearn's own test suite: not runnable in this env yet

Nilearn ships 135 test files in the package, and its own suite would be the strongest check — the
[reference-from-tests](../../practices/reference-from-tests.md) shape, as
[scikit-learn](../scikit-learn/README.md) uses. Here **26 of those modules fail to import**,
including every masker, GLM, decoding and connectome module, because a shared helper imports two
test-only dependencies unconditionally:

```text
nilearn/_utils/estimator_checks.py:34   import polars as pl
nilearn/_utils/estimator_checks.py:44   from numpydoc.docscrape import NumpyDocString
E   ModuleNotFoundError: No module named 'polars'
```

Running only the 109 modules that do import would be a green check that skips the core of the
library, so it is not done. `polars`, `pyarrow` and `numpydoc` — nilearn's declared `test_common`
dependencies — are requested for this env as
[playgroundlogic/aarchsci#43](https://github.com/playgroundlogic/aarchsci/issues/43). **Un-defers
when** they ship: then the suite runs with nilearn's own execution model from its `tox.ini` — the
fast and slow marker groups in parallel, `flaky`/`single_process` serially.

### Pins

| | |
|---|---|
| data | **none staged** — a 4-D image with a non-box mask, a 120-scan two-condition design, a 12-region correlated mixture and trended signals, all from `default_rng(20261013)` |
| checks | `identities.py`, pinned by sha256 |
| image | `quay.io/aarchsci/neuroimaging@sha256:a1216a8657c1…` — nilearn 0.14.1, nibabel 5.4.2, dipy, afni, nipype, scikit-learn 1.9.1, numpy 2.5.4 |

cosign-verified against `playgroundlogic/aarchsci`; the signature covers the **manifest-list**
digest, so verify the tag and pin the arm64 digest. A newer digest than
[neuroimaging](../neuroimaging/README.md) pins — that recipe stays on the image it was verified
against. No x86 run: `neuroimaging` has no x86 build.

### Run + verify

```sh
make stage RECIPE=nilearn
spawn task run --spec "$(make -s spec RECIPE=nilearn)" --wait
aws s3 cp "s3://$(make -s print-bucket)/runs/nilearn/r1/score.tsv" -
```

Fails on a pin mismatch, a masker round trip that is not exact or writes outside the mask, a GLM
that misses planted betas or disagrees with least squares beyond `1e-10`, an AR(1) fit too close to
OLS to show the comparison discriminates, a correlation that is not Pearson with an empirical
estimator, a default estimator that does not shrink, or a detrend that leaves the constant or the
trend — or a NaN anywhere a bound is applied. Check the bucket regardless
([exit 0 isn't proof](../../practices/container-path.md)).

### Not covered

**Nilearn's own suite**, until aarchsci#43 lands (above). **Real scans**: nothing here reads a
BIDS dataset, so fetchers, confound loading (`load_confounds`) and resampling between real spaces
are untouched. **Second-level models, decoding and searchlight**, which are where most fMRI
analyses end up. **Surface data** (`SurfaceImage`, fsaverage): the bundled `fsaverage5` ships in
the package but is not exercised. **Plotting**, the part of Nilearn most users see first.

</details>
