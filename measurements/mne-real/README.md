# MNE-Python on a whole MEG session — Graviton4, Intel and AMD

> **One subject's complete six-run MEG session goes through the standard MNE pipeline (Maxwell
> filter, ICA, epochs, decoding, dipole fit) and every assertion holds on all three chips. Every
> decision also comes out identical: 887 triggers, 857 epochs kept, the same ICA artefact components,
> and faces decoded at AUC 0.8371 at 168 ms.** AMD EPYC runs it 1.8× faster than Graviton4 and is 24%
> cheaper billed. Intel matches Graviton4's speed and costs 16% more.

Workload: **subject 01 of OpenNeuro ds000117 v2.0.0** (Wakeman & Henson 2015, *Sci. Data* 2:150001,
doi:10.18112/openneuro.ds000117.v2.0.0). That is all six runs of the face-recognition task: 306-channel
Elekta MEG plus 70 EEG at 1100 Hz, 3.29 M samples (50 min), 5.24 GB of FIF, with the site's
fine-calibration and cross-talk files. The pipeline follows the steps MNE-BIDS-Pipeline runs on
this dataset:

1. Per run: find triggers, mark noisy and flat sensors, Maxwell-filter (SSS, every run moved to
   run 01's head position), then band-pass 1–40 Hz.
2. Per session: epoch −0.2…0.8 s with peak-to-peak rejection, fit ICA, remove the EOG and ECG
   components, and average famous, unfamiliar and scrambled faces.
3. Decode faces from scrambled faces over time.
4. Fit a dipole to the face response.

**Why one subject is the representative size:** the subject's session is the unit of MEG
preprocessing. MNE-BIDS-Pipeline runs this whole pipeline once per subject, and group statistics
then read the per-subject evokeds, which are kilobytes. A cohort is this task
[fanned out](../../patterns/job-arrays.md) 16 times. A single-run slice would misrepresent it: ICA
and decoding both run on the concatenated session.

**Data tier: RODA.** The data is OpenNeuro's bucket on AWS Open Data, copied server-side into the
bucket. The path is "latest", so the pin is [`sources.tsv`](sources.tsv): each object's S3 VersionId
and single-part ETag (its md5). [`00-pin.task.json`](00-pin.task.json) checks the staged copies
against those md5s on AWS and writes [`data.sha256`](data.sha256), which every run checks at stage-in
([provenance](../../practices/input-provenance.md)).

## Run it

```sh
export AWS_PROFILE=aws B=<your bucket>
bash measurements/mne-real/stage-inputs.sh "$B"     # server-side copy from s3://openneuro.org; pins on AWS
for a in arm64 x86_64 amd; do
  spawn task run --spec "$(python3 scripts/spec_arch.py mne $a "$B" "$(date +%s)" \
    measurements/mne-real/real.task.json)" --wait
done
bash measurements/mne-real/collect.sh "$B" <scratch dir>   # results/, results/compare.txt
```

[`real.py`](real.py) is the workload and its checks. Its central calls are:

```python
raw = mne.preprocessing.maxwell_filter(raw, calibration=CAL, cross_talk=CTC, origin=origin,
                                       coord_frame="head", destination=ref_info["dev_head_t"])
ica = mne.preprocessing.ICA(n_components=0.999, method="fastica", random_state=97).fit(raw, picks="meg", decim=5, reject=reject)
auc = cross_val_multiscore(SlidingEstimator(make_pipeline(StandardScaler(), LogisticRegression(solver="liblinear")),
                           scoring="roc_auc"), X, y, cv=StratifiedKFold(5, shuffle=True, random_state=0))
```

## Results

`r8g.2xlarge`, `r8i.2xlarge` and `r8a.2xlarge` (8 vCPU, 64 GiB). Graviton4 runs the recipe's
aarch.science `neuroimaging` image and both x86 chips run its linux-64 env twin, on the same staged
bytes:

| chip | instance | tool wall | billed | peak RSS | $/hr | compute $/result | billed $/result |
|---|---|---|---|---|---|---|---|
| Graviton4 | `r8g.2xlarge` | 658 s | 740 s | 32.5 GiB | 0.4713 | 0.0861 | 0.0969 |
| Xeon 6975P-C | `r8i.2xlarge` | 663 s | 726 s | 32.5 GiB | 0.5557 | 0.1023 | 0.1121 |
| **EPYC 9R45** | `r8a.2xlarge` | **362 s** | **413 s** | 32.5 GiB | 0.6390 | **0.0643** | **0.0733** |

A result is the whole verified session: six runs preprocessed, plus ICA, evokeds, decoding with five
nulls, both dipole fits and every check below. "Billed" is spawn's task window (stage-in to
stage-out). $/hr is spawn's `cost_estimate` divided by that window.

