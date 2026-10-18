# scikit-learn on a real workload — Covertype, Graviton4 vs x86

> **Same box size, same code, same bytes: Graviton4 fits all three model families for 15–34% less per fit,
> and the linear model's optimum agrees across chips to 1.6e-12.** The gradient-boosted
> ensemble is a path — its probabilities differ in the last bits across chips — and its accuracy
> is still identical to six digits.

Workload: **all 581,012 rows** of UCI Covertype (54 features, 7 classes) — the standard
medium-size tabular benchmark, and the size a researcher actually fits, not a slice. An 80/20
stratified split, then the three model families most work starts with: an L2 multinomial
`LogisticRegression`, `HistGradientBoostingClassifier` (300 iterations, 255 leaves) and a
100-tree `RandomForestClassifier`, all on 8 threads with fixed seeds.

## Run it

```sh
export AWS_PROFILE=aws
bash measurements/scikit-learn-real/stage-inputs.sh "$COOKBOOK_BUCKET"
spawn task run --spec "$(python3 scripts/spec_arch.py scikit-learn arm64 "$COOKBOOK_BUCKET" "$(date +%s)" measurements/scikit-learn-real/covtype.task.json)" --wait
spawn task run --spec "$(python3 scripts/spec_arch.py scikit-learn x86_64 "$COOKBOOK_BUCKET" "$(date +%s)" measurements/scikit-learn-real/covtype.task.json)" --wait
```

One task per chip runs `real.py` (pinned by sha256) against the staged data. The x86 leg is the
recipe's `geo-ml` env twin: sklearn 1.9.1, numpy 2.5.4, same versions, linux-64 builds.

## Results

8 threads, one `2xlarge` per chip, same image versions, same input bytes:

| chip | instance | fit wall: logreg / HGB / RF | $/hr | compute $/fit: logreg / HGB / RF | task compute | billed | **billed $/result** | peak RSS |
|---|---|---|---|---|---|---|---|---|
| Graviton4 | `c8g.2xlarge` | 49.0 / 85.6 / 10.6 s | 0.3190 | 0.0043 / 0.0076 / 0.0009 | 657 s | 826 s | **0.0732** | 4.68 GiB |
| Xeon 6975P-C | `c8i.2xlarge` | 63.1 / 88.4 / 10.6 s | 0.3748 | 0.0066 / 0.0092 / 0.0011 | 693 s | 825 s | **0.0859** | 4.71 GiB |

`task compute` is the whole script: the three fits, a repeat of each tree ensemble (determinism),
an affine-invariance refit of each, an lbfgs cross-solver fit (273 / 288 s, the largest
single item) and an unregularised-boosting diagnostic. **Billed $/result** is that whole verified
run, launch to terminate.

- **Graviton4 is cheaper on every fit**: logreg **34%**, HGB **18%**, RF **15%** less per fit. The
  wall times are close on the trees (HGB 85.6 vs 88.4 s, RF a tie) so the saving there is the 15%
  lower hourly price; on the Newton logistic fit (BLAS-bound Hessian assembly) Graviton4 is also
  1.29× faster.
- **Boot and staging are 130–170 s of the bill** against 11–12 min of compute — a compute-dominated
  job, unlike [salmon](../salmon-real/README.md). Fitting several models in one task is the shape
  to use; a task per model would pay the boot each time.
- **Peak RSS 4.7 GiB on both** is the forests: 8.88 M nodes each, two held at once for the
  invariance comparison. A `c8g.2xlarge` has 3× that.
- n = 1 per cell. Every fit is deterministic on its chip (asserted in-run, below), so a repeat
  would only re-measure the timing noise.

## What held at scale, on both chips

| claim | why it must hold | Graviton4 | x86 |
|---|---|---|---|
| sha256 == scikit-learn 1.9.1's own `fetch_covtype` pin; UCI's archive is the same bytes | provenance | ok | ok |
| class counts == UCI `covtype.info` (211840, 283301, 35754, 2747, 9493, 17367, 20510) | published | exact | exact |
| every row has exactly one wilderness area and one soil type | documented encoding | 581,012 of 581,012 | same |
| logistic stationarity `max|X'(P−Y) + W/C| / n` | definition of the optimum | **2.589e-11** | **2.589e-11** |
| `Σ_i p_ik == n_k` per class (unpenalised intercept) | conservation at the optimum | **1.514e-12** · n | **1.514e-12** · n |
| lbfgs reaches newton-cholesky's minimum, rel | unique minimum (strictly convex) | 7.6e-10 | 6.1e-10 |
| HGB fit twice, fixed seed, 8 threads: predict_proba bytes | determinism | identical | identical |
| RF fit twice: node arrays and predict_proba bytes | determinism | identical | identical |
| HGB and RF refit on `4X + 1024`: predictions | trees see order, not scale; exact in float64 | bit-identical | bit-identical |
| RF on `4X + 1024`: same trees, thresholds `4t + 1024` | as above | true | true |
| accuracy: majority 0.4876 < logreg < both ensembles, ensembles ≥ 0.90 | what each model class achieves | ok | ok |

