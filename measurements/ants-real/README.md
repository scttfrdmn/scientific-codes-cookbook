# ANTs on a real workload — a 125-subject T1 cohort normalised to MNI152, Intel and AMD

> **AMD EPYC runs the cohort 1.58× faster and a quarter cheaper per subject than same-generation Intel, and
> the two chips produce byte-identical warps for all 125 subjects.** Graviton4 is a recorded gap:
> there is no arm64 build of ANTs.

Workload: the **whole** NeuroFeedback Skull-stripped repository (NFBS; Puccio et al. 2016,
[doi:10.1186/s13742-016-0150-5](https://doi.org/10.1186/s13742-016-0150-5)): 125 T1w MPRAGE scans
from the Enhanced Rockland Sample, each with a **manually corrected** brain and brain mask. These are
the bytes [fsl-real](../fsl-real/README.md) registered. Every subject's curated brain goes through
`antsRegistrationSyNQuick.sh` to FSL's MNI152 2 mm brain (rigid, then affine, then SyN), the standard
ANTs normalisation. The recipe's identities run on the real scan as well. A cohort is what this is
run on, and 125 is a typical study size.

## Run it

```sh
export AWS_PROFILE=aws B=<your bucket>
measurements/fsl-real/stage-inputs.sh "$B"                     # NFBS from RODA, once; shared with fsl-real
measurements/ants-real/stage-inputs.sh "$B" template           # scripts; prints the template task to launch
spawn task run --spec "${TMPDIR:-/tmp}/ants-real-template.json" --wait   # MNI152 2 mm out of FSL's package
measurements/ants-real/stage-inputs.sh "$B"                    # checks the template against mni.sha256
for a in x86_64 amd; do
  spawn task run --spec "$(python3 scripts/spec_arch.py ants $a "$B" r1 measurements/ants-real/cohort.task.json)" --wait
done
```

Each subject (`subject.sh`, pinned by sha256 like `run.sh`) is, at its core:

```sh
antsRegistrationSyNQuick.sh -d 3 -f MNI152_T1_2mm_brain.nii.gz -m brain.nii.gz -t s -n 1 -e 42 -o s_
```

## Results

16 vCPU / 32 GiB, 16 subjects at a time (ITK single-threaded, `-n 1`), the same image and the same bytes:

| chip | instance | cores × threads | cohort wall | billed | peak RSS | $/hr | compute $/subject | billed $/subject |
|---|---|---|---|---|---|---|---|---|
| Graviton4 | — | — | **gap: no arm64 build** | | | | | |
| Intel Xeon 6975P-C | `c8i.4xlarge` | 8 × 2 | 2,087 s | 2,154 s | 10.4 GiB | 0.7497 | 0.00348 | 0.00359 |
| **AMD EPYC 9R45** | `c8a.4xlarge` | 16 × 1 | **1,325 s** | 1,376 s | 10.6 GiB | 0.8622 | **0.00254** | **0.00264** |

- **AMD is 1.58× faster for 15% more per hour, so 27% cheaper per subject in compute and 26% billed.** By step (mean per
  subject): SyN 26.1 s on Intel against 19.4 s on AMD, the planted-motion rigid registration 67.1 against 46.2 s.
- **That is per vCPU, which is what is rented.** 16 vCPU is 8 cores × 2 hyperthreads on `c8i` and
  16 full cores on `c8a`, and 16 single-threaded jobs fill both (`avg_cores` 12.45 and 12.52; the
  shortfall is the last, partial round of 125 / 16).
- **Compute-dominated:** 51–67 s of boot, staging, extraction and stage-out against 22–35 min of ANTs.
- Peak RSS is the container cgroup, including the extracted cohort in `/tmp` (tmpfs high-water
  3.3 GiB). About 0.45 GiB per running subject beyond that, so 2 GiB/vCPU (`c8*`) is ample.
- n = 1 per chip. ANTs is deterministic here: the 16-subject probe and the cohort, on different
  Intel instances, gave byte-identical warps for those 16 subjects.
- Graviton4 has no row because there is nothing to run on it. conda-forge's `ants` 2.6.5 is
  linux-64 only and the project's `antsx/ants` image is amd64-only. The request is
  [ants-feedstock#35](https://github.com/conda-forge/ants-feedstock/issues/35). FSL, on the same
  scans, is in [fsl-real](../fsl-real/README.md).

## What held at scale, on both chips

| assertion | bound | Intel | AMD |
|---|---|---|---|
| all subjects complete | 125 of the archive's 125 | 125 | 125 |
| **identity transform, linear interpolation** | `Σ|out − in| / Σ|in|` below one float32 ulp (2⁻²⁴) | max **1.4e-16** | max **1.4e-16** |
| **a rigid motion planted on each subject's own brain** (6° + 5.7 mm, 21.60 mm at the corners), undone by `antsRegistration` | < ¼ voxel = 0.25 mm at every corner | max **0.074 mm**, median 0.0063 | identical |
| **brain registered to itself** | `max|T − I| < 1e-6` | **0** on all 125 | identical |
| **SyN improves on its own affine stage** (curated mask carried to MNI152, Dice vs MNI152's brain mask) | every subject | **125 / 125**, median 0.9528 → 0.9643 | identical |
| SyN raises the correlation to the template | every subject | 125 / 125, median 0.914 → 0.940 | identical |
| **the SyN warp is diffeomorphic**: Jacobian determinant > 0 | 0 voxels with `det J ≤ 0`, whole field | **0** (min in brain 0.108) | identical |
| **its inverse undoes it**: `|W⁻¹(W(x)) − x|`, mean inside the MNI152 brain | < 0.1 mm (1/20 of a template voxel; a missing inverse leaves the ~4 mm warp) | max **0.044 mm**, median 0.035 | identical |

The identity is not bit-exact on these real headers as it is on the recipe's fixture. It sums to about 3e-8
over a scan, which is ~1e-16 relative: double-precision rounding. The bound says "nothing beyond
rounding", and a resampling error of a hundredth of a voxel would be of order 1e-3.

Reported, not asserted: the worst inverse-consistency error at any brain voxel is 0.96 mm (median
subject 0.50). The warp moves brain voxels by 4.0 mm on average.

## Fixed point or path, across chips

**Neither: every number is byte-identical between Intel and AMD.** That includes the SHA-256 of
every subject's rigid transform, affine and SyN warp field (125 / 125 each), and every Dice,
correlation, Jacobian and inverse-consistency value ([`results/compare.txt`](results/compare.txt)).
That fits the build: one amd64 binary on both chips, single-threaded, a fixed seed (`-e 42`), and
no BLAS or LAPACK library linked (`ldd`), so there is no runtime kernel dispatch to differ. Optimisers are
paths, and these paths do not diverge here because nothing in the arithmetic changes between the
two chips. A Graviton build would be a different binary, and nothing here predicts its bits.

## Against FSL on the same scans (observation)

Same 125 scans, same MNI152 2 mm bytes (from the same `fsl-data_standard` package), same metric:

| | affine | nonlinear |
|---|---|---|
| ANTs (`antsRegistrationSyNQuick.sh`, curated brain) | median Dice **0.9528** | SyN **0.9643** |
| FSL ([fsl-real](../fsl-real/README.md): FLIRT on BET's brain, FNIRT on the whole head) | 0.9029 | FNIRT 0.9553 |

SyN's Dice is higher than FNIRT's on 120 of 125 subjects. This is **not** a cross-validation. The
inputs differ (FSL registers BET's brain and the whole head, ANTs the curated brain), and the metric
measures overlap with a template mask, not agreement between the two codes. Both pipelines improve
on their own affine start for every subject.

## Pins

| | |
|---|---|
| data | RODA: `s3://fcp-indi/data/Projects/RocklandSample/NFBS_Dataset.tar.gz`, 1,751,464,473 bytes, **sha256 `fd616b9e…40d2cc`**, staged by fsl-real and asserted on the box |
| template | `MNI152_T1_2mm_brain` and `_brain_mask` from `fsl-data_standard-2208.0-0.tar.bz2` (sha256 `2faefb4d…1b828a`, recipes/fsl's lock), unpacked by `00-template.task.json`; the two files pinned in `mni.sha256` |
| image | the recipe's `docker.io/antsx/ants@sha256:59c45f54…`, binary `2.6.5.dev1-gfdce4d2` |
| scripts | `run.sh`, `subject.sh` staged and pinned by sha256 in the spec; `mkspec.py` writes the specs |

Per-subject rows for both chips are in [`results/`](results/); `compare.py` writes `results/compare.txt`.

## Caveats

n = 1 per chip. Sized from a 16-subject probe on `c8i.4xlarge` (SyN ~26 s and under 0.5 GiB per subject).
The rigid check in the probe used full sampling. The cohort uses 25% regular sampling with a fixed seed, which
cut that step from 200 s to 67 s and still recovers the motion to 0.074 mm at worst. The template
is 2 mm, the resolution fsl-real used. A 1 mm template was not measured.