| phase, summed over runs (s) | Graviton4 | Xeon | EPYC |
|---|---|---|---|
| `find_bad_channels_maxwell` ×6 | 188.5 | 210.2 | 98.1 |
| `maxwell_filter` ×6 | 37.8 | 21.9 | 12.6 |
| FIR 1–40 Hz ×6 | 17.5 | 19.3 | 13.4 |
| ICA fit (fastica, 43 components) | 66.3 | 75.3 | 40.4 |
| decoding: 221 time points × 5 folds × (1 real + 5 nulls) | 331.3 | 308.1 | 185.2 |
| everything else (read, epochs, evoked, dipoles, checks) | 16.6 | 28.2 | 12.3 |

- **AMD is 1.82× Graviton4 overall and fastest in every phase.** Its hour costs 36% more, so it is
  25% cheaper per result in compute and 24% cheaper billed. Intel is as fast as Graviton4 overall,
  1.7× faster at `maxwell_filter` (BLAS), a little faster at decoding, and slower at
  bad-sensor detection, filtering and ICA. Its hour makes it 16% dearer per
  result.
- **Decoding is half of every run, and it is single-threaded.** liblinear fits one model at a time
  (`n_jobs=1`), so the cgroup averaged 1.7–1.9 busy cores of 8. `n_jobs` would spread the 6,630 fits
  across cores, with one BLAS thread per worker.
- **The box is sized by memory.** The process peaks at **32.5 GiB** (`ru_maxrss`, identical on all
  three chips). The sampler timeline puts it around the concatenation and the ICA fit on the
  whole session (306 channels × 3.29 M samples). The staged 5.2 GB sits in tmpfs on top of that. An `m8g.2xlarge` (32 GiB) would not fit, so
  this is an `r` box. The 5 s RSS sampler in `real.sh` read only 15.9–27.9 GiB, because the peak is
  brief, so take peak memory from the kernel ([sizing](../../patterns/sizing.md)).
- **Overhead is 51–82 s of each bill.** Stage-in of 5.2 GB took 12 s. The rest is Docker and the
  image or env-twin install.
- n = 2 per chip. A first round of three runs with an identical `real.py` (only the RSS line added
  since) produced outputs with the same sha256 as the round reported here, on each chip.

## What held at scale, on all three chips

Every row is asserted inside the task by [`real.py`](real.py). A failure fails the run.

| claim | why it must hold | Graviton4 | Xeon | EPYC |
|---|---|---|---|---|
| 887 stimulus triggers (STI101 low byte) == events.tsv codes, each onset = trigger + 37 samples | published: the 34 ms projector delay Wakeman & Henson measured, round(0.034 × 1100) | 6 of 6 runs | same | same |
| 587 button presses == events.tsv samples with zero delay (one L+R press listed under one code) | buttons have no projector | 6 of 6 runs | same | same |
| Parseval: one-segment boxcar PSD of 4 whole-run channels | identity | 5.6e-16 | 8.9e-16 | 8.9e-16 |
| FIR linear on a real MEG + EEG channel, whole run | linear filter | 1.2e-15 | same | same |
| average reference on 70 real EEG channels: sums to 0 | identity | 9.7e-15 | same | same |
| **`maxwell_filter(2.5a − 0.7b) = 2.5f(a) − 0.7f(b)`**, two real 20 s segments, < 1e-12 | **SSS is a linear projection** | **3.6e-15** | **3.8e-15** | **3.8e-15** |
| **SSS output has rank `nfree`: σ[nfree]/σ[nfree−1] < 1e-8**, every run | **reconstruction from nfree internal moments** | **≤ 6.9e-12** | **≤ 3.6e-12** | **≤ 3.6e-12** |
| epoch average == numpy mean; full-rate baseline mean == 0 (857 × 306 × 1101) | identity | 0; 1.1e-16 | 0; 1.3e-16 | 0; 1.3e-16 |
| ICA `apply(exclude=[])` through all 306 PCA components returns its input | lossless unmix/remix | 3.8e-15 | 3.0e-15 | 2.7e-15 |
| at least one ICA component matches the EOG | artefact present | [11, 16] | same | same |
| **faces vs scrambled peak AUC > every one of 5 permuted-label nulls**, peak after onset | **decodable signal** | **0.8371 vs 0.6075, 168 ms** | same | same |
| planted 50 nAm dipole, real 306-sensor geometry and real noise covariance: < 1 mm, < 1°, 1% | noise-free forward model | 0.247 mm, 0.20°, 50.20 | same | same |

