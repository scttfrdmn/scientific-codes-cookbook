# yt over a whole Enzo cosmology run: Graviton4, Intel and AMD

> **yt reduces all 51 outputs of a complete AMR cosmology run (z = 99 to 0), and an independent
> h5py reader agrees with it on every one: leaf cells bit for bit, covering grids elementwise and
> exactly, and every mass total to a few 1e-16. Enzo's own run log agrees on every level's grid
> and cell counts.** The whole run takes 29–50 s on 8 vCPUs. Graviton4 is cheapest per run billed
> ($0.0096), AMD is fastest (29 s), and Intel is slowest (50 s) and 32% dearer. Intel and AMD give
> byte-identical results. Graviton4 differs from them only in last bits, and the cause is traced
> below.

Workload: **every output** of yt's `Enzo_64` sample run, the cosmology simulation yt's own
docs use. It is Enzo with a 64³ root grid, 128 Mpc/h comoving, refinement to level 4,
and star formation. That is 44 data dumps and 7 redshift outputs, 3.97 GB in 1,065 files, growing
from 12 grids / 262,144 cells at z = 99 to 752 grids / 1,070,273 leaf cells and 12,183 star
particles at z ≈ 0. Every output gets the reductions a user runs on a cosmology series: total
quantities, on-axis projections and their 800×800 images, 1-D and 2-D (density–temperature)
profiles, covering grids, particle reads and deposition, and a radial profile round the densest
point.

**Data tier: a durable public id.** `Enzo_64.tar.gz` from yt's sample-data hub, pinned by the
sha256 that **yt 4.4.2's own `yt/sample_data_registry.json`** carries (`2b17fffb…f71bc9`). It is
fetched on AWS and checked there, never on the laptop. Its 1,065 unpacked files are pinned in
[`files.sha256`](files.sha256), and the task re-checks every staged byte against that list.

## Run it

```sh
export AWS_PROFILE=aws B=<your bucket>
bash measurements/yt-real/stage-inputs.sh "$B"       # fetch on AWS, check vs yt's registry, unpack
for a in arm64 x86_64 amd; do
  spawn task run --wait --spec "$(python3 scripts/spec_arch.py yt $a "$B" r1 measurements/yt-real/real.task.json)"
done
python3 measurements/yt-real/collect.py "$B" r1      # results/<chip>/, results/crosschip.txt
```

[`real.py`](real.py) is ordinary yt (`yt.load`, `ds.proj`, `create_profile`, `covering_grid`,
`("deposit", "all_count")`) fanned over a `multiprocessing.Pool` of 8, one output per worker,
`OPENBLAS_NUM_THREADS=1`. Its `Independent` class is the reference: it parses Enzo's
`.hierarchy` text and reads the HDF5 grids with h5py, using no yt code.
[`mkspec.py`](mkspec.py) writes the spec and pins the scripts by sha256.

## Results

8 workers on a `2xlarge` (8 vCPU, 16 GiB) of each chip. The same `astro` env (aarch.science on
arm64, its `astro-ed5afa726bbe` linux-64 twin on both x86 chips) and the same staged bytes:

| chip | instance | tool wall | billed | worker peak RSS | $/hr | compute $/result | billed $/result |
|---|---|---|---|---|---|---|---|
| **Graviton4** | `c8g.2xlarge` | 32 s | 108 s | 0.86 GiB | 0.3190 | **0.0028** | **0.0096** |
| Intel (Xeon 6975P-C) | `c8i.2xlarge` | 50 s | 121 s | 0.86 GiB | 0.3748 | 0.0052 | 0.0126 |
| AMD (EPYC 9R45) | `c8a.2xlarge` | **29 s** | **87 s** | 0.86 GiB | 0.4311 | 0.0035 | 0.0104 |

A *result* is the whole run analysed, all 51 outputs. *Tool wall* is `real.py`, and *billed* is
spawn's task window, wrapper start to stage-out done. Peak RSS is the largest single worker's `ru_maxrss`.

- **Graviton4 is cheapest billed: AMD is 9% dearer and Intel 32% dearer.** AMD is 1.1x faster
  than Graviton4, but its hour costs 35% more, so it is also 24% dearer on compute alone. Intel's 8 vCPUs are 4 cores × 2
  threads (`Thread(s) per core: 2`), while AMD's and Graviton4's are 8 full cores, so the rental
  is like-for-like but a per-core comparison it is not.
- **Boot is most of the bill:** 58–76 s of each window is launch, Docker, image pull and staging
  4 GB. These timings are not compute cost.
- **Per-output cost tracks leaf cells:** 2.2 s at 262,144 cells (z = 99), 6.9 s at 1,070,273
  (z ≈ 0) on Graviton4, 231 s of worker time in all. The box is sized by the staged 4 GB,
  which sits in tmpfs (half the RAM), not by cores. 8 workers × 0.86 GiB fits beside it in 16 GiB.
- n = 1 per chip. The reductions are deterministic: a second run on a fresh instance
  ([`diag.task.json`](diag.task.json), three outputs) reproduced the r1 image hashes bit for bit,
  on Graviton4 and on Intel.

## Why this size

This is a whole simulation, which is the unit yt is pointed at: a `DatasetSeries` over every
output. A single output would drop the series claims below, such as dark matter being the same
particles in every output. It would also drop the early outputs with no refinement and the late
ones with five levels, and those exercise different code paths. By today's standards it is a
small run (64³). yt's per-output work grows with leaf cells, so a production run costs more per
output, not differently. Every identity here is size-independent.

## What held, on all three chips

1,733 checks per chip, all passing.

