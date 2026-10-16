---
tool: glmnet
tool_version: "5.0"
env: r
image: quay.io/aarchsci/r@sha256:f224ffc9bff58dd405c5bc9a201e41137cea3c5670ac5302768191128c6ced38
gap_x86_64: the aarch.science `r` env is arm64-only (manifest list has a linux/arm64 entry and no amd64, checked 2026-10-16) and aarch.* builds ARM only; no x86 build of the same env exists
spawn_version: 0.127.0
last_verified: 2026-10-16
---
# glmnet — lasso, ridge and logistic paths held to their own optimality conditions

Fits glmnet's gaussian and binomial elastic-net paths on Graviton4 and checks every solution against the KKT conditions, the ridge closed form, `lm()` and `glm()`. For anyone fitting penalised regression in R on ARM.

## Run it

```bash
make stage RECIPE=glmnet     # once: the checks only — the design and responses are synthesised
spawn task run --spec "$(make -s spec RECIPE=glmnet)" --wait
make ls RECIPE=glmnet

fit <- glmnet(X, y, alpha = 1, control = list(thresh = 1e-14))     # the whole lasso path
cv  <- cv.glmnet(X, y, foldid = foldid, relax = TRUE)              # fixed folds -> reproducible
coef(cv, s = "lambda.1se", gamma = "gamma.1se")                    # exactly the planted support
```

## Make it yours

| In the recipe | Swap for | What to know |
|---|---|---|
| `control = list(thresh = ...)` | `thresh = ...` | **glmnet 5.0 deprecates the `thresh=` argument.** The default `1e-7` leaves KKT residuals of **1.3e-4** (in units of sd(y)), about 3,000× the residual at `1e-14`, because the residual scales as √thresh. Tighten it before you compare against anything else. |
| `lambda` on raw `y` | — | **glmnet's λ is in y units.** On an unscaled response the ridge it solves is `(XᵀX/n + (λ/sd_y)I)b = Xᵀy/n`. Reading it as `+ λI` puts you **0.16** off. Standardise y first, or divide. |
| `relax = TRUE`, `lambda.1se` | plain lasso | plain lasso at `lambda.1se` kept **2 extra neighbours** (x04, x19) here, and got the support exactly in only 13 of 40 seeds of this design. The relaxed fit got it in 40 of 40. Shrinkage bias is the reason, not a bug. |
| `foldid = foldid` | `nfolds = 10` | random folds make `lambda.1se`, and so the selected model, change from run to run. Fix the folds and two runs give identical CV curves. |
| synthetic `X`, `y` | your data | the checks are identities that hold for any well-posed design. Keep `p < n` for the `lm()`/`glm()` legs. |

**Leave the fixture.** Every check is an optimality condition or a closed form. The data only has to be well-posed: unequal column scales, offsets, neighbour correlation and a known support. **Scale it** to your own design: the KKT check holds at any size and needs no reference.

## Shape, size, cost

One task on `c8g.large` (2 vCPU / 4 GiB), TTL 30m as a **backstop** with `cost_limit` $0.08 as the real guard. The checks take **5 s** of a 1m59s window. 45 s is installing Docker and 67 s pulling R, the catalog's largest image ([layout](../../patterns/layout-and-effective-cost.md)). **These timings are not compute cost.**

<details>
<summary>As shipped: KKT at every λ on two paths, ridge and λ=0 equal their closed forms to 5e-15, the planted support recovered exactly, a permuted null selects nothing</summary>

### The checks

| observable | assertion | observed |
|---|---|---|
| staged bytes | match pin | 1 of 1 |
| R / glmnet | read from the running install | **4.5.3** / 5.0, `aarch64` |
| **gaussian λ_max** | **`== max_j \|x̃_jᵀ(y−ȳ)\|/n`, rel < 1e-12** | **6.373e-16** |
| coefficients at λ_max | exactly 0, intercept = ȳ | 0 |
| λ_max at α = 0.5 | `== λ_max(α=1)/0.5`, rel < 1e-12 | 8.497e-16 |
| **gaussian lasso KKT, all 70 λ** | **≤ p·√thresh = 2.0e-6** | **4.160e-08** (inactive 3.5e-16) |
| control: coefficients one λ step off | must violate by ≥ 1000× the bound | 5.029e-02 (25,147×) |
| same path at default thresh 1e-7 | *reported* | 1.277e-04 (3,070×; √1e7 = 3,162) |
| **ridge vs `(XᵀX/n + λI)⁻¹Xᵀy/n`, 25 λ** | **≤ 1e3·κ·ε = 2.8e-12** | **4.663e-15** |
| ridge on raw y vs `λ/sd_y` | same bound | 1.864e-15 (naive `λ`: 1.613e-01, reported) |
| **λ = 0 vs `lm()`** | **same bound** | **1.544e-15** |
| `cv.glmnet`, fixed `foldid` | two runs give identical `cvm` | identical |
| first five variables to enter the path | exactly the planted support | 2,5,9,14,18 |
| **relaxed lasso at `lambda.1se`** | **exactly the planted support** | **2,5,9,14,18** |
| plain lasso at `lambda.1se` | ⊇ support; *reported* | 2,4,5,9,14,18,19 |
| **permuted-y null at `lambda.1se`** | **selects nothing** | **0** (`lambda.min`: 0, reported) |
| binomial λ_max | same identity as gaussian, rel < 1e-12 | 3.095e-16 |
| **binomial lasso KKT, all 71 λ** | **≤ p·√(2·thresh) = 2.8e-6** | **1.263e-08** |
| binomial intercept score `mean(y−p)` | same bound | 6.274e-16 |
| **λ = 0 vs `glm(binomial)`** | **≤ 1e3·κ_Fisher·ε = 3.5e-12** | **3.289e-15** |