Reported, not asserted:
- The faces − scrambled gradiometer RMS peaks at **159 ms**: the M170.
- Rejection kept 857 of 887 epochs (285 famous, 286 unfamiliar, 286 scrambled).
- No sensor was marked noisy or flat in any run, and `nfree` was 44, 44, 45, 43, 44, 44.
- Baseline was removed at 1100 Hz, so after decimation to 220 Hz the pre-stimulus mean is 7.7e-3 of
  the peak, not 0.
- The real single-dipole fit to the face response is poor: GOF 17.4%, at the 200 ms edge of the
  window. The M170 is bilateral, and one dipole in a sphere does not describe it. A distributed
  inverse needs a BEM, and that needs a FreeSurfer subject.

## Across chips: what is a fixed point and what is a path

**Every decision is identical on all three chips:** the triggers, the bad-sensor lists, `nfree`, the
857 epochs kept (same selection), the 43 ICA components and the EOG [11, 16] and ECG [26, 17] picks,
the 159 ms peak, and the decoding summary to the printed four digits. Graviton4 and the two x86 chips
run different builds and different BLAS kernels: `neoversev2` against `Cooperlake`.

| quantity | Graviton4 vs Xeon | Graviton4 vs EPYC | Xeon vs EPYC | kind |
|---|---|---|---|---|
| SSS singular values, the kept 44 | 1.2e-13 rel | 1.0e-13 | 1.2e-14 | fixed point (linear algebra) |
| evoked arrays, max \|Δ\|/max | 8.0e-11 | 8.0e-11 | 2.5e-13 | fixed point (linear pipeline) |
| ICA mixing matrix | 3.1e-9 | 3.1e-9 | 4.6e-12 | iterative, converged |
| decoding AUC curve, max \|Δ\| | 9.2e-5 | 9.2e-5 | 9.2e-5 | iterative, inside liblinear's `tol = 1e-4` |
| planted dipole position | 1.0e-10 mm | 4.6e-11 mm | 1.1e-10 mm | well-posed fit |
| real dipole position | 0.26 mm | 1.6 mm | 1.4 mm | **path**: ill-posed fit, GOF 17% |

The linear pipeline differs by rounding (8e-11 after SSS, filtering, ICA cleaning and averaging),
and every fixed point agrees far inside its assertion. The real dipole fit is the one path. Its
objective is flat around a poor single-dipole solution, so the optimiser stops up to 1.6 mm apart
on different chips. The planted dipole, under the same optimiser in the same geometry, agrees to
1e-10 mm. **Intel and AMD are close but not byte-identical**: both report OpenBLAS `Cooperlake`, yet
the evokeds differ at 2.5e-13. Each chip reproduces its own bytes run to run. Which library
dispatches differently between the two x86 chips was not traced.

## Caveats

- One subject, sensor level. Source imaging with a BEM, time-frequency, cluster statistics and
  EEG-based analysis were not run. This pipeline uses MEG and drops the EEG after the reference check.
- No movement compensation. Every run is aligned to run 01's head position. The cHPI coils sit at 293–328 Hz, above the 40 Hz low-pass.
- The head-fitted SSS origin is (3.3, 36.9, −23.1) mm, from `fit_sphere_to_headshape`'s defaults on
  this subject's digitisation.
- Specs ran with `cost_limit` $0.50 (×1.3 and ×1.6 for Intel and AMD) and a 1 h TTL backstop.
  Prices are on-demand us-west-2, from spawn's estimate for these instances.

## Spend

Ten launches, **$0.71** of compute in all. The six verifying runs (two rounds of three) cost $0.56.
The rest was the pin task, two single-run probes and one full-session sizing run. The cross-region
copy of 5.2 GB from us-east-1 adds about $0.10 of transfer, once.
