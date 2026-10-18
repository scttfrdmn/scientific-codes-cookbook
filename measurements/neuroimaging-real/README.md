# DIPY on a real workload — a 123-subject diffusion cohort, Graviton4, Intel and AMD

> **AMD runs the cohort 1.29× faster than Graviton4 and Intel 1.26× slower; per subject Graviton4
> is cheapest in compute and AMD by a hair billed.** Every identity holds on all three, and DIPY
> reproduces FSL's `dtifit` to float32 rounding — but only after matching two things `dtifit` does
> that its documentation does not mention, without which the two codes differ by 0.028 in FA.

Workload: the baseline diffusion scan (`ses-BAS1`, `DIFF_137_AP`: Siemens TrioTim, 2 mm isotropic,
9 b=0 + 128 directions at b=1500) of every NFBS subject that has one — **123 of the 125** that
[fsl-real](../fsl-real/README.md) skull-strips, from the Enhanced Rockland Sample on AWS Open Data.
Each subject gets what a DTI study runs: a median-Otsu brain mask, DIPY's tensor fit by OLS and by
its default WLS, and FA/MD maps; FSL's `dtifit` fits the same bytes as an independent second code.
A cohort is the unit this is run on, and 123 is a typical study size.

## Run it

```sh
make stage RECIPE=fsl && make stage RECIPE=neuroimaging ARCH=x86_64      # FSL packages, the env twin
measurements/neuroimaging-real/stage-inputs.sh "$(make -s print-bucket)"   # 123 scans, dtifit, scripts
B=$(make -s print-bucket); M=measurements/neuroimaging-real
for t in fsl dipy; do sed "s|\${COOKBOOK_BUCKET}|$B|g" $M/$t-cohort.task.json > /tmp/$t.json; spawn task run --spec /tmp/$t.json --wait; done
spawn task run --spec "$(python3 scripts/spec_arch.py fsl amd $B r1 $M/fsl-cohort.task.json)" --wait            # or x86_64
spawn task run --spec "$(python3 scripts/spec_arch.py neuroimaging amd $B r1 $M/dipy-cohort.task.json)" --wait   # or x86_64
sed "s|\${COOKBOOK_BUCKET}|$B|g" $M/compare.task.json > /tmp/c.json && spawn task run --spec /tmp/c.json --wait   # all three chips
```

