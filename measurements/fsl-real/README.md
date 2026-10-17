# FSL on a real workload — a 125-subject T1 cohort, Graviton4 and x86

> **Graviton4 runs the cohort 1.53× faster and 44% cheaper per subject than same-generation Intel,
> and every assertion holds on both — but on real scans BET is a path, not a fixed point:** the
> template mask that was byte-identical across chips in [the recipe](../../recipes/fsl/README.md)
> is identical for only 102 of 125 subjects here.

Workload: the **whole** NeuroFeedback Skull-stripped repository (NFBS; Puccio et al. 2016,
[doi:10.1186/s13742-016-0150-5](https://doi.org/10.1186/s13742-016-0150-5)) — 125 T1w MPRAGE
scans (1 mm, neck included) from the Enhanced Rockland Sample, each with a **manually corrected**
brain mask. Every subject goes through the standard FSL structural chain: `fslreorient2std` →
`robustfov` → `bet` → `flirt` 12-DOF to MNI152 2 mm → `fnirt --config=T1_2_MNI152_2mm`, plus a
planted-motion FLIRT. A cohort is what this chain is run on, and 125 is a typical study size.

## Run it

```sh
make stage RECIPE=fsl                                                   # FSL, from FSL's channel
measurements/fsl-real/stage-inputs.sh "$(make -s print-bucket)"         # NFBS + fnirt/applywarp
B=$(make -s print-bucket)
sed "s|\${COOKBOOK_BUCKET}|$B|g" measurements/fsl-real/cohort.task.json > /tmp/c.json
spawn task run --spec /tmp/c.json --wait                                                # Graviton4
spawn task run --spec "$(python3 scripts/spec_arch.py fsl x86_64 $B r1 measurements/fsl-real/cohort.task.json)" --wait  # x86
```

## Results

16 vCPU / 32 GiB, 16 subjects at a time (each tool single-threaded), same image per architecture,
same lock-verified FSL, same bytes:

| chip | instance | cohort wall | billed | $/hr | compute $/subject | billed $/subject | peak RSS |
|---|---|---|---|---|---|---|---|
| **Graviton4** | `c8g.4xlarge` | **2,332 s** | 2,403 s | 0.6381 | **0.00331** | **0.00341** | 15.9 GiB |
| Intel Xeon 6975P-C | `c8i.4xlarge` | 3,577 s | 3,645 s | 0.7497 | 0.00596 | 0.00607 | 16.1 GiB |

- **1.53× faster, 44% cheaper per subject on Graviton4.** By step (summed over the cohort), Intel
  took 1.37× as long for `robustfov`+`bet`, 1.96× for the 6-DOF FLIRT, 1.71× for the 12-DOF
  FLIRT and 1.52× for FNIRT, which is 80% of the work (212 s of a 267 s subject on Graviton4).
- **That is per vCPU, which is what is rented:** 16 vCPU is 16 cores on `c8g` and 8 cores × 2
  hyperthreads on `c8i`. 16 single-threaded jobs fill both (`avg_cores` 15.17 and 15.43).
- **Compute-dominated:** 68–71 s of boot, install, staging and stage-out against 39–60 min of FSL.
- Peak RSS is the container cgroup, including the 5.5 GiB of `/tmp` the extracted cohort occupies
  (tmpfs high-water 5.6 GiB on both). ~0.6 GiB per FNIRT, so 2 GiB/vCPU (`c8g`) is ample.
- n = 1 per cell. FSL is deterministic on a given chip, but not across chips (below).

## What held at scale, on both chips

| assertion | bound | Graviton4 | x86 |
|---|---|---|---|
| all subjects complete | 125 of the archive's 125 | 125 | 125 |
| **FLIRT undoes a motion planted on each subject's own curated brain** (6°, 21.60 mm at the corners) | < ¼ voxel = 0.25 mm, every subject | max **0.198 mm**, median 0.085 | max **0.206 mm**, median 0.085 |
| **FNIRT improves on its own affine start** (curated mask warped to MNI152, Dice vs MNI152's brain mask) | every subject | **125 / 125**, min gain 0.011 | **125 / 125**, min gain 0.011 |
| BET (after `robustfov`) vs the curated mask, Dice | ≥ 0.80 per subject — a gross-failure guard | min 0.832, median 0.901 | min 0.832, median 0.901 |

Reported, not asserted: FLIRT-only Dice to MNI152 median 0.903 → FNIRT 0.955 (both chips); BET's
containment of the curated mask median 0.844 — BET's brain is *smaller* than NFBS's generous masks,
the opposite of its 6.8% superset on the MNI152 template.

**`robustfov` is not optional on a scan with a neck.** Plain `bet` on the whole-head image gives
median Dice 0.75 and falls below 0.80 on **75 of 125** subjects (worst 0.34); after FSL's own
`robustfov` crop the worst is 0.83, and it improves every one of the 125. A pipeline that skips it
also drags FLIRT down — the probe's three FNIRT-worse-than-affine subjects all came from it.

## Fixed point or path, across chips

| quantity | identical across chips | largest difference |
|---|---|---|
| BET mask after `robustfov` | **102 / 125** byte-identical | A00054857: 1,026,298 vs 968,097 voxels (Dice 0.930 vs 0.906) |
| plain BET (Dice to curated mask) | 42 / 125 | 0.056 Dice |
| FLIRT planted-motion residual | 0 / 125 | 0.150 mm |
| FLIRT 12-DOF Dice to MNI152 | 0 / 125 | 0.021 |
| FNIRT Dice to MNI152 | 0 / 125 | 0.0036 |

All of these are **paths**: BET deforms a surface mesh over a fixed number of iterations, FLIRT
and FNIRT are optimisers, and a last-bit difference on one chip can carry each to a different
stopping point. The template's byte-identity in the recipe was a property of the template — smooth,
symmetric, no neck — not of BET. This is why no assertion above compares chips digit for digit:
each holds on each chip by its own bound, and the cohort statistics agree to three or four digits.

## Pins

| | |
|---|---|
| data | RODA: `s3://fcp-indi/data/Projects/RocklandSample/NFBS_Dataset.tar.gz`, 1,751,464,473 bytes, written 2016-10-17, **sha256 `fd616b9e…40d2cc`** — asserted on the box before any compute |
| FSL | `fsl-real-linux-{aarch64,64}.lock`: the recipe's 89 packages verbatim plus `fsl-fnirt` 2203.2 and `fsl-fugue` 2603.0 (for `applywarp`), URL + sha256, from FSL's channel |
| image | the recipe's `mambaorg/micromamba` per-architecture digests |
| scripts | `run.sh`, `subject.sh` staged and pinned by sha256 in the spec; `mkspec.py` writes the specs |

Raw per-subject rows for both chips are in [`results/`](results/), with the cross-chip comparison
from [`compare.py`](compare.py) in `results/compare.txt`.

## Caveats

n = 1 per chip. Sized from two 8-subject probes on `m8g.2xlarge` (FNIRT ~220 s and ~0.6 GiB per
subject, tmpfs 5.3 GiB); the second probe ran `run.sh` without the BET guard line. The `nproc`
line in `summary.txt` reads 1 because GNU `nproc` honours `OMP_NUM_THREADS=1`; `avg_cores` is the
measured parallelism.
