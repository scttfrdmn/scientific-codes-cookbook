---
tool: nilearn
tool_version: "0.14.1"
env: neuroimaging
image: quay.io/aarchsci/neuroimaging@sha256:f57c5cc3637cfcf20dfdf8a1627f3a57b56b397dde9f99805d35fa4b4a7a657c
env_x86_64: neuroimaging-d0a05a385703
last_verified_x86_64: 2026-10-17
spawn_version: 0.127.0
last_verified: 2026-10-17
---
# Nilearn — its own 8,211 tests, plus masking, a GLM, connectivity and cleaning against closed forms

Runs Nilearn's own test suite the way its `tox.ini` does, then holds four main APIs to exact identities on Graviton4: a masker round trip, a GLM against least squares, connectivity against Pearson, detrending against a projection. For anyone doing fMRI analysis on ARM.

## Run it

```bash
make stage RECIPE=nilearn     # once: 7 pinned pytest wheels + the checks; every image is synthesised
spawn task run --spec "$(make -s spec RECIPE=nilearn)" --wait
make ls RECIPE=nilearn

FirstLevelModel(noise_model="ols", signal_scaling=False).fit(img, design_matrices=[dm])  # == lstsq(X, Y)
ConnectivityMeasure(kind="correlation", cov_estimator=EmpiricalCovariance())             # == np.corrcoef
```

## Which box — [measured](../../measurements/nilearn-real/README.md) on all 871 quality-checked ABIDE I subjects (99 GB)

| chip | `8xlarge` | wall | **billed $/result** |
|---|---|---|---|
| **Graviton4** | `r8g` | 459 s | **0.499** |
| Xeon 6975P-C | `r8i` | 527 s | 0.709 |
| EPYC 9R45 | `r8a` | **300 s** | 0.576 |

**Take Graviton4 for a one-off pass, AMD if the data stays on the box:** AMD runs CC200 extraction, the tangent embedding and three CVs 1.5× faster, but staging the cohort is half the bill. Every identity holds at scale and all three chips give identical region signals and predictions (10-fold accuracy 0.705). The release's own `rois_cc200` series put the atlas 3 mm off its header, so extract from `func_preproc` yourself.

## Make it yours

| In the recipe | Swap for | What to know |
|---|---|---|
| `cov_estimator=EmpiricalCovariance()` | the default | **the default `kind="correlation"` is not Pearson.** Its estimator is Ledoit–Wolf, which shrinks toward the identity — here up to **0.085** off, off-diagonals at **0.923** of Pearson on average. Fine for noisy short scans, but say so if you report "correlation". |
| `signal_scaling=False` | the default | the default rescales each voxel to **percent signal change**, so effect sizes are not in your data's units. Leave it on for group analysis; turn it off to compare against anything else. |
| `noise_model="ols"` | `"ar1"` (the default) | AR(1) is generalised least squares — a **different estimator**, here 0.037 away from OLS on the same data. Only OLS equals `lstsq`. |
| one BLAS/OpenMP thread per xdist worker | the default | **parallel processes each bring a pool as wide as the box.** Measured: a test that takes 13 s alone took 3,824 s among 16 workers, while one thread alone cost nothing (199 s vs 206 s). Set `OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1` whenever you fan Nilearn out over processes. |
| synthetic NIfTI images | your scans | nothing is fetched. [neuroimaging](../neuroimaging/README.md) covers the file side (DIPY, AFNI, nibabel); [nipype](../nipype/README.md) the workflows. |

**Leave the fixtures.** Each check is an identity, so the data only has to be well-posed — a masker hole, a full-rank design, a correlated mixture — and synthesising it keeps the run offline. **Scale it** to real data: all four identities hold at any size.

## Shape, size, cost

One task on `c8g.4xlarge` (16 vCPU / 32 GiB), TTL 120m as a **backstop** with `cost_limit` $0.80 as the real guard. The suite's three groups take 40 s, 133 s and 505 s (the serial one) inside a ~13-minute window ([layout](../../patterns/layout-and-effective-cost.md)) — these timings are not compute cost.

<details>
<summary>As shipped: Nilearn's own 8,211 tests with 0 failures and every one accounted for, an exact masker round trip, a GLM equal to least squares to 6e-15, Pearson to 2e-16</summary>

### The checks

| observable | assertion | observed |
|---|---|---|
| staged bytes | match pins | 7 wheels + 5 checks |
| **Nilearn's suite, fast group** (`not slow and not flaky and not single_process`, xdist, `--timeout=60`) | **0 failed, 0 errors** | **5,951 passed**, 69 skipped, 1,021 xfailed of 7,039 |
| **slow group** (`slow and not flaky and not single_process`, xdist) | **0 failed, 0 errors** | **709 passed**, 12 skipped, 225 xfailed of 944 |
| **serial group** (`flaky or single_process`) | **0 failed, 0 errors** | **224 passed**, 4 skipped, 2 xfailed of 228 |
| every collected test reports exactly one outcome | 0 missing / uncollected / multiple, per group | 0 / 0 / 0 |
| the three groups partition the suite | items sum to one `--collect-only` | 7,039 + 944 + 228 = **8,211** |
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

### Nilearn's own suite, the way Nilearn runs it

