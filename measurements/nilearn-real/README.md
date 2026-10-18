# Nilearn on the whole quality-checked ABIDE I cohort — Graviton4, Intel and AMD

> **All 871 subjects go through region extraction, connectomes, the tangent embedding and a
> cross-validated autism classifier, and every identity holds on all three chips.** The region signals
> are bit-identical across chips and so is every prediction. AMD finishes the analysis 1.5× faster than
> Graviton4, but Graviton4 is the cheapest per result billed, because staging 93 GiB is half of every bill.
> Along the way the run found that the release's own `rois_cc200` time series put the atlas 3 mm away
> from where its header says it is.

Workload: **ABIDE I Preprocessed** (Preprocessed Connectomes Project; C-PAC, `filt_noglobal`), the
871 subjects that `nilearn.datasets.fetch_abide_pcp(quality_checked=True)` returns. That is 403 autism
and 468 control subjects from 20 sites, 168,434 volumes, 99.3 GB of `func_preproc` images. It goes
through the analysis that nilearn's ABIDE examples and Abraham et al. 2017 (*NeuroImage* 147:736) run:

- CC200 region signals with `NiftiLabelsMasker`;
- per-subject connectomes;
- the group tangent embedding (`ConnectivityMeasure(kind="tangent")`);
- a `LinearSVC` on the tangent vectors, cross-validated over 10 folds stratified by site × diagnosis,
  and leave-one-site-out.

This is the whole quality-checked release, the cohort ABIDE classification papers report on. A
smaller subset would not reach site heterogeneity, and leave-one-site-out needs every site.

**Data tier: RODA.** The release is in `s3://fcp-indi` (INDI, AWS Open Data). [`stage-inputs.sh`](stage-inputs.sh)
copies each of the 2,616 objects S3-to-S3 (server-side CopyObject), so nothing passes through a
laptop. It asks S3 to compute each object's full-object SHA256 as it writes it. Those digests are
[`abide.sha256`](abide.sha256), which [`pins.sha256`](pins.sha256) pins in turn. The task checks every
staged byte against them before anything reads the data (5–6 s, 32 at a time).

## Run it

```sh
export AWS_PROFILE=aws B=<your bucket>
bash measurements/nilearn-real/stage-inputs.sh "$B"     # ~100 GB, server-side; a re-stage only re-reads checksums
for a in arm64 x86_64 amd; do
  spawn task run --spec "$(python3 scripts/spec_arch.py nilearn $a "$B" "$(date +%s)" \
    measurements/nilearn-real/real.task.json)" --wait
done
bash measurements/nilearn-real/collect.sh "$B" <scratch dir>   # results/, results/compare.txt
```

[`real.py`](real.py) is the workload and its checks. Its user-facing calls are:

```python
NiftiLabelsMasker(labels_img=cc200, strategy="mean", standardize=False, detrend=False).fit_transform(img)
ConnectivityMeasure(kind="tangent").fit_transform(signals)                     # 871 subjects, 180 regions
cm = ConnectivityMeasure(kind="tangent", vectorize=True, discard_diagonal=True)  # refit inside every fold
LinearSVC(C=1.0).fit(cm.fit_transform(train), y_train)
```

## Results

`r8g.8xlarge`, `r8i.8xlarge` and `r8a.8xlarge` (32 vCPU, 256 GiB), 32 worker processes with one
BLAS thread each, the recipe's image on Graviton4 and the `neuroimaging` env's linux-64 twin on the
two x86 chips, the same staged bytes:

| chip | instance | tool wall | billed | peak RSS | $/hr | compute $/result | billed $/result |
|---|---|---|---|---|---|---|---|
| **Graviton4** | `r8g.8xlarge` | 459 s | 953 s | 59.5 GiB | 1.8851 | 0.2404 | **0.4990** |
| Xeon 6975P-C | `r8i.8xlarge` | 527 s | 1,148 s | 69.8 GiB | 2.2227 | 0.3254 | 0.7088 |
| EPYC 9R45 | `r8a.8xlarge` | **300 s** | **811 s** | 57.0 GiB | 2.5562 | **0.2130** | 0.5758 |