The bounds are `1e-6` on stationarity (Newton at `tol=1e-10` converges quadratically, so a
correct fit sits orders inside), `1e-6` relative on the lbfgs objective (a first-order method at
`tol=1e-7`; the bound is on the objective because the collinear one-hot columns leave the
coefficients ill-conditioned), and exact equality everywhere else.

## Across chips: what is a fixed point and what is a path

- **The logistic optimum is a fixed point**, and it agrees: coefficients to **1.6e-12** absolute
  (max |W| 12.2), the objective `0.629010187599845` to all 15 printed digits, the stationarity
  residuals digit for digit — with OpenBLAS on `neoversev2` here and `Cooperlake` there.
  Accuracy 0.725420 on both.
- **The random forest is identical across chips**: same node arrays hash, same probabilities hash,
  accuracy 0.954468. On integer features with Gini counts there is no rounding for a chip to change.
- **HistGradientBoosting is a path.** Its probability bytes differ across chips (it sums float
  gradients and hessians into histograms); its test accuracy is still **0.969760 on both**. Reported,
  not asserted — on a different dataset the last bits could flip a prediction.
- **lbfgs is a path too**: 3,963 vs 3,893 iterations to the same minimum.

## The default `l2_regularization=0` diverges on this data

The diagnostic fits the same HGB with sklearn's default `l2_regularization=0` for 100 iterations.
Its **training** log-loss falls to 0.200 by iteration 40, then rises — 0.276, 0.358, **0.817** at 100
— while the largest leaf value reaches **5.7e4**. It is
not a chip effect — the trace, the leaf maximum and the accuracy (0.910088) are identical on both.
So **set `l2_regularization`** for multiclass boosting; test accuracy alone at a
fixed `max_iter` would read it as overfitting.

**It needs neither large trees nor much data, and "unregularised Newton" does not explain it on
its own.** [`hgb-repro/`](hgb-repro/) is a minimal reproduction on Graviton4: 20,000 rows, the 10
numeric features, and every hyperparameter at sklearn's default except `max_iter=200, early_stopping=False`.
Training log-loss bottoms at 0.418 (iteration 55) and ends at 1.934, with leaves reaching 2.2e4.
It also re-runs the full-data diagnostic above to the digit (0.1998 / 0.8166 / 5.73e4).

- **Fixes:** `l2_regularization` ≥ 0.01, `min_hessian_to_split` ≥ 0.01 (patched, since it is not
  an estimator parameter), `learning_rate=0.05`, starting from raw score 0 instead of the log class
  priors, or **`min_samples_leaf=1`**.
- **Not fixes:** 7–255 leaves, `min_samples_leaf` 5 or 100, `min_hessian_to_split=0.002`.
- **LightGBM and XGBoost do not diverge on the same rows**, using sklearn's own
  `get_equivalent_estimator` mapping (l2 = 0). Both decrease monotonically to 0.16 and 0.15.
- **The discriminator is `min_samples_leaf`.** At 1, sklearn and LightGBM agree (0.155 vs 0.152 at
  200). At 5 and 20, sklearn diverges and LightGBM does not. LightGBM applies `min_data_in_leaf`
  to bin counts *estimated from hessians* (`feature_histogram.hpp`, `cnt_factor = num_data / sum_hessian`),
  while sklearn uses true counts. The diverging leaves are ~20–40 rows whose summed hessian sits
  just above the 1e-3 floor (the first rise: 34 rows, G = 2.0, H = 1.05e-3, step −191).
- With the default `early_stopping='auto'` the fit stops at `n_iter_=11` (train loss 0.64), and
  max |leaf| has already reached 1.9e3.

No scikit-learn issue or doc note covers this. The nearest is
[#17763](https://github.com/scikit-learn/scikit-learn/issues/17763) (2020), about learning rates
above 0.1, which was closed because LightGBM degraded there too.

## Pins

| | |
|---|---|
| data | `covtype.data.gz` sha256 `614360d0…5b15771` — figshare file 5976039, the URL and checksum in sklearn 1.9.1's `datasets/_covtype.py`; identical to the copy in UCI's archive, doi:10.24432/C50K5N (durable-id tier) |
| script | `real.py`, sha256 `48b296bd…ebaf0343` (checked in-task against the staged `pins.sha256`) |
| image | the recipe's `geo-ml@sha256:83ca6331…` (arm64) and its `geo-ml-ffc4dc5282aa` x86 twin |
