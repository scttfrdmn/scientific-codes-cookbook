---
tool: dipy
tool_version: "1.12.1"
env: neuroimaging
image: quay.io/aarchsci/neuroimaging@sha256:cb8a08c524d7aff435432265a8c9ffce81429b0dc8c4685653b8557691529277
spawn_version: 0.126.1
last_verified: 2026-10-10
---
# DIPY + AFNI — diffusion tensors against a closed form, on Graviton

Fits DIPY's DTI model to a signal synthesised from a known tensor and checks it against the analytic FA, then checks AFNI and nibabel agree on the same NIfTI. For anyone doing neuroimaging on ARM.

## Run it

```bash
make stage RECIPE=neuroimaging    # once: the checks only — there is no data to stage
spawn task run --spec "$(make -s spec RECIPE=neuroimaging)" --wait
make ls RECIPE=neuroimaging

sig = single_tensor(gtab, S0=100., evals=[1.5e-3, 0.4e-3, 0.4e-3], evecs=np.eye(3), snr=None)
TensorModel(gtab).fit(vol).fa          # must equal 0.686161147707, which is arithmetic
3dTstat -mean -prefix m.nii.gz dwi.nii.gz    # must equal numpy's mean over the same file
```

## Make it yours

| In the recipe | Swap for | What to know |
|---|---|---|
| the synthesised signal | `dipy.data.fetch_*` | **the fetchers download at run time**, which this recipe avoids — and the synthetic route is *better*, not a workaround: FA and MD have closed forms in the eigenvalues, so the truth is exact instead of borrowed. |
| `snr=None` | a finite SNR | noise-free is what makes the comparison a correctness check rather than a statistical one. Add noise and you are testing the estimator's variance — a different, valid question needing a different bound. |
| `evals=[1.5e-3, 0.4e-3, 0.4e-3]` | your own | any triple works; the analytic FA follows. Keep all three positive or the tensor is not physical. |
| `3dTstat`, `3dcalc` | `3dDWItoDT` | **absent from this build.** The conda AFNI package ships a subset — no diffusion toolbox — so AFNI cannot fit tensors here. See below. |
| float32 NIfTI | scaled int16 | **the part worth copying.** Scanner data arrives as integers with `scl_slope`/`scl_inter`, and that is where two tools silently disagree. The recipe checks it, *and* checks that disagreement was possible. |

**Leave the fixture** — there isn't one to leave. Nothing is fetched and nothing is pinned but the checks themselves, because the expected answer is a formula. **Scale it** by pointing the same checks at your own acquisition; the closed form does not care about voxel count.

## Shape, size, cost

One task on `m8g.large` (2 vCPU / 8 GiB), TTL 30m as a **backstop** with `cost_limit` $0.10 as the real guard. The whole analysis is seconds; the recorded **68 s** window is boot, Docker install and image pull, so **these timings are not compute cost** ([layout](../../patterns/layout-and-effective-cost.md)).

<details>
<summary>As shipped: a constructed truth to 2e-15, an exact zero, and a cross-tool agreement with its own vacuity guard</summary>

### The checks

| observable | assertion | observed |
|---|---|---|
| staged bytes | match pin | matches |
| AFNI programs used | present before use | `afni`, `3dinfo`, `3dcalc`, `3dTstat` |
| AFNI version | == `AFNI_25.0.00` | 25.0.00 'Severus Alexander' |
| dipy / nibabel / numpy | read from the running install | 1.12.1 / 5.4.2 / 2.5.3 |
| **DIPY FA vs analytic 0.686161147707** | **max error < 1e-10** | **2.109e-15** |
| **DIPY MD vs analytic 7.666666666667e-04** | **max error < 1e-12** | **1.843e-18** |
| **isotropic tensor FA** | **< 1e-9 (exactly 0 analytically)** | **5.839e-13** |
| FA monotone in anisotropy | increases on every rung | yes |
| **`3dcalc a*2+1` vs numpy** | **difference == 0** | **0.000e+00** |
| **`3dTstat -mean` vs numpy** | **< 1e-6 relative** | **1.029e-07** |
| AFNI vs nibabel, scaled int16 | < 1e-3 absolute | 3.391e-05 |
| **the same, if the slope were ignored** | **≥ 1000× further off** | **6.584e+07×** |

### The truth is a formula, so there is nothing to fetch

Fractional anisotropy is a function of the eigenvalues alone:

```text
FA = sqrt( 1.5 · Σ(λᵢ − λ̄)² / Σλᵢ² )
```

