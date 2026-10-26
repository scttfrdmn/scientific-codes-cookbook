# GROMACS on one Graviton4 node: 8 → 192 cores, five decompositions at 64, and the launch cliff

> **benchMEM (81,743 atoms) goes from 12.995 ns/day on 8 cores to 147.669 on 192 — 11.4× — while
> ns/day per $/hr falls from 40.7 to 19.3.** Throughput never declines; the cost per nanosecond
> more than doubles. At a fixed 64 cores the decomposition spans 25%, and at 8 cores a launch that
> starves OpenMP threads costs 7.1×.

GROMACS 2026.3-conda_forge, `gmx_mpi`, `ARM_NEON_ASIMD`, CPU part `0xd4f` (Graviton4) on every
rung, as each `run.log` records. Phase B of the sizing work, 2026-09-09, us-east-1. This is the data
behind [How many cores should I ask for?](../../patterns/sizing.md).

## The core ladder — `results/knee/`

ntomp = 4 and ranks = cores ÷ 4 throughout, one `c8g` box sized to the core count (`Running on 1
node with total N cores`). Price is c8g on-demand at $0.03988/vCPU-hr. The artifacts carry no price.

| cores | ranks | ns/day | step efficiency | ns/day per $/hr |
|---|---|---|---|---|
| 8 | 2 | 12.995 | — | 40.7 |
| 16 | 4 | 24.660 | 95% | 38.6 |
| 32 | 8 | 47.189 | 96% | 37.0 |
| 48 | 12 | 66.084 | 93% | 34.5 |
| 64 | 16 | 82.763 | 94% | 32.4 |
| 96 | 24 | 124.114 | 100% | 32.4 |
| **192** | **48** | **147.669** | **59%** | **19.3** |

Step efficiency is the speedup over the previous rung divided by the core ratio. Two things change
at 192, and this measurement cannot separate them. The 192-vCPU box reports **two NUMA nodes**
(`node0` 0–95, `node1` 96–191, `knee/c192/topo.txt`), and atoms per rank halve from 3,406 to 1,703.
Every rung's `mpi_reported` (or, at 192, `Using 48 MPI processes` in `run.log`) equals the ranks launched.

## Decomposition at 64 cores — `results/decomp64/`

| ranks × threads | 64 × 1 | **32 × 2** | 16 × 4 | 8 × 8 | 4 × 16 |
|---|---|---|---|---|---|
| ns/day | 81.623 | **90.317** | 78.345 | 77.093 | 72.089 |

Best ÷ worst is 1.253, and 32 × 2 beats 8 × 8 by 17.2%. The 16 × 4 cell is the same configuration
as the knee's 64-core rung, run separately: 78.345 here against 82.763 there, a 5.6% spread between two
samples of one setting, so differences of that size between cells are not resolved. The 64-vCPU box is **one NUMA node**
(`decomp64/*/topo.txt`: `NUMA node(s): 1`, 0–63), so the spread is not NUMA. Each `timing.txt`
carries GROMACS' own DD grid, separate-PME-rank count and load imbalance.

## The launch, at 8 cores — `results/launch-default/` vs `results/launch-bound/`

| ranks × threads | 1 × 8 | 2 × 4 | 4 × 2 | 8 × 1 |
|---|---|---|---|---|
| first sweep (`launch-default`) | **1.891** | 3.686 | 7.132 | 13.469 |
| re-run (`launch-bound`) | **13.503** | 12.929 | 13.168 | 13.486 |

The `mdrun` command lines are identical between the two, and both report 800% core in GROMACS'
own accounting. What changed was the `mpirun` invocation, which is not in the logs. The session
record says the first sweep used the default `mpirun -np R` binding and the re-run used `--map-by
slot:PE=T --bind-to core`. The specs were not kept, so that attribution is the session's account,
not something these artifacts prove. What they do prove is the size of the effect: 1 × 8 ran
7.14× slower in the first sweep, and the four layouts of the re-run fall within 4.4% of each other.

## Size — `results/size-small-20k/`, `results/size-benchRIB/`

Same launch, same ladder. `benchSMALL.tpr` has 20,028 atoms (its provenance was not recorded).

| cores | 8 | 16 | 32 | 48 | 64 | 96 | 192 |
|---|---|---|---|---|---|---|---|
| small, ns/day | 38.859 | 74.680 | 137.678 | 192.628 | 239.070 | 330.852 | 495.097 |
| efficiency vs 8 | 100% | 96% | 89% | 83% | 77% | 71% | 53% |

**benchRIB (2M atoms) produced no result at any core count.** Each `run.log` stops after the
citation banner, before the tpr is read, so `ns_per_day=NA` on all seven rungs. The cause was
not established. A truncated download and a setup hang are both open.

## What is pinned, and what was lost

| | |
|---|---|
| benchMEM.tpr | sha256 `5099268bf3a3…`, the [gromacs recipe](../../recipes/gromacs/README.md)'s pin (S3 copy re-hashed 2026-10-24) |
| benchSMALL.tpr | sha256 `9a0317e8a99d0acb6a2df77a585c2813bd421d31297b2bbf1efae1917c8a8cdf` (S3 copy) |
| benchRIB.tpr | 57,476,346 bytes, S3 ETag `f3e3e13573a1fac14577bd64f2c287fb-7`; not re-hashed |
| image | not recorded in the logs. The recipe's pin on the run date was `quay.io/aarchsci/md@sha256:1ee941664add…` |
| specs | **not kept**. The session wrote them to `/tmp`. Each `run.log` holds the `mdrun` command line |

Artifacts copied verbatim from `s3://scicookbook-942542972736-us-east-1/runs/{gromacs-knee,
gromacs-dc64,gromacs-sweep,gromacs-sweep2,gromacs-size}/`. MD at this size is a path, not a fixed
point, so no energy is asserted. These are throughput numbers from a `-maxh`-bounded run, not
compute cost ([layout](../../patterns/layout-and-effective-cost.md)).
