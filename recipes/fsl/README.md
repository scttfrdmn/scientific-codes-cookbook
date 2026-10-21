---
tool: fsl
tool_version: "fsl-base 2610.0 (bet2 2111.9, flirt 2111.4)"
image: docker.io/mambaorg/micromamba@sha256:26c186ed2c9fdfd7aab1e7f8416b6ef54358557db8a0d9a7d11a07d1d224f1b2
images_x86_64:
  - docker.io/mambaorg/micromamba@sha256:9a2239d03b9464c761ac87c13f45b37ec0f774db7d1b152a5f9add146bee2e72
last_verified_x86_64: 2026-10-16
last_verified_amd: 2026-10-18
spawn_version: 0.127.0
last_verified: 2026-10-16
---
# FSL — BET against FSL's own brain mask, FLIRT undoing a planted motion, on both chips

Installs FSL from its publisher's own conda channel and checks brain extraction against FSL's published MNI152 mask and rigid registration against a planted motion, on Graviton4 and x86. For anyone running FSL on AWS — including on ARM, which no image provides.

## Run it

```bash
make stage RECIPE=fsl       # once: FSL's packages for BOTH architectures, from FSL's own channel, lock-verified
spawn task run --spec "$(make -s spec RECIPE=fsl)" --wait                # Graviton4
spawn task run --spec "$(make -s spec RECIPE=fsl ARCH=x86_64)" --wait    # same spec, x86

bet MNI152_T1_1mm.nii.gz brain -m -f 0.5
flirt -in moving -ref fixed -dof 6 -omat recovered.mat
```

## Make it yours

| In the recipe | Swap for | What to know |
|---|---|---|
| **you stage FSL** | a public FSL image | **FSL's licence forbids redistributing it without its source** ("all original and amended source code is included in any transmitted product"), so no public binary image may carry it — which is why aarch.* cannot. `make stage` downloads FSL from FSL itself, into your account. **Non-commercial use only.** |
| **FSL's own channel** | conda-forge | FSL is not on conda-forge, but FSL's channel ships **`linux-aarch64` at the same versions as `linux-64`** — so FSL runs on Graviton today. |
| `fsl-linux-*.lock` | re-solving | every package is pinned by URL and sha256 (89 per architecture, 19 from FSL); staging fails on any mismatch, and the task installs **offline** in ~12 s. |
| `-f 0.5` | your threshold | `-f` is BET's fractional intensity threshold: higher keeps less. Measured on MNI152: 0.2 → 2.66 M voxels, 0.5 → 1.95 M, 0.8 → 1.47 M. |
| the bundled MNI152 templates | your T1 | `fsl-data_standard` (196 MB) ships the templates *and* FSL's own brain masks, which is what makes BET checkable without fetching anything. |

**Leave the fixture.** FSL's published brain mask is an external reference for BET, and a planted motion is an exact one for FLIRT. **Scale it** to your own scans: the registration check transfers directly — plant a motion, recover it.

## Which box — [measured](../../measurements/fsl-real/README.md) (125 real T1s: robustfov, BET, FLIRT, FNIRT to MNI152)

| chip | instance | cohort wall | $/hr | **billed $/subject** |
|---|---|---|---|---|
| **Graviton4** | `c8g.4xlarge` | 39 min | 0.6381 | **0.0034** |
| Intel | `c8i.4xlarge` | 60 min | 0.7497 | 0.0061 |
| AMD | `c8a.4xlarge` | **36 min** | 0.8622 | 0.0045 |

**Take Graviton4: cheapest per subject — 44% under Intel, 23% under AMD**, which is 7% faster but dearer by the hour. Run `robustfov` before `bet` on any scan with a neck — without it BET falls below Dice 0.80 on 75 of 125 subjects.

## Shape, size, cost

One task on `m8g.xlarge` / `m8i.xlarge` (4 vCPU / 16 GiB), TTL 45m as a **backstop** with `cost_limit` $0.25 as the real guard. Install, three BETs and two FLIRTs take seconds of a ~1.3-minute window on either chip ([layout](../../patterns/layout-and-effective-cost.md)).

<details>
<summary>As shipped: BET contains 99.98% of FSL's brain mask, FLIRT undoes a 19.7 mm motion to 0.13 mm, and BET's mask is byte-identical across chips</summary>

### The checks

| observable | assertion | arm64 | x86_64 |
|---|---|---|---|
| packages | 89 match the lock; offline install | ✓ | ✓ |
| **BET mask contains FSL's MNI152 brain mask** | **≥ 0.99** | **0.9998** | **0.9998** |
| BET vs FSL's mask, Dice | ≥ 0.95 | 0.9670 | 0.9670 |
| **control: `-f` 0.2 / 0.5 / 0.8** | **volume strictly decreasing** | 2,662,046 > 1,950,961 > 1,471,328 | identical |
| planted rigid motion | must move the corners > 10 mm | 19.650 mm | 19.650 mm |
| **FLIRT: `recovered · planted − I` at the corners** | **< 0.5 mm** (¼ voxel) | **0.1261 mm** | **0.1268 mm** |
| self-registration | < 0.5 mm | 0.0053 mm | 0.0053 mm |
| BET mask bytes | — | `b025f725…` | **byte-identical** |

