---
tool: xgboost-lightgbm
tool_version: "xgboost 3.4.2 / lightgbm 4.7.0"
env: geo-ml
image: quay.io/aarchsci/geo-ml@sha256:14aeb6be84be41b991aab8bd262197044b7c351b067ef9199ae22c4fc6ccde20
env_x86_64: geo-ml-1876b553ab9b
spawn_version: 0.126.1
last_verified: 2026-10-11
last_verified_x86_64: 2026-10-17
---
# xgboost + lightgbm — five exact checks on Graviton, and no comparison between them

Checks both boosters against guarantees they document rather than against each other, because their predictions are not supposed to match. For anyone training gradient-boosted trees on ARM.

## Run it

```bash
make stage RECIPE=gradient-boosting   # once: the checks only — every fixture is seeded
spawn task run --spec "$(make -s spec RECIPE=gradient-boosting)" --wait
make ls RECIPE=gradient-boosting
XGBRegressor(n_jobs=1, tree_method="exact", random_state=20261011,
             monotone_constraints=(1, 0)).fit(X, y)     # no negative step along x0, ever
LGBMRegressor(n_jobs=1, verbose=-1, random_state=20261011,
              monotone_constraints=[1, 0]).fit(X, y)    # same guarantee, different library
```

## Make it yours

| In the recipe | Swap for | What to know |
|---|---|---|
| comparing each library to **itself** | comparing them to each other | **don't.** xgboost grows depth-wise, LightGBM leaf-wise, with different binning — their predictions differ *correctly*. Asserting agreement would assert away a method difference. |
| `monotone_constraints` | unconstrained | the single most checkable thing here: a documented structural guarantee you can verify over a grid, with no tolerance. The recipe also fits **unconstrained** to prove the constraint is doing work. |
| importance **rank** | importance values | xgboost reports normalised gain (0.979), LightGBM raw split counts (309). Only the order is shared ground ([same rule as the aligners](../../practices/cross-checks.md)). |
| `n_jobs=1` | your core count | both are only **bit-reproducible** with the thread count pinned, and every exact claim here rests on that. |
| the seeded fixtures | your data | you lose the known answer. The point of these five is that the right result is derivable, not measured. |

**Leave the fixtures.** Nothing is fetched and nothing is a band: a constant target *is* predicted exactly, separable data *is* fit with zero errors, a constrained model *cannot* step downward. **Scale it** to real data for performance work — these checks travel, the exactness doesn't.

## Which box — [measured](../../measurements/gradient-boosting-real/README.md) (all 11M rows of HIGGS, 500 rounds, 8 threads)
| chip | instance | fit: xgboost / LightGBM | **compute $/fit** | billed $/result | peak RSS |
|---|---|---|---|---|---|
| **Graviton4** | `m8g.2xlarge` | 92.6 / 160.0 s | **0.0092 / 0.0160** | **0.0705** | 17.4 GiB |
| Xeon 6975P-C | `m8i.2xlarge` | 149.7 / 205.9 s | 0.0176 / 0.0242 | 0.1044 | 17.4 GiB |
| EPYC 9R45 | `m8a.2xlarge` | 88.0 / 170.0 s | 0.0119 / 0.0230 | 0.0948 | 17.4 GiB |

**Take Graviton4:** cheapest per fit for both libraries, 23–48% below the x86 chips. Size by RAM, not cores: the peak is the CSV load, so 32 GiB, not a 16 GiB `c8g`. At 8 pinned threads, both libraries' test predictions came out byte-identical on all three chips.

## Shape, size, cost

One task on `m8g.large` (2 vCPU / 8 GiB), TTL 30m as a **backstop** with `cost_limit` $0.12 as the real guard. All five checks and ten model fits take **7 s** of a 1m58s window; 71 s is pulling the image and 39 s installing Docker ([layout](../../patterns/layout-and-effective-cost.md)). `OMP_NUM_THREADS=1` is load-bearing, not tidiness.

<details>
<summary>As shipped: five exact identities, a constraint control, and the two things deliberately not compared</summary>

### The checks

| observable | assertion | observed | x86_64 (m8i.large) |
|---|---|---|---|
| staged bytes | match pin | 1 of 1 | identical |
| xgboost / lightgbm / numpy | read from the running install | **3.4.2** / 4.7.0 / 2.5.3 | identical |
| **constant target, both** | **predictions `==` 7.25 exactly** | **0.000e+00**, 1 distinct value each | identical |
| **separable data, both** | **misclassified `== 0`** | **0 of 500**, both | identical |
| **same seed, one thread** | **two runs bit-identical** | **0.000e+00**, both | identical |
| **monotone constraint, both** | **no negative step** | **0.000e+00**, both | identical |
| unconstrained control, both | must be clearly **< −1e-3** | **−1.514e-01** / −1.056e-01 | identical |
| **importance rank, both** | **x0 first, x1 second** | `[0,1,…]` both | identical |
| noise feature order | *reported* | `[2,3,4,5]` vs `[2,3,5,4]` | identical |
| importance scales | *reported, incomparable* | gain 0.979 vs 309 splits | identical |

### The obvious check is the wrong one

Two gradient-boosting libraries on the same data look like a cross-validation opportunity. They are
not. **xgboost grows trees depth-wise and LightGBM leaf-wise, with different binning and different
regularisation defaults** — so their predictions diverge on anything non-trivial, and that
divergence is correct behaviour rather than a bug in either.

So this recipe makes **no numerical comparison between them.** Each is held to guarantees it
documents, and the only cross-library claim is on *rank*, where the answer is fixed by how the data
was built. That is the same discipline the aligners earned here: use rank when raw values are
incomparable, and say which property you are actually asserting.