The package ships its tests (135 modules); the [reference-from-tests](../../practices/reference-from-tests.md)
shape, as [scikit-learn](../scikit-learn/README.md) uses it. The env now carries the suite's
non-pytest dependencies (`polars`, `pyarrow`, `numpydoc`, from aarchsci#43), and pytest plus the
two plugins Nilearn's test group names (`pytest-timeout`, `pytest-rerunfailures`) are staged as
pinned wheels. The three marker groups and their warning-as-error flags come straight from
`tox.ini` (`testenv:latest` and `testenv:flaky`); `pytest.ini` transcribes Nilearn's own
`[tool.pytest.ini_options]`, keeping `xfail_strict` and the registered markers.

Three things differ from running it in a checkout, each because the installed package is not one:

- **A byte-identical copy in `/tmp`.** Some tests write beside themselves
  (`test_as_ndarray_memmap` writes `_utils/tests/data/mmap.dat`), and `site-packages` is read-only
  to the container user. The task proves the copy with `diff -r` before testing it.
- **Items sorted by node id** (`order.py`). `test_img_plotting.py` parametrizes over a *set of
  functions*, whose order follows memory addresses, so xdist workers can disagree and abort the
  run at collection (8 workers agreed; 16 did not). Reported as
  [nilearn/nilearn#6662](https://github.com/nilearn/nilearn/issues/6662).
- **One thread per worker** in the two parallel groups (above, Make it yours).

Every collected item is accounted for (`account.py`, from a separate `--collect-only` process),
and skips are explained in `suite-summary.txt`; the largest causes are `SCIPY_ARRAY_API` unset
(34 + 7 + 1) and plotly or kaleido absent (28 + 2 + 3).

### Pins

| | |
|---|---|
| data | **none staged** — a 4-D image with a non-box mask, a 120-scan two-condition design, a 12-region correlated mixture and trended signals, all from `default_rng(20261013)` |
| test runner | `pytest 9.1.1`, `pluggy 1.6.0`, `iniconfig 2.3.1`, `pytest-xdist 3.8.0`, `execnet 2.1.2`, `pytest-timeout 2.4.0`, `pytest-rerunfailures 16.7` — pure-Python wheels by exact version, sha256-pinned in `stage-inputs.sh` |
| checks | `identities.py`, `account.py`, `order.py`, `suite_check.py`, `pytest.ini`, pinned by sha256 |
| image | `quay.io/aarchsci/neuroimaging@sha256:f57c5cc3637c…` — nilearn 0.14.1, nibabel 5.4.2, scikit-learn 1.9.1, numpy 2.5.4, polars 2.0.0, pyarrow 25.0.0, numpydoc 1.11.0, dipy, afni, nipype, mne |

cosign-verified against `playgroundlogic/aarchsci`; the signature covers the **manifest-list**
digest (`6cd4f404…`), so verify the tag and pin the arm64 digest. The four identities give the same values to every printed digit on this digest as on the previous one (`a1216a86…`). A newer digest than
[neuroimaging](../neuroimaging/README.md) pins — that recipe stays on the image it was verified
against.

### x86_64

The same suite and identities run on `c8i` (Intel) through the env's [locked x86 twin](../../envs-x86_64/neuroimaging-d0a05a385703.linux-64.lock): the same versions, linux-64 builds, installed offline in 4 s (289 of 294 package versions identical; the four that differ, glib, sqlite and charset-normalizer, are listed in the lock). **All 8,211 tests: 0 failures on both chips**, the same counts in every group. Of the identities, the exact ones (masker 0, Pearson 2.220e-16) and every control are identical; only round-off residuals move, e.g. GLM vs `lstsq` 5.995e-15 → 6.328e-15, all far inside their bounds.

One `flaky`-marked check, `check_img_estimator_dtype_bool`, needed Nilearn's own reruns on **both** chips (estimator10 4 retries on Graviton4; CanICA 7 on Intel). That is what its `@pytest.mark.flaky(reruns=10)` is for, and `suite-summary.txt` reports retries per test rather than counting them as outcomes.

### Run + verify

```sh
make stage RECIPE=nilearn
spawn task run --spec "$(make -s spec RECIPE=nilearn)" --wait
aws s3 cp "s3://$(make -s print-bucket)/runs/nilearn/r1/suite-summary.txt" -
aws s3 cp "s3://$(make -s print-bucket)/runs/nilearn/r1/score.tsv" -
```

Fails on a pin mismatch, any failure or error in Nilearn's suite, a collected test without exactly one outcome, groups that do not partition the suite, a masker round trip that is not exact or writes outside the mask, a GLM
that misses planted betas or disagrees with least squares beyond `1e-10`, an AR(1) fit too close to
OLS to show the comparison discriminates, a correlation that is not Pearson with an empirical
estimator, a default estimator that does not shrink, or a detrend that leaves the constant or the
trend — or a NaN anywhere a bound is applied. Check the bucket regardless
([exit 0 isn't proof](../../practices/container-path.md)).

### Not covered

**Plotly and kaleido backends**, which the env does not carry (the matplotlib plotting tests do run). **Real scans** are in
[nilearn-real](../../measurements/nilearn-real/README.md) (ABIDE I, region signals, tangent space, a
classifier), but nothing reads a BIDS dataset, so fetchers and confound loading (`load_confounds`) are
untouched. **Second-level models and searchlight**, which are where many fMRI analyses end up. **Surface data** (`SurfaceImage`, fsaverage): the bundled `fsaverage5` ships in
the package and the suite's surface tests use it, but the identities do not.

</details>
