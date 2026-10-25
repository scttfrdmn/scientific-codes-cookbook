# Amber `pmemd.cuda` on L4 and L40S: saturated compute, idle memory, and a 36-second JIT

> **On an 18,310-atom TIP3P box, a 250,000-step run held 91% GPU utilisation on an L4 (544.62
> ns/day) and 88% on an L40S (768.51), using 386 MB of 23,034 and 653 MB of 46,068.** The first
> run on a fresh instance spends 36 s before the GPU does anything. The second starts in 1 s.

Amber 26 `pmemd.cuda_SPFP`, built from Scott's licensed source in `nvidia/cuda:12.8.1-devel` and
run natively on the Deep Learning Base OSS Nvidia Driver AMI (Amazon Linux 2023), through `spawn
launch` (spawn#601). 2026-09-18. The data behind [Do you need a GPU, and which
one](../../patterns/gpu-tradeoff.md). The licensed build is not redistributable, so it stays in the
private bucket. Its numbers are publishable.

Workload ([`md.in`](md.in)): 25,000 steps (250,000 for the long run), dt 2 fs, PME, 10 Å cut,
SHAKE, Langevin at 300 K, built with `tleap`. Inputs, not committed (`inputs/amber/` in the
bucket): `sys.prmtop` sha256 `1be2c133d6f0b6b6c5936c77af6ad20322139714ae3d1c58c17791ae60792bab`,
`sys.rst7` `6579ae10c7adf4506dd2bb5cf4e8168d69210d35d8024c179341f3161c33f604`.

## Cold, warm, long — `results/g6-firm/` (L4) and `results/g6e-firm/` (L40S)

[`amber_firm.sh`](amber_firm.sh) runs three phases on one fresh instance:

| card | phase | steps | ns/day | wall, measured / Amber's | to first GPU activity | active samples, mean | peak mem |
|---|---|---|---|---|---|---|---|
| L4 | cold | 25,000 | 556.24 | 56.8 / 16 s | **36 s** | 8, 83% | 386 MB |
| L4 | warm | 25,000 | 553.69 | 8.3 / 8 s | **1 s** | 8, 91% | 386 MB |
| L4 | **long** | 250,000 | **544.62** | 79.8 / 80 s | 0 s | 80, **91%** (peak 92) | 386 MB |
| L40S | cold | 25,000 | 770.67 | 58.3 / 14 s | 34 s | 5, 88% | 653 MB |
| L40S | warm | 25,000 | 762.24 | 6.2 / 7 s | 1 s | 6, 88% | 653 MB |
| L40S | **long** | 250,000 | **768.51** | 56.7 / 56 s | 0 s | 57, **88%** (peak 89) | 653 MB |

`~/.nv/ComputeCache` is absent before the cold run and 68K after it. A cold start therefore pays a
one-off driver JIT of a binary that carries no native cubin for the card. In the long runs every
sample after the first is at or above 90% (L4) or 86% (L40S). The plateau is unambiguous there, as
it is not in the short runs.

On memory, 386 / 23,034 MB is **1.68%** of the L4, and 653 / 46,068 MB is **1.42%** of the L40S.

## The run that looked fine — `results/g6n1/` vs `results/g6n2/`

| run | `ntwr` | ns/day | util mean / max | compute wall |
|---|---|---|---|---|
| `g6n1` | −1 (a restart file every step) | 32.04 | 5.7 / 14% | 187.0 s |
| `g6n2` | 25000 (one at the end) | **561.25** | 9.6 / 92% | 65.7 s |

Both report `status: OK`. `g6n1` measured restart-file writes rather than the L4, and only the
utilisation shows it. The committed `md.in` (uploaded 15:54) is the fixed one: `g6n1` finished at
15:52 and `g6n2` at 15:57. `g6n2`'s 9.6% mean against seven samples at 90–92% is the startup
artifact the firm-up run then measured (Amber's own wall is 16 s of 65.7).

## The failures on the way — kept as evidence

| run | card | what failed |
|---|---|---|
| `g5b` | A10G | `missing-libs: libgfortran.so.5`. The CUDA-devel image that built the binary does not run it |
| `g6` | L4 | `libemil.so: cannot open shared object file`. The build's RPATH is absolute |
| `g6d` | L4 | `No space left on device` with the root volume at 75G/75G, from the `ntwr=-1` restart writes |

**Not captured:** driver version, the A10G and H100 results (an A10G `cfg/amber_g5.yaml` and two
`cfg/amberbig_p5_*.yaml` watches exist, but no p5 result prefix does), and the build itself.
`runs/amber/build.log` and `build3.log` stay in the bucket, because they are compile output of
licensed source. Scripts: `amber_measure.sh` (container runs), `amber_measure_native.sh` (`g6n*`),
`amber_firm.sh` (firm-up). Artifacts copied verbatim from
`s3://cookbook-942542972736-us-west-2/{runs,inputs}/amber/`.