Two things are reported rather than asserted, because they genuinely cannot be compared:

```text
importance values   xgboost [0.9792  0.0192  0.0005  0.0004  0.0004  0.0003]   normalised gain
                    lightgbm [309.   182.    19.     16.     5.      9.    ]   split counts
noise rank order    xgboost [2,3,4,5]        lightgbm [2,3,5,4]
```

The scales are different quantities. And the four irrelevant features carry no signal, so their
ordering is arbitrary in both libraries — asserting it would be asserting noise, the subtlest way
a check goes bad.

### Four identities with nothing to argue about

**A constant target is predicted exactly.** Every tree predicts the base value, so the output is
`7.25` with a single distinct value across 400 rows — `0.000e+00` error in both libraries. Not
"close": equal.

**Separable data is fit with exactly zero errors.** The labels are a pure threshold on one feature,
so the misclassification count is an integer and the assertion is `== 0`. Both reach `0 of 500`.

**A fixed seed and one thread give bit-identical runs.** `0.000e+00` between two independent fits
in both libraries. This one comes first in importance even though it looks like bookkeeping: every
exact claim above would be unfalsifiable if the models were not reproducible.

**A monotone constraint cannot be violated.** Declaring a feature non-decreasing is a structural
guarantee, checkable by walking a grid along that feature. Neither library takes a single negative
step — `0.000e+00`.

### The constraint control, which is the part worth copying

"No negative step" is satisfied trivially by a flat model, so on its own it proves nothing. The
fixture is therefore **adversarial**: `y` *decreases* in x0 (slope −4.006) while the model is told
x0 must be non-decreasing. A correct implementation honours the constraint and consequently fits
the data badly — that is the guarantee working, not failing.

And the same data is fit **unconstrained** alongside, which must come out clearly decreasing:

```text
                      constrained      unconstrained
xgboost                0.000e+00         -1.514e-01
lightgbm               0.000e+00         -1.056e-01
```

The recipe asserts both halves. Without the second, a library that silently ignored
`monotone_constraints` would pass.

### The fixtures are checked for vacuity before a box is paid for

Each of the three datasets has a way it could make its assertion pass for the wrong reason, so
`stage-inputs.sh` rules each one out locally:

```text
separable set      144 positives of 500 (28.8%)  -- not degenerate; 0 errors would be trivial
                   labels are a pure threshold on x0
monotone fixture   slope in x0 = -4.006          -- decreasing, so the constraint costs something
importance truth   sd contributions  x0=5.096  x1=0.511  noise=0.102
```

An empty class, a flat fixture, or x0/x1/noise too close together would each leave a green check
over nothing. Confirming a test *can* fail is cheaper than discovering later that it couldn't.

### Pins

| | |
|---|---|
| data | **none.** Five fixtures from `default_rng(20261011)`; every reference is a structural guarantee |
| checks | `identities.py`, pinned by sha256 |
| image | `quay.io/aarchsci/geo-ml@sha256:14aeb6be84be…` — xgboost 3.4.2, lightgbm 4.7.0, scikit-learn 1.9.1, libpysal 4.15.0, python 3.14.8 |

This pins a **newer** `geo-ml` digest than [geo-ml](../geo-ml/README.md), which is still on
`ed8c59de…`. That is deliberate: a pin change is a change to a recipe's input, so moving that one
would require re-running its Moran's I assertions rather than editing a hash
([why](../../practices/reference-from-tests.md)).

cosign-verified against `playgroundlogic/aarchsci`; the signature covers the **manifest-list**
digest, so verify the tag and pin the arm64 digest.

**x86_64.** The same spec with `ARCH=x86_64` runs on `m8i.large` through the `geo-ml-1876b553ab9b`
twin (`envs-x86_64/`: the pinned image's versions as linux-64 builds, installed offline in 15 s).
xgboost, lightgbm and numpy are the same versions; 16 of 367 packages differ, none in the
computation (aiohttp, backports.zstd, charset-normalizer, executing, fonttools, jedi, libcurl,
libglib, libsqlite/sqlite, libzip, open-radar-data, platformdirs, soupsieve, tifffile, wrapt).
`score.tsv` is **byte-identical** across chips — not only the exact checks but the reported gain
importances to five figures and both noise-feature orders, which is what one thread and a fixed
seed buy.

### Run + verify

```sh
make stage RECIPE=gradient-boosting
spawn task run --spec "$(make -s spec RECIPE=gradient-boosting)" --wait
aws s3 cp "s3://$(make -s print-bucket)/runs/gradient-boosting/r1/score.tsv" -
```

Fails on a pin mismatch, a constant target predicted inexactly, a single misclassification on
separable data, two same-seed runs that differ, any negative step under a monotone constraint, an
unconstrained fit that is *not* clearly decreasing, or either library ranking the constructed
features wrongly — but check the bucket regardless
([exit 0 isn't proof](../../practices/container-path.md)).

### Not covered

**Performance, accuracy, and multi-threaded determinism are in
[gradient-boosting-real](../../measurements/gradient-boosting-real/README.md)** (all of HIGGS,
8 threads), not here: 7 s on seeded toys is not a benchmark, and this recipe pins `n_jobs=1`. **GPU training**, the main reason people reach for
these two, and Round Two here regardless. **Categorical features**, LightGBM's native handling and
xgboost's `enable_categorical`, both untouched. **Early stopping, cross-validation and
hyperparameter search** — the workflow most users actually run. **`datashader`, `statsmodels` and
`fiona`**, also in this env and unexercised; `scikit-learn` only via these two estimators'
interfaces here — its own suite and closed forms are [scikit-learn](../scikit-learn/README.md).

</details>
