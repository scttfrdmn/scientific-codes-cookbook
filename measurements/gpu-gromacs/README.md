# GROMACS on GPUs: A10G, L4, L40S, the offload stages, MPS packing, and the CPU leg

> **On one 21,087-atom SPC water box at 25,000 steps, L4 ran 392.759 ns/day, L40S 489.703 and A10G
> 287.294. The best CPU box, `c8a` at 8 threads, ran 160.513.** None of the GPUs is saturated by
> one job: the mean utilisation is 24–35%. Packing four jobs onto one L4 with MPS takes the
> aggregate from 430.1 to 758.2 ns/day, and it is flat after two.

The data behind [Do you need a GPU, and which one](../../patterns/gpu-tradeoff.md). 2026-09-15 to
-21. The GPU rows use NGC `nvcr.io/hpc/gromacs@sha256:d1ed1e13321d…` (GROMACS 2023.2, `avx2_256`),
run through `spawn launch` on the Deep Learning Base OSS Nvidia Driver AMI. `spawn task run` cannot
provision a GPU AMI (spawn#601). Every GPU `docker run` is `--privileged --gpus all`, the fix for
the systemd/cgroup-v2 device reset. The system comes from `gmx solvate -cs spc216 -box 6 6 6` (7,029
SOL, as `run1-a10g/smoke-check.txt` prints), with PME, 1.0 nm cut-offs, 2 fs, h-bond constraints and
V-rescale at 300 K.

## One job per card — `results/tradeoff/g6`, `g6e`, `run2-a10g-offload`

| card | instance | ns/day | GPU util mean / max | plateau ¹ | peak mem | $/hr ² | $/ns |
|---|---|---|---|---|---|---|---|
| **L4** | `g6.2xlarge` ³ | **392.759** | 35.1 / 86% | 51% | 222 MB | 0.978 | **0.0598** |
| L40S | `g6e.2xlarge` ³ | 489.703 | 29.8 / 61% | 41% | 463 MB | 2.242 | 0.1099 |
| A10G | `g5.2xlarge` | 287.294 | 24.4 / 74% | 47.5% | 287 MB | 1.212 | 0.1012 |

¹ Median over the active window, from the first to the last sample at or above half the series'
90th-percentile sample ([the record's rule](../../practices/measurement-record.md), derived from the
committed `gpu_util*.csv`). These runs last 9–15 s, so the series has 11–19 samples and swings 0–86% within the
active window. No row here has a steady plateau. ² The rates the
[pattern page](../../patterns/gpu-tradeoff.md) used. The artifacts carry no price. ³ Size from the pattern page. The `host.log` names the card,
not the instance, and these two runs' configs are not in the bucket. The A10G size is printed by RUN 2.

The flags are `-nb gpu -pme gpu -update gpu -ntmpi 1 -ntomp 8`. GROMACS refuses explicit GPU
tasks it cannot run, so a completed run is evidence the offload happened. Only the A10G runs also
capture `md.log`'s own assignment lines (`1 GPU selected`, `PP:0,PME:0`, `PME tasks will do all
aspects on the GPU`).

**The A10G "~60%" is a different run.** `run1-a10g/` (2026-09-15, `-nb gpu` alone, 50,000 steps)
ran 465.827 ns/day with a visible plateau of 55–68% over its last ten samples (median 59). The
287.294 row is RUN 2's full-offload config at 25,000 steps. The two are not comparable in
absolute terms, because PME tuning amortises over the longer run and the A10Gs were different
physical cards.

## Which stage the GPU is for — `results/run2-a10g-offload/`

One A10G, one box, four configs, 25,000 steps each:

| config | flags | ns/day | util mean / max |
|---|---|---|---|
| all on GPU | `-nb gpu -pme gpu -update gpu` | **287.294** | 24.4 / 74% |
| PME on CPU | `-nb gpu -pme cpu -update gpu` | 111.991 | 11.5 / 53% |
| update on CPU | `-nb gpu -pme gpu -update cpu` | 245.067 | 26.9 / 45% |
| all on CPU | `-nb cpu` | 44.371 | 0.3 / 29% |

Full offload is 6.47× the CPU-only run on the same 8 vCPU. Moving PME alone to the CPU costs
2.57×. Moving the update costs 1.17×. GROMACS 2023 under `-nb gpu` alone already offloads PME and
the update (RUN 1's `md.log`), so a stage's contribution is measured by forcing it off.

## Packing one L4 with MPS — `results/mps-l4/`

| N jobs | ntomp each | per-system ns/day | aggregate ns/day | util mean | plateau ¹ | peak mem | $/ns ² |
|---|---|---|---|---|---|---|---|
| 1 | 8 | 430.1 | 430.1 | 27% | 45% | 250 MB | 0.0546 |
| **2** | 4 | 372.5 | **744.9** | 82% | 94% | 463 MB | **0.0315** |
| 3 | 2 | 252.5 | 757.4 | 91% | 99.5% | 676 MB | 0.0310 |
| 4 | 2 | 189.6 | 758.2 | 94% | 100% | 888 MB | 0.0310 |

`MPS=on` (`nvidia-cuda-mps-control -d`, inside the privileged container). Per-system throughput
falls 2.27× from N = 1 to 4, while the aggregate rises 1.76×. The aggregate gains 73.2% from 1 to
2 jobs, then 1.7% and 0.1%. *Util mean* is the series mean; the plateau is the same rule as above.

## The CPU leg — `results/tradeoff/cpu-*`

Same `.mdp` and solvate, `-nb cpu -ntomp 8`, conda-forge GROMACS 2026.3, 25,000 steps:

| box | arch | ns/day | | box | arch | ns/day |
|---|---|---|---|---|---|---|
| `c8a` | x86_64 | **160.513** | | `c6a` | x86_64 | 63.130 |
| `c7a` | x86_64 | 110.572 | | `c8i` | x86_64 | 63.106 |
| `c9g` | aarch64 | 67.047 | | `c6i` | x86_64 | 56.092 |
| `c7i` | x86_64 | 66.412 | | `c8g` | aarch64 | 50.711 |
| `c5` | x86_64 | 44.997 | | `c7g` | aarch64 | 43.992 |

**Weaker than the GPU rows, and read it that way:** the instance *size* is not recorded (8 OpenMP
threads; the cfgs for these runs are not in the bucket). Every completed row but `cpu-c8g3` ran
`condaforge/miniforge3:latest` (the x86 rows and Graviton3/5, `cpu-c7g` and `cpu-c9g`, per each
`host.log`) and installed GROMACS at run time, a tag and a solve rather than a pin. Only `cpu-c8g3` ran a pinned
image (`quay.io/aarchsci/md@sha256:1ee941664add…`). `cpu-c8g` and `cpu-c8g2` failed `no-gmx-on-path`
and are kept as failures. `cpu_canary.sh` in the bucket was re-uploaded on 2026-09-16 08:26, after
the `c7a`, `c7i` and `c8g3` runs on 09-15, so it is the later revision. The GPU and CPU legs
are different builds (NGC 2023.2 vs conda-forge 2026.3). The NGC image never completed a CPU-only
run, so any GPU-vs-CPU multiple carries the build difference too.

## Not captured, and a failure

- **Blackwell: placed, then failed.** `tradeoff/g7` (2026-09-21, `g7.2xlarge`, NVIDIA RTX PRO 4500
  Blackwell Server Edition) pulled the image and allocated 263–269 MB, but every utilisation sample
  is 0 and the result is `status: FAIL reason: mdrun`. `mdrun.log` was not uploaded, so the cause
  is not known. This was a capacity watch that fired, not a missing card.
- **Acquisition.** The H100 (`p5.4xlarge`) and `g7e` watches are in `cfg/` (`p5_*`, `hunt_g7e_*`,
  `g7e_*`). They prove the attempts were armed, not their outcome. No result prefix exists for p5
  or g7e, and the no-capacity outcome, along with the CPU and L40S placement times, lives in
  `lagotto history`, not in S3.
- **Driver and CUDA versions.** Not in any `host.log`.

Scripts: `gpu_canary.sh` (RUN 1), `gpu_canary2.sh` (RUN 2), `gpu_measure.sh` (g6, g6e and g7),
`gpu_mps.sh` (MPS) and `cpu_canary.sh`, plus the launch configs in `cfg/`. Artifacts copied verbatim
from `s3://cookbook-942542972736-us-west-2/{runs,inputs}/gpu/`.