| claim | why it must hold | worst residual |
|---|---|---|
| grids and cells on every level == Enzo's `OutputLevelInformation.out` for that cycle (50 outputs × 5 levels) | the simulation's own record, written as it ran | exact, 250 of 250 |
| outputs on disk == Enzo's `OutputLog`; time == parameter file == `OutputLog` | the run's record; the parameter file prints 14 digits and the log 16, so the bound is half a unit of the parameter file's last digit | exact / within it, 51 of 51 |
| yt's leaf cells == the reader's leaf cells (its own child masking from the hierarchy), as a multiset of exact values | the same doubles, selected two ways | **bit-identical**, 51 of 51 |
| gas mass: yt `total_quantity` == reader `fsum(ρ·dV)` | same cells | 3.3e-16 |
| on-axis projection, all 3 axes: `Σ σ · (2pdx)(2pdy)` == total mass | the quadtree projection is conservative | 5.1e-16 |
| 1-D density profile and 2-D density–temperature phase: Σ bins == total mass | every cell lands in one bin | 1.5e-15 / 1.7e-15 |
| radial profile round the densest point: Σ bins == sphere mass | same | 2.2e-16 |
| **covering grid at level 2 (256³) == reader's painting, elementwise** | injection of the same doubles, finer over coarser | **exact (`==`)** |
| **covering grid 64³ at level 4 round the densest point == reader's painting** | covers all five levels in 33 outputs | **exact (`==`)** |
| control: the reference transposed / flipped must *not* match | both are shape-preserving on a cube, so they are the wrong answers to reject | 0.17–1.0 away |
| control: a weighted projection comes back as an average (`g/cm**3`) | so the unweighted one is integrating | 51 of 51 |
| particles: count == hierarchy == reader; indices unique; index set and masses == reader | same particles | exact / 1.3e-16 |
| NGP deposit: `Σ all_count` == N, `Σ all_mass` == particle mass | each particle lands in one leaf cell | exact / 1.3e-16 |
| dark matter == 1 − Ω_b/Ω_m (from the run's `64.init`) in every output; gas == Ω_b/Ω_m at the ICs | Enzo's mass unit is the mean matter density × box | 1.3e-16 / 3.3e-16 |
| the dark-matter particle set and mass are the same in all 51 outputs | DM is neither created nor destroyed | 1 index set, spread 0 |

Every compared value is first required to be finite, because a NaN passes every one-sided bound.
The mass bounds are 1e-12, not `==`. yt's unit round trip (code units → cgs → code units) moves
individual cell and particle masses by one ulp from exact code-unit arithmetic, measured in
[`diag.py`](diag.py), so `==` would assert something yt does not promise.

## Across chips

**Intel and AMD are byte-identical to each other** in all 1,733 check values and all 1,737
observations. Against Graviton4, each matches 1,723 / 1,733 check values and 1,623 / 1,737
observations. None of the differences is a path effect: every reduction here is deterministic, and
each difference is in the last bits. [`diag_compare.py`](diag_compare.py) traces both kinds
([`results/diag.txt`](results/diag.txt)):

- **Unit factors on 2 of 51 outputs.** yt derives `code_mass` and `code_density` from the redshift
  and the cosmology, and on DD0016 and DD0034 those factors differ by 1–2 ulp between Graviton4
  and x86. They are arithmetic on `1 + z` and the cosmological parameters, so this is
  floating-point library rounding, not yt's logic; which call rounds differently is not
  established. Everything in physical units on those two outputs then differs
  in its last bit: the gas and total mass, the projection, phase and radial-profile hashes, and
  10 residuals, all still ≤ 1e-15. `code_length`, `code_time` and the other 49 outputs are
  bit-identical.
- **Projection images on every output.** All 102 image hashes differ. On the three outputs
  differenced pixel by pixel, the 800×800 buffers differ on 5–78% of pixels, by at most
  **9.2e-16 relative**. That is a few ulp, so every pixel samples the same quadtree cell on
  both chips. The pixelizer's arithmetic differs in its last bits. Which instruction is
  responsible is not established here.

## What the run found

- **Enzo_64 does not conserve its own mass, and this is not yt's doing.** Gas + stars + dark
  matter starts at exactly 1 (code units), falls to 1 − 1.44e-4 at z = 0.21 and ends at
  1 − 9.7e-5. The loss starts at z = 4, after refinement starts (z = 5) and long before the first
  star forms (z = 1.55). Until then it is entirely in the gas. Star formation runs with feedback
  off. yt agrees with the independent reader to 3e-16 on every
  output, and the root grid carries the same mass as the leaf cells (Enzo projects children onto
  parents; root − leaf ≤ 1.7e-16). So the deficit is in the data Enzo wrote. Reported, not
  asserted: it is the simulation's property.
- **Enzo stores arrays `(z, y, x)`, yt hands them back `(x, y, z)`.** The covering-grid check
  compares against the HDF5 arrays transposed. On a 64³ root grid a transpose is
  shape-preserving, so an axis mix-up would pass any shape check and every conservation total.
  Only the elementwise comparison and its transposed control can catch one, and the control is
  rejected by 0.17–1.0 on every output.

## Pins

| | |
|---|---|
| data | `Enzo_64.tar.gz`, sha256 `2b17fffb6a2e8e471ef85dbdbfa5568f09f7bdb77715c69b40dfe48be8f71bc9` (yt 4.4.2 `sample_data_registry.json`); the 1,065 unpacked files in [`files.sha256`](files.sha256) |
| scripts | `real.py`, `run.sh`: sha256 in [`real.task.json`](real.task.json)'s command, checked before anything runs |
| image | the recipe's `astro@sha256:eaa6cb33…` (arm64) and its `astro-ed5afa726bbe` x86 twin |