Every bound is set as `ok(x, tol)`, which fails on a non-finite value. `NaN > tol` is FALSE, so a
plain comparison would pass a NaN.

### Why these bounds

**KKT is the definition of the answer.** With `standardize=TRUE` the penalty is on `b_j·s_j`,
where `s_j` is the *population* sd. So in original units the lasso solution must satisfy
`x_jᵀr/n = λ·s_j·sign(b_j)` where `b_j ≠ 0` and `|x_jᵀr/n| ≤ λ·s_j` where `b_j = 0`. Here
`r = y − fitted` (gaussian) or `y − p` (binomial). Any `b` that satisfies them is the minimiser.
glmnet's documented rule stops a sweep when no single update changes the objective by more
than `thresh` × the null deviance. That deviance is 1 on glmnet's internally standardised
gaussian y, and below 2 per observation for binomial. So each last step is under √thresh
(√(2·thresh)), and the gradient is off by at most the sum of the later steps: **p·√thresh**. The control proves the check discriminates: the neighbouring λ's coefficients
miss by 25,000× the bound. The default-thresh row shows the √thresh scaling directly.

**The closed-form legs run at `thresh = 1e-30`.** That pushes the coordinate-descent residual
(2e-14) below rounding. What is left is round-off in two different solvers, bounded by
conditioning × ε ([tolerances come from the problem](../../practices/cross-checks.md)): κ is
12.8 for the standardised Gram matrix and 15.7 for the Fisher information. Here `lm()` uses QR,
`glm()` uses IRLS and the ridge reference uses `solve()`, so these are genuine second routes.

**λ is in y units.** glmnet standardises y internally for gaussian, so the same reported λ
on an unscaled response means a ridge penalty of `λ/sd_y`. That form is asserted, and the naive
reading is reported 0.16 off.

**Support recovery is a design claim.** The design has 5 planted effects of 1–2 standardised
units against noise sd 1, with neighbour correlation ≈ 0.45. Exact recovery by the relaxed fit
held in 40 of 40 seeds of this design, and so did the entry order. Plain lasso at `lambda.1se`
held in 13 of 40, so it is reported, not asserted. The permuted null selected nothing at
`lambda.1se` in all 200 seeds of five designs, this one included. The pinned seed and `foldid` make the run itself deterministic.

### Pins

| | |
|---|---|
| data | **none staged** — `X`, the gaussian `y` and the binomial `yb` come from `set.seed(20261016)` |
| checks | `identities.R`, pinned by sha256 |
| image | `quay.io/aarchsci/r@sha256:f224ffc9bff5…` — R 4.5.3, glmnet 5.0 (tag `2026.10.16`) |

cosign-verified against `playgroundlogic/aarchsci`. The signature covers the **manifest-list**
digest (`sha256:77cd253cfcd8…`), so verify the tag and pin the arm64 digest. This is a newer
digest than [r](../r/README.md) and [r-spatial](../r-spatial/README.md) pin; those recipes stay
on the image they were verified against. No x86 run: the `r` env has no x86 build.

### Run + verify

```sh
make stage RECIPE=glmnet
spawn task run --spec "$(make -s spec RECIPE=glmnet)" --wait
aws s3 cp "s3://$(make -s print-bucket)/runs/glmnet/r1/score.tsv" -
```

The run fails on any of these:

- a pin mismatch, or a missing `GLMNET IDENTITIES OK` sentinel
- a λ_max that is not the closed form
- any KKT violation over the bound, or a control that does not violate
- ridge or λ=0 off its closed form, `lm()` or `glm()`
- non-deterministic CV, or a support that is not exactly the planted one
- a null that selects anything

Check the bucket regardless ([exit 0 isn't proof](../../practices/container-path.md)).

### Not covered

These are untouched:

- other families: `poisson`, `cox`, `multinomial`, `mgaussian`
- sparse `Matrix` input
- `penalty.factor`, box constraints (`lower.limits`/`upper.limits`) and offsets
- `p > n`, where the `lm()`/`glm()` legs no longer exist but KKT still holds
- glmnet's own test suite
- any performance claim: this is one box at one small size

</details>