So a signal synthesised from `λ = (1.5, 0.4, 0.4)·10⁻³` has `FA = 0.686161147707` and
`MD = 7.666666666667e-04` **before any tool runs**. Fitting it back is then a correctness check
with no reference dataset, no download, and no tolerance beyond conditioning: DIPY returns the
generating parameters to **2.1e-15** and **1.8e-18**.

The staging script verifies that arithmetic *locally*, in four lines, before an instance is paid
for — including that an isotropic tensor gives exactly `0.0`. If the closed form were wrong the
recipe would chase a wrong target on a running box.

`snr=None` is load-bearing. A noise-free signal makes this a statement about the estimator's
correctness; with noise it becomes a statement about its variance, which needs a statistical
bound rather than 1e-10.

### Two identities that need no reference at all

**An isotropic tensor has FA exactly zero** — the numerator is the variance of the eigenvalues,
so it vanishes by construction. Measured **5.839e-13**, which is the conditioning of the fit, not
a modelling choice. An implementation that normalised wrongly could not satisfy this however
plausible its output looked on real data.

**FA increases monotonically with anisotropy**, checked across four rungs. Cheap, and it catches a
sign or normalisation error that the two cases above would both pass.

### The cross-tool check, and why its *negative* control is the interesting half

`3dDWItoDT` is absent, so this is not the DTI cross-validation it might have been. What AFNI and
nibabel both do is read the same NIfTI — and that is where pipelines diverge silently, so it is
worth checking properly:

```text
float32, unscaled      3dTstat -mean vs numpy      1.029e-07 relative   (float32 eps is 1.19e-07)
int16 with scl_slope   AFNI vs nibabel             3.391e-05 absolute
      the same, if the slope were ignored          2.233e+03 absolute
      discrimination ratio                         6.584e+07
```

**Agreement is worthless unless disagreement was possible.** If both tools ignored
`scl_slope`/`scl_inter` they would agree with each other just as neatly — so the recipe computes
that alternative too and asserts it is at least 1000× further away. It is **6.6e+07×** further
away, which is what makes the agreement mean something. Scanner data arrives as scaled integers;
a pipeline that drops the slope produces plausible images with wrong units.

`3dcalc`'s arithmetic is checked as **exactly** equal to numpy's, not merely close — `a*2+1` over
a float32 volume comes back with zero difference, so the assertion is `== 0` with no tolerance to
argue about.

### Pins

| | |
|---|---|
| data | **none.** Every signal is synthesised; the expected values are closed forms |
| checks | `identities.py`, staged and pinned by sha256 |
| image | `quay.io/aarchsci/neuroimaging@sha256:cb8a08c5…` — dipy 1.12.1, nibabel 5.4.2, nilearn 0.14.1, AFNI 25.0.00, python 3.14.8 |

Versions are read from the running install and AFNI's version is **asserted**, because the
numbers above were taken on `AFNI_25.0.00` and a different build is a different measurement.

cosign-verified against `playgroundlogic/aarchsci`; the signature covers the **manifest-list**
digest, so verify the tag and pin the arm64 digest.

### Run + verify

```sh
make stage RECIPE=neuroimaging
spawn task run --spec "$(make -s spec RECIPE=neuroimaging)" --wait
aws s3 cp "s3://$(make -s print-bucket)/runs/neuroimaging/r1/score.tsv" -
```

Fails on a pin mismatch, a missing AFNI program, an AFNI that is not 25.0.00, a recovered tensor
more than 1e-10 from its closed form, a non-zero FA on an isotropic tensor, non-monotone FA, a
`3dcalc` result that is not bit-equal to numpy, an AFNI/nibabel disagreement — or an agreement
that fails to discriminate against the ignore-the-slope reading. But check the bucket regardless
([exit 0 isn't proof](../../practices/container-path.md)).

### Not covered

**`3dDWItoDT` and AFNI's whole diffusion toolbox are absent from this build** — also
`3dDTtoDWI`, `1dDW_Grad_o_Mat++`, `3dAutomask` and `nifti_tool`. The conda AFNI package ships a
subset of AFNI's programs, so the DTI cross-validation between AFNI and DIPY that this recipe was
first designed around is **not reachable here**; it would need an image carrying the full AFNI
distribution. That is the honest reason the cross-check is about NIfTI interpretation instead.

Beyond that: real diffusion data (everything here is synthetic, so nothing speaks to motion,
eddy-current or susceptibility correction), tractography and connectomes, DIPY's other
reconstruction models (CSD, DKI, free water), registration and spatial normalisation, nilearn's
whole first-level/second-level GLM surface — nilearn is in this env and unexercised — and AFNI's
preprocessing pipelines (`afni_proc.py`), which are what most AFNI users actually run.

</details>