A result is the whole verified run: 871 subjects extracted and checked, the group tangent fit, and
three cross-validations of 10, 20 and 10 folds.

| phase (s) | Graviton4 | Xeon | EPYC |
|---|---|---|---|
| per subject ×871: load, CC200 masker, hand mean, C-PAC check, mask round trip, detrend, Pearson | 181.8 | 301.0 | 156.6 |
| tangent fit, 871 subjects (one process) | 62.6 | 46.2 | 33.2 |
| 10-fold CV, tangent refit per fold | 63.1 | 49.9 | 33.3 |
| leave-one-site-out, 20 folds | 76.1 | 70.1 | 36.9 |
| permuted-label control, 10 folds | 63.0 | 49.6 | 33.2 |

- **The box is chosen by staging, not by compute.** Spawn stages into host `/tmp`, a tmpfs of half the
  RAM. The 93 GiB of images do not fit the 64 GiB tmpfs of a 128 GiB box, so 256 GiB is the floor. The
  workers peak at 57–70 GiB on top of that. An 8-worker sizing run on 88 subjects peaked at 14.8 GiB, about
  1.85 GiB a worker.
- **AMD is 1.53× Graviton4 on the tool, and 11% cheaper per result in compute.** Its hour is 36%
  dearer, though, and the 494–621 s of staging and boot are billed at that rate too, so Graviton4 is
  13% cheaper billed. Intel is slowest at the per-subject step: its 32 vCPU are 16 cores × 2 threads,
  while the other two have 32 full cores. It is the dearest per result either way.
- **Graviton4 beats Intel only on the per-subject step**, which is decompression and array reductions.
  On the eigendecompositions of the tangent fit (one process) and the CV folds built on them, both x86
  chips are faster.
- **Overhead is half or more of every bill**: staging 93 GiB, plus the image pull (arm64) or the offline
  env install (x86). These timings are not compute cost. A cohort you analyse more than once
  belongs on a box that keeps it.
- n = 1 per chip. The pipeline is deterministic (fixed fold seeds, `LinearSVC(random_state=0)`), and
  all three chips gave identical predictions, so one run per cell is defensible.

## What held at scale, on all three chips

Every row is asserted inside the task by [`real.py`](real.py). A failure fails the run.

| claim | why it must hold | Graviton4 | Xeon | EPYC |
|---|---|---|---|---|
| 871 subjects, unique FILE_ID and SUB_ID, 403 / 468, 20 sites, all three files staged | the release, nilearn's QC filter | yes | yes | yes |
| atlas-to-image voxel map from the two affines is an integer shift | nearest resampling is then exact | (1,1,0) | same | same |
| **`NiftiLabelsMasker` vs a hand mean over each label's voxels, every element** | **≤ the float64 summation bound n·ε·mean\|x\|** | **0 (bit-equal)** | **0** | **0** |
| `NiftiMasker` round trip, in-mask / out-of-mask, on each subject's own mask | a gather and a scatter | 0 / 0 | 0 / 0 | 0 / 0 |
| `signal.clean(detrend=True)` ⟂ [1, t], max rel | a projection | 3.5e-17 | 2.8e-17 | 2.8e-17 |
| correlation with `EmpiricalCovariance` vs `np.corrcoef`, < 1e-12 | Pearson | 2.0e-15 | 3.2e-15 | 3.2e-15 |
| **C-PAC's `rois_cc200` vs a mean over the index-aligned atlas, every element** | **cross-code: ≤ n·ε₃₂·mean\|x\| + ulp₃₂ + 5e-7** | **6.6e-6 max, inside** | same | same |
| control: C-PAC vs nilearn's header-aligned signals must differ by > 1 | the placements differ | 402.8 | same | same |
| **tangent: ‖mean of the 871 tangent matrices‖_F / n² < 1e-7, no max_iter warning** | **the Fréchet-mean fixed point, nilearn's own tol** | **4.51e-8** | **4.51e-8** | **4.51e-8** |
| control: the same gradient at the arithmetic mean must be > 100× it | control | 5.76e-4 | same | same |
| tangent: whitening · mean · whitening = I, < 10³·κ·ε = 1.6e-11 (κ 72.2) | `whitening_` is mean^-½ | 2.4e-14 | 4.5e-14 | 4.5e-14 |
| tangent: `inverse_transform` recovers all 871 covariances, < 10³·κ_max·ε = 1.5e-9 | exp is log's inverse | 4.9e-14 | 9.1e-14 | 9.1e-14 |