### BET against FSL's own answer

`fsl-data_standard` ships FSL's brain mask for the MNI152 template, so BET can be checked against a
published reference rather than a band. The claim worth asserting is **containment**: BET's brain
holds 1,826,850 of the reference's 1,827,243 voxels (0.9998), while being 6.8% larger in total —
a slightly generous superset, which is how BET is known to err. Dice (0.967) is reported and
bounded too, but containment is the sharper statement.

The `-f` ladder is the control. A threshold that did nothing would leave the volume unchanged; this
one must take it strictly down as `-f` rises — 2.66 M → 1.95 M → 1.47 M voxels — which is what
BET's documentation says `-f` does.

### FLIRT undoes a motion it was never told

The task builds a 6° rotation about the image centre plus a (4.0, −2.5, 3.0) mm translation in
FSL's own millimetre convention, applies it with `flirt -applyxfm`, and registers the result back
with a 6-DOF fit. Since `flirt` maps input to reference, the recovered matrix must be the planted
one's inverse, so `recovered · planted` must be the identity. That residual is measured
physically — as the displacement it causes at the corners of the 2 mm volume — so the bound is in
millimetres: **0.126 mm**, against a planted motion that moves the corners **19.65 mm**, bounded
at a quarter voxel.

### What is identical across chips, and what is a path

**BET is byte-identical** on Graviton4 and Intel — the same mask to the last bit. **FLIRT's
recovered matrix is not**: its terms differ around the sixth decimal (rotation ~5e-6, translation
~7e-4 mm). FLIRT is an iterative optimiser, so its result is a path, and floating-point evaluation
on each chip moves the last digits; the residual it leaves (0.1261 vs 0.1268 mm) is the same to a
fraction of a micron, and an order of magnitude inside the bound. Neither assertion depends on
those digits.

On real scans BET is a path too: across 125 NFBS subjects its mask is byte-identical on 102
([measured](../../measurements/fsl-real/README.md)). The template's identity is a property of the
template, not of BET.

### Pins

| | |
|---|---|
| FSL | `fsl-linux-64.lock` and `fsl-linux-aarch64.lock` — 89 packages each, URL + sha256, solved from `fsl.fmrib.ox.ac.uk/fsldownloads/fslconda/public` + conda-forge. `fsl-base` 2610.0, `fsl-bet2` 2111.9, `fsl-flirt` 2111.4, `fsl-fast4` 2111.3, `fsl-avwutils` 2209.8, `fsl-data_standard` 2208.0 — **identical on both architectures** |
| base image | `docker.io/mambaorg/micromamba` 2.9.0-debian13 — arm64 `26c186ed…`, amd64 `9a2239d0…`, the per-architecture entries of one manifest list. Not cosign-signed, so the digests are the pins |
| fixture | none staged: FSL's own MNI152 templates and brain mask, from `fsl-data_standard` |

FSL itself is never in this repository or any image we publish: the locks are metadata, and the
packages come from FSL to your bucket when you stage.

### Run + verify

```sh
make stage RECIPE=fsl
spawn task run --spec "$(make -s spec RECIPE=fsl)" --wait
spawn task run --spec "$(make -s spec RECIPE=fsl ARCH=x86_64)" --wait
aws s3 cp "s3://$(make -s print-bucket)/runs/fsl/r1/smoke-check.txt" -
```

Fails on a package that does not match the lock, an install that is not offline-clean, a BET mask
that does not contain FSL's brain mask or agrees with it below Dice 0.95, a `-f` ladder that is not
strictly decreasing, a FLIRT that leaves more than 0.5 mm of the planted motion, a planted motion
too small to test, or a self-registration that moves the image. Check the bucket regardless
([exit 0 isn't proof](../../practices/container-path.md)).

### Not covered

**FAST** (tissue segmentation) is installed but not checked here, and FSL ships tissue priors that
would make a reference for it. **FEAT/MELODIC** (fMRI analysis), **FDT/BEDPOSTX** (diffusion)
and **FIRST** (subcortical) — most of what FSL is used for. FNIRT and real scans are covered by
the [measurement](../../measurements/fsl-real/README.md), not by this recipe's task. And the cross-tool check this sets up but does not
take: [ants](../ants/README.md) registers the same kind of planted motion, so both could be pointed
at one fixture and their recoveries compared.

</details>