Two single-tool tasks per chip, S3 between them: `dtifit` first (FSL's image), then DIPY (the
recipe's image), which reads the same chip's `dtifit` maps.

## Results

16 vCPU / 64 GiB, 16 subjects at a time (each process single-threaded, `OPENBLAS_NUM_THREADS=1`),
same image per architecture, same bytes. **DIPY task** (mask, OLS, WLS and every check below):

| chip | instance | cohort wall | billed | $/hr | compute $/subject | billed $/subject | peak RSS |
|---|---|---|---|---|---|---|---|
| Graviton4 (Neoverse-V2) | `m8g.4xlarge` | 220 s | 340 s | 0.7181 | **0.000357** | 0.000551 | 22.4 GiB |
| Intel Xeon 6975P-C | `m8i.4xlarge` | 278 s | 355 s | 0.8467 | 0.000532 | 0.000679 | 23.9 GiB |
| **AMD EPYC 9R45** | `m8a.4xlarge` | **171 s** | **241 s** | 0.9738 | 0.000376 | **0.000530** | 23.1 GiB |

`dtifit` task: 100 / 136 / 69 s wall, 186 / 221 / 145 s billed, 0.000162 / 0.000260 / 0.000152
compute $/subject (Graviton4 / Intel / AMD); peak RSS 8.0 GiB on all three.

- **AMD 1.29× faster than Graviton4, Intel 1.26× slower** — for both codes (`dtifit` 1.45× and
  1.36×). AMD's 35% higher hourly price eats its speed: compute $/subject is Graviton4's +5%, billed
  is −4% only because a shorter run carries less fixed overhead.
- **Per vCPU, which is what is rented:** 16 vCPU is 16 cores on `m8g` and `m8a`, and 8 cores × 2
  hyperthreads on `m8i` (`lscpu` threads per core 1 / 2 / 1). 16 single-threaded jobs fill all
  three (`avg_cores` 15.29 / 15.33 / 15.34). That is why Intel is last: half the cores.
- **Where the time goes** (summed over subjects, Graviton4): `median_otsu` 46%, the checks 27%, WLS
  18%, AFNI 4%, OLS 1%. The brain mask costs 39× the OLS fit it gates.
- **Boot dominates** a run this short: 120 s of the 340 s billed on Graviton4 is boot, Docker,
  staging 9.2 GB, the image pull and the sha256 check, so **these timings are not compute cost**.
- Peak RSS is the container cgroup, including 11.7 GiB of `/tmp` (the 9.2 GB of scans plus `dtifit`'s
  maps) — 73% of a 32 GiB box's 16 GiB tmpfs, which is why this is `m8g`, not `c8g`. ~0.6 GiB per process.
- n = 1 per cell. Both codes are deterministic: Intel and AMD produce **byte-identical** outputs.

## What held at scale, on all three chips

| assertion | bound | observed (identical on all three unless noted) |
|---|---|---|
| all subjects complete, 137 volumes each | 123 / 123 | 123 / 123 |
| **DIPY OLS vs `dtifit`, FA** — modes matched (below) | < 1e-6 | **8.508e-08** |
| DIPY OLS vs `dtifit`, MD (relative) / eigenvalues (÷ MD) | < 1e-6 | 5.959e-08 / 1.501e-07 |
| **a tensor planted at every brain voxel**, at the subject's real gradient table, recovered to its closed-form FA | < 1e-10 (OLS and WLS) | 1.5e-11 / 3.3e-11 (G4); 1.1e-11 / 3.3e-11 (Intel, AMD) |
| **FA and MD invariant under rotating the gradient table** | < 1e-10 | FA 1.7e-11, MD 4.1e-13 (G4); 1.6e-11, 2.1e-13 (Intel, AMD) |
| `fslmaths -Tmin -bin` mask == numpy's all-volumes-positive mask | identical | 123 / 123 |
| AFNI `3dTstat -mean` vs numpy | < 1e-6 relative | 6.695e-08 |

Compared over the 99.836% of brain voxels whose tensor is positive definite and unclipped. The
dtifit bound is 16 float32 ulps: `dtifit` computes FA in single precision and writes float32 maps,
and 8.5e-8 is about one ulp. Reported, not asserted: median FA in the mask 0.178 (OLS) / 0.179
(WLS), median MD 7.20e-4 mm²/s, WLS moves a voxel's FA by a median 0.0033 from OLS.

## Two things `dtifit` does that DIPY does not

Read from `dtifit.cc` (fsl-fdt 2604.0), and each measured on the cohort:

| DIPY vs `dtifit`, max FA difference | |
|---|---|
| neither matched (the naive comparison) | 2.831e-02 |
| bvec renormalisation matched only | 2.831e-02 |
| 1%-of-S0 signal floor matched only | 6.639e-06 |
| **both matched** | **8.508e-08** |

- **`dtifit`'s OLS floors the signal.** It fits once, estimates S0, raises every sample ≤ 1% of S0
  to 1% of S0, and fits again. That binds in **20,060** voxels (0.10% of brain, mostly CSF, where
  b=1500 drives the signal into the noise floor) and moves FA by up to 0.028 there.
- **`dtifit` renormalises the bvecs** to unit length (in single precision). These files are unit only
  to ~1e-6, so that alone moves diffusivities by ~1e-9 mm²/s — invisible next to the floor.
- **`dtifit` does not clip negative eigenvalues; DIPY clips them at 1e-6 / −min(design matrix)**, ≈ 6.7e-10 mm²/s, not 0. So
  `dtifit` reports FA > 1 in 15,907 voxels and DIPY a clipped tensor there; those 0.16% are excluded
  as convention, not fit. DIPY's clip level depends on the gradient table's orientation, so in those
  voxels the rotation invariance fails by up to 9.7e-4 — a property of the clip, not the fit.

## Fixed point or path, across chips

Every quantity is a **fixed point**: the mask is a filter plus a histogram threshold, OLS is a
closed-form solve and DIPY's WLS one reweighting of it. All asserted, against Graviton4
([`results/compare.txt`](results/compare.txt)); Intel and AMD give the same numbers:

| quantity | bound | Intel and AMD vs Graviton4 |
|---|---|---|
| median-Otsu brain mask | identical | **123 / 123** byte-identical |
| DIPY MD (OLS / WLS) | < 1e-12 mm²/s | 6.3e-16 / 2.8e-15 |
| DIPY FA where MD ≥ 1e-4 mm²/s (OLS / WLS) | < 1e-9 | 1.2e-11 / 7.4e-12 |
| `dtifit` positive mask / FA / L1–L3 | identical / ≤ 1e-6 | identical / 1.19e-07 (1 float32 ulp) / ≤ 8.9e-08 of max |

The cross-chip difference is absolute in diffusivity — a few ulps of log S through a fit of
condition ~3e3 — so FA, a ratio, amplifies it where MD is near zero: over *every* voxel the largest
FA difference is 5.6e-9, in a voxel with MD 1.3e-9 mm²/s. That is why FA is bounded where
diffusivity is tissue-scale and MD absolutely. No DIPY map is byte-identical across architectures;
the x86 build on Intel and on AMD is byte-identical in every output.

## Pins

| | |
|---|---|
| data | RODA: `s3://fcp-indi/data/Projects/RocklandSample/RawDataBIDSLatest/sub-*/ses-BAS1/dwi/`, the 123 subjects in [`subjects.txt`](subjects.txt), 492 files, 9.21 GB, written 2019-10-31. The prefix says "Latest", so **the pin is [`dwi.sha256`](dwi.sha256)**, computed on AWS by `pin.task.json` and asserted on every box before any compute |
| DIPY | the recipe's `quay.io/aarchsci/neuroimaging` arm64 digest; on x86 its `env_x86_64` twin. dipy 1.12.1, nibabel 5.4.2, numpy 2.5.3, AFNI 25.0.00; OpenBLAS `neoversev2` on Graviton4, `Cooperlake` on both Intel and AMD |
| dtifit | `fsl-dtifit-linux-{aarch64,64}.lock`: `recipes/fsl`'s 89 packages verbatim plus `fsl-fdt` 2604.0, URL + sha256, from FSL's channel |
| scripts | `fsl.sh`, `dipy.sh`, `subject.py`, `compare.py`, pinned by sha256 in each spec; `mkspec.py` writes the specs |

Per-subject rows for every chip are in [`results/`](results/).

## Caveats

n = 1 per chip. Sized from a 16-subject probe on `m8g.4xlarge` (DIPY ~26 s and ~0.6 GiB per
subject, `dtifit` ~11 s; tmpfs 10.7 GiB). The two matched modes and the compared voxel set were
found on that probe; each bound above is set by the precision of the quantity it bounds, not by
the value observed.