The tangent gradient is computed independently at the returned mean, from the transform's own
output, not read back from the iteration. Twenty regions are constant in 1–9 subjects, so the group uses the 180 regions that are live in all 871. No
region has zero voxels in the image.

Reported, not asserted:
- **10-fold CV accuracy 0.705** (folds 0.632–0.770); **leave-one-site-out 0.677**. With the
  diagnosis permuted, 0.509. Abraham et al. 2017 report about 67% for this pipeline shape on this cohort.
- Volumes per subject 78 / 176 / 316 (min / median / max).

## The release's CC200 time series sit 3 mm off the atlas

The release ships C-PAC's own CC200 time series beside the images it extracted them from. The
values match nilearn's reduction **only when the atlas is laid on the image by array index**
(max 6.6e-6), not where the two NIfTI headers place it. The atlas file is 63×75×61 with its origin at
(93, −129, −72) mm. The images are 61×73×61 at (90, −126, −72). Index alignment therefore moves every
region 3 mm in x and 3 mm in y. The header is the right placement: the regions' centres of mass
through the atlas header agree with the release's own `CC200_ROI_labels.csv` to **0.08 mm**. Through
index alignment they are off by up to **4.31 mm**.

The effect on the data: per region, C-PAC's series correlate with nilearn's at a median of 0.960. But
610 of 871 subjects have some region below r = 0.8, 32 have one below 0.5, and the worst is
**r = −0.23**. Anyone who analyses `rois_cc200` from this release is analysing regions displaced by
one voxel diagonally. Extracting from `func_preproc` with nilearn, as here, avoids it. Only CC200 was
checked; the release's other `rois_*` atlases were not.

## Across chips: what is a fixed point and what is a path

**Identical on all three chips:**
- the region signals, all 871 arrays by sha256: the masker is a sum, with no BLAS in it;
- every 10-fold and leave-one-site-out prediction, so every accuracy;
- the tangent gradient (4.507e-8) and every exact identity above.

**The tangent mean differs only in its last bits.** Against Graviton4, both x86 chips differ by at
most 1.3e-11, or 4.5e-14 relative; 31 of 32,400 entries are bit-identical. It is a converged fixed
point computed through `eigh` and matrix products, so this is rounding at the fixed point. It is
reported in [`results/compare.txt`](results/compare.txt), not asserted. **Intel and AMD are
byte-identical to each other**, the tangent mean included. They run the same linux-64 twin, and
OpenBLAS 0.3.34 picks `Cooperlake` on both, because it has no Granite Rapids or Zen 5 kernel. Graviton4
runs `neoversev2`.

## Caveats

- One atlas (CC200) and one preprocessing strategy (`filt_noglobal`). Surface data, `FirstLevelModel` on
  task fMRI, and searchlight decoding were not run.
- The images are stored as float64, which is why each worker needs ~1.85 GiB.
- The specs ran with `cost_limit` 2.0 (scaled ×1.3 / ×1.6 for x86) and a 3 h TTL. They are retightened
  from these runs to 1.0 and 90 m. Prices are on-demand us-west-2, derived from spawn's cost estimate
  over each run's window.

## Spend

About **$1.82** of EC2 for the three verifying runs ($1.78), two probes and an 88-subject sizing run.
Copying 99.6 GB from us-east-1 is about $2 of inter-region transfer, once. The staged copy costs
about $2.30 a month to keep in the bucket.
