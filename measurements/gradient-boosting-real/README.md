# xgboost + LightGBM on a real workload: all of HIGGS on Graviton4, Intel and AMD

> **Graviton4 has the cheapest fit for both libraries: 48% below Intel and 23% below AMD for xgboost,
> and 31–34% below both for LightGBM.** And all 500,000 test predictions are byte-identical on all
> three chips, for both libraries, at 8 threads.

Workload: **all 11,000,000 rows** of UCI HIGGS (28 features, binary), the dataset LightGBM's own
[`docs/Experiments.rst`](https://github.com/microsoft/LightGBM/blob/v4.6.0/docs/Experiments.rst)
benchmarks on, with its published split (last 500,000 rows are the test set) and its published
settings: 500 rounds, learning rate 0.1, 255 leaves, `min_sum_hessian`/`min_child_weight` 100,
xgboost on `hist` + `lossguide`. That is the size people actually train a boosted model at: a
10M-row table fits in memory, but it is not a toy. Both libraries run on 8 threads with fixed seeds.

## Run it

```sh
export AWS_PROFILE=aws
bash measurements/gradient-boosting-real/stage-inputs.sh "$COOKBOOK_BUCKET"   # fetches HIGGS on AWS, once
for a in arm64 x86_64 amd; do
  spawn task run --wait --spec "$(python3 scripts/spec_arch.py gradient-boosting $a "$COOKBOOK_BUCKET" "$(date +%s)" \
    measurements/gradient-boosting-real/higgs.task.json)"
done
```

One task per chip runs `real.py` (pinned by sha256). The x86 legs use the recipe's `geo-ml` env
twin: xgboost 3.4.2, LightGBM 4.7.0, numpy 2.5.3, the same versions as linux-64 builds.

## Results

8 vCPU, one `m8*.2xlarge` per chip, same versions, same input bytes:

| chip | instance | fit wall: xgboost / LightGBM | $/hr | compute $/fit: xgboost / LightGBM | task compute | billed | **billed $/result** | peak RSS |
|---|---|---|---|---|---|---|---|---|
| **Graviton4** | `m8g.2xlarge` | 92.6 / **160.0** s | 0.3590 | **0.0092** / **0.0160** | 552 s | 707 s | **0.0705** | 17.4 GiB |
| Xeon 6975P-C | `m8i.2xlarge` | 149.7 / 205.9 s | 0.4234 | 0.0176 / 0.0242 | 771 s | 888 s | 0.1044 | 17.4 GiB |
| EPYC 9R45 | `m8a.2xlarge` | **88.0** / 170.0 s | 0.4869 | 0.0119 / 0.0230 | 575 s | 701 s | 0.0948 | 17.4 GiB |

*Fit wall* is the 500-round training call only: what a user pays for. *Task compute* is the whole
script: loading the CSV (18–27 s), both main fits, six 100-round verification fits, and SHAP.
*Billed $/result* is that whole verified run, launch to terminate, at spawn's printed price.

- **Graviton4 is the cheapest place to fit either model.** xgboost costs 48% less than on Intel and
  23% less than on AMD. LightGBM costs 34% and 31% less. AMD is the fastest xgboost box
  (88.0 vs 92.6 s), but it costs 36% more per hour, which more than eats that 5%.
- **Intel's 8 vCPU are 4 cores with SMT** (`Thread(s) per core: 2`). Graviton4 and AMD give 8 real
  cores. That is most of why Intel is 1.62× slower on xgboost and 1.29× slower on LightGBM at the
  same vCPU count.
- **Peak RSS is 17.4 GiB on every chip, and it happens while the CSV loads** (pyarrow parse plus
  the float32 copy), not during training. cgroup `memory.peak` reads 17.3 GiB on Graviton4 and
  20.1 GiB on x86. The difference is the x86 twin's env, which installs into the container at run
  time. 32 GiB is the smallest size that holds this; `c8g.2xlarge` (16 GiB) would OOM at load.
- **Boot and staging are 117–155 s of the bill**, against 9–13 min of compute. The 2.8 GB input
  stages in 9–15 s. These timings are not compute cost: two 500-round fits in one task is the
  shape to copy, and a task per model would pay the boot twice.
- n = 1 per cell. Both libraries are deterministic at a pinned thread count (asserted in-run), so
  a repeat would only re-measure timing noise.

## What held at scale, on all three chips

| claim | why it must hold | Graviton4 | Intel | AMD |
|---|---|---|---|---|
| sha256 of the staged file == the pin; 11,000,000 × 29, labels {0,1}, all finite | provenance | ok | ok | ok |
| tree 0: sum of leaf hessians == n·p0·(1−p0) | conservation. At iteration 0 every row has the same hessian | xgb 1.20e-08, lgb 1.13e-08 rel | same | same |
| LightGBM: every tree's leaf counts sum to n_train | conservation (no bagging) | 500 of 500 | 500 of 500 | 500 of 500 |
| TreeSHAP contributions + bias == raw margin, 1,000 rows | local accuracy, a SHAP axiom | xgb 4.586e-06 (float32), lgb 1.785e-13 | 4.584e-06, 1.785e-13 | 4.584e-06, 1.785e-13 |
| a fresh 100-round fit == the main model's first 100 trees | determinism at 8 threads | margins and xgb dump identical | same | same |
| fit on `4X`, predict `4X_test` == fit on X | trees see order, not scale. ×4 is exact in float32 | bit-identical, both | same | same |
| `m_bb` constrained non-decreasing: steps on a 500 × 200 grid | documented guarantee | min step 0.000e+00, both | same | same |
| the unconstrained model on the same grid must step down | control: shows the constraint does work | −0.880 / −0.895 | same | same |
| test AUC > 0.80 | sanity floor. The real check is the published value, below | 0.845918 / 0.845705 | same | same |

The hessian bound is 1e-5 relative. It is a float sum of 10.5M terms, and the observed error
is 1e-8. SHAP is 1e-3 for xgboost, whose contributions are float32 (4.6e-6 observed), and 1e-9 for
LightGBM, which works in float64. Everything else is exact equality.

**Against the published benchmark:** LightGBM's AUC is **0.845705 against Experiments.rst's
0.845724**. xgboost's `hist` is **0.845918 against 0.845314**. Both are within 6e-4. That is
reported, not asserted: the published numbers come from much older versions of both libraries, so they
are a check that the setup matches, not a reference value. The top three features by gain are `m_bb`, `m_wwbb` and `m_wbb`
in both libraries, the same high-level invariant masses Baldi et al. built HIGGS around.

## Across chips: a path that came out identical

Both models are paths, not fixed points: a float histogram sum per split, compounded over 500
trees. They are still **byte-identical across all three chips**. `xgb_margin.npy` sha256 is
`1d538b38…` and `lgb_margin.npy` is `b54781c7…` on Graviton4, Intel and AMD, and so are the
feature-gain rankings. Neither library links a BLAS for training, so no OpenBLAS kernel dispatch
is involved. The thread-pinned histogram reductions are evidently ordered the same way on every
chip. This is an observation about this build and this data, not a guarantee to assert elsewhere:
a different thread count changes the reduction order.

The one difference is xgboost's SHAP local-accuracy residual: 4.586e-06 on Graviton4 against
4.584e-06 on both x86 chips. That is a float32 path in the TreeSHAP kernel, and it is well inside
its bound. The margins it is compared against are identical.

## Pins

| | |
|---|---|
| data | `HIGGS.csv.gz` sha256 `ea302c18164d4e3d916a1e2e83a9a8d07069fa6ebc7771e4c0540d54e593b698`, 2,816,407,858 bytes. UCI ML Repository, doi:10.24432/C5V312 (durable-id tier), `ml/machine-learning-databases/00280/HIGGS.csv.gz` (gzip mtime 2014-02-12). Fetched on AWS by `00-fetch.task.json` and read end to end there (`fetch.tsv`: 11,000,000 rows, 5,829,123 signal) |
| script | `real.py`, sha256 checked in-task against the staged `pins.sha256` |
| image | the recipe's `geo-ml@sha256:14aeb6be…` (arm64) and its `geo-ml-1876b553ab9b` x86 twin |

UCI serves the file chunked with no `Content-Length` and dropped the first fetch at 1.25 GB, so
`fetch.py` resumes with `Range`. Completeness is proved by the end-to-end gzip read, which checks
the CRC-32 and length trailer, and by the row count.
