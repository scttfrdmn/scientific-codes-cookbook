# PLUMED on a real workload: a whole RODA peptide library through `plumed driver`, Graviton4, Intel and AMD

> **16,000 published sander trajectories (3.2 M frames, 174 M collective-variable values) cost $0.16
> of compute on Graviton4, $0.19 on Intel and $0.21 on AMD, in about 15 minutes on each.** Every CV
> matches an independent numpy evaluation of the published bytes to 5e-11 and cpptraj to its own
> formula's precision. Every SHAKE-constrained X–H distance PLUMED reads equals the topology's r0
> within SHAKE's tolerance. The x86 COLVARs are identical on Intel and AMD. Against Graviton4 they
> differ in 175 files, each by one unit in the tenth decimal. A serial `plumed driver` from the
> MPI build spends 1.06 s per call in `MPI_Init`; `plumed --no-mpi` drops it and halves the job.

Workload: every Ala-capped pentapeptide **Ala-X-Y-Z-Ala** in RODA's
[Conformational Space of Short Peptides](https://registry.opendata.aws/short_peptides/)
(`s3://toyokounqpeptides/5pep/`, us-west-2). That is all 8,000 central tripeptides over the 20
amino acids, replicas R2 and R3, the two that ship their own tleap topology (`p.1.top`; R1 packages
have none). Each one is 2 ns of sander Langevin dynamics in vacuum at 303 K with SHAKE on bonds to
H, saved every 10 ps as 200 Amber NetCDF frames, plus its `.out` and final restart.

## Run it

```sh
B=$(make -s print-bucket)
bash measurements/plumed-real/stage-inputs.sh "$B"     # fetches the cohort ON AWS and pins it
spawn task run --wait --spec "$(python3 scripts/spec_arch.py plumed arm64 $B r1 measurements/plumed-real/real.task.json)"
aws s3 cp s3://$B/runs/plumed-real/graviton4-nompi/colvars.tar.gz s3://$B/inputs/plumed-real/graviton4-colvars.tar.gz
for a in x86_64 amd; do                                 # serially; each compares itself with Graviton4
  spawn task run --wait --spec "$(python3 scripts/spec_arch.py plumed $a $B r1 measurements/plumed-real/real-xchip.task.json)"
done
```

Per trajectory, `real.py` writes the PLUMED input from the trajectory's own topology. It has
backbone φ/ψ/ω, an N–Cα–C–Cβ chirality torsion per non-Gly residue, the heavy-atom Rg, the
end-to-end distance, and one `DISTANCE` per SHAKE-constrained bond. cpptraj converts the NetCDF to
DCD (PLUMED's molfile reader has no NetCDF) and computes the same torsions, Rg and distance
independently. Then:

```sh
plumed --no-mpi driver --mf_dcd x.dcd --plumed plumed.dat --timestep 10
```

16 workers, one trajectory each, one thread per process.

## Results

16 vCPU, 32 GiB (`*.4xlarge`), the same 3.17 GB bundle, PLUMED 2.9.2 and cpptraj V7.6.2 from the
aarch.science `md` image on Graviton4 and its linux-64 lock twin on both x86 chips. A *result* is
the whole cohort: 16,000 trajectories through PLUMED, cpptraj and every check below.

| chip | instance | tool wall | billed | peak mem (worker / container) | $/hr | compute $/result | billed $/result |
|---|---|---|---|---|---|---|---|
| **Graviton4** | `c8g.4xlarge` | **891 s** | 1085 s | 0.22 / 1.24 GiB | 0.6381 | **0.158** | **0.192** |
| Xeon 6975P-C | `c8i.4xlarge` | 912 s | 983 s | 0.31 / 6.80 GiB | 0.7497 | 0.190 | 0.205 |
| EPYC 9R45 | `c8a.4xlarge` | 879 s | 937 s | 0.31 / 6.80 GiB | 0.8622 | 0.211 | 0.224 |

- **The three chips finish within 4% of each other, so the hourly price decides.** Graviton4 is 17%
  cheaper per result than Intel and 25% cheaper than AMD in compute dollars. Billed, it is 6% and
  14% cheaper, because its window outside the tool is longer: 194 s against 71 s (Intel) and 58 s
  (AMD). Intel's 16 vCPUs are 8 cores × 2 threads; Graviton4's and AMD's are 16 cores (`lscpu`,
  `results/*/host.txt`).
- **This is a process-start workload, and that is the real shape of driver post-processing.**
  For a two-CV input, PLUMED's own timer reports 2 ms for 200 frames (the first probe). The
  `plumed driver` process costs 0.41–0.43 s of CPU per trajectory on every chip. A batch of many
  short trajectories is paid for in process starts, so fewer, longer driver calls are the lever,
  not a faster chip.
- **`plumed --no-mpi` halved the job.** conda-forge's PLUMED is the `mpi_openmpi` build, and its
  `MPI_Init` alone holds a serial `driver` for **1.06 s of wall per call** (1.468 s → 0.411 s,
  identical COLVAR, `results/startup/startup.txt`). That time is not CPU (0.467 s → 0.411 s), so it
  hides as idle cores. The first pass over the cohort, without the flag, took 1982 / 2018 / 1956 s
  (`results/with-mpi/`) and produced byte-identical COLVARs on every chip.
- **Memory is not the constraint.** Each worker peaks at 0.22–0.31 GiB (`ru_maxrss`). The
  container figure is cgroup `memory.peak`, which on x86 also counts the env twin installed at
  start. tmpfs holds the 3.17 GB bundle plus a 0.75 GB COLVAR archive (another 0.75 GB of
  Graviton4's on x86).
- Prices are on-demand us-west-2: spawn's `cost_estimate` divided by the billed window. n = 1 per
  chip. The computation is a fixed function of fixed bytes, and the chips agree to the printed
  digit (below), so a repeat would measure only timing noise.

## What held at scale, on all three chips

| claim | why it must hold | observed (each chip) |
|---|---|---|
| frames conserved | each trajectory is 200 frames at `ntwx = 5000` of `nstlim = 10^6` | 3,200,000 NetCDF frames = 3,200,000 COLVAR rows = 3,200,000 sander energy records past step 0, at steps 5000·k exactly |
| the published runs completed | sander writes `Final Performance Info` only at a clean end | 16,000 / 16,000 |
| the trajectory is the run that ended | the restart (double) is written at the last frame's step | `float32(restart) == last frame`, bit for bit, 16,000 / 16,000 |
| PLUMED read the published bytes | the DCD is a lossless float32 copy | DCD == NetCDF exactly, 16,000 / 16,000 |
| PLUMED == an independent evaluation | numpy computes every CV from the NetCDF in float64 (atan2 torsions) | max **5.0e-11** rad / nm over 174 M values: the 1e-10 print unit |
| PLUMED == cpptraj | a second MD-analysis code on the same bytes (see below for the bound) | **5.0e-11** wherever \|sin φ\| ≥ 0.01; everywhere within its acos error |
| every X–H bond is the force field's r0 | `ntc = 2` SHAKE at `tol = 1e-5` holds them | max \|r − r0\|/r0 **6.72e-6** over 113,760,000 constraint-frames; bound 1.27e-5 (tol + float32 storage) |
| no residue changes handedness | a classical force field cannot invert an L-Cα | 15,520,000 chirality torsions, all negative, min \|value\| 1.766 rad |
| the computation is deterministic | a fixed program on fixed bytes | 50 / 50 COLVARs byte-identical on a second pass |

**The cpptraj bound comes from its formula.** cpptraj's `Torsion()` (`TorsionRoutines.cpp`) returns
`acos(cos φ)` with the sign from a triple product. Its forward error is ~kε/\|sin φ\|, which grows
without bound at φ → 0 or π, a trans ω. PLUMED and numpy use `atan2`, which is well-conditioned
everywhere. A flat 1e-9 fails on exactly one trajectory, at 4.49e-9, where φ = −3.0e-8 rad (a cis
peptide bond). So the check asserts 1e-9 plus 32ε/\|sin φ\|, capped at acos's worst case √(64ε),
and reports the well-conditioned maximum separately so the split stays visible.
Where \|sin φ\| ≥ 0.01 the extra term is under 1e-12, and the agreement there is 5.0e-11.
Normalised, max (Δ − 1e-10)·\|sin φ\| is **1.4e-16 to 2.0e-16, about one ε**, on every chip. The
largest raw differences (4.5e-9 on Graviton4, 6.9e-9 on x86) all sit at φ ≈ 0 or ±π. This is the
[like-with-like rule](../../practices/cross-checks.md): the two codes agree; the metric had to
allow for one of them using acos.

## What differed across chips

**Nothing past the printed digit.** These are fixed points: deterministic functions of identical
input bytes.

- **Intel vs Graviton4:** 15,825 of 16,000 COLVARs byte-identical. In the other 175, the largest
  difference is **1.00e-10**, exactly one unit of the `%.10f` format. That is consistent with a
  last-bit difference between the arm64 and x86 builds landing on a rounding boundary of the
  printed value.
- **AMD vs Graviton4:** identical to the Intel result. **The Intel and AMD COLVARs are
  byte-identical to each other** for all 16,000 trajectories: same linux-64 binaries.
- Every assertion above passed unchanged on all three. These CVs make no BLAS calls, so the
  [OpenBLAS kernel question](../../patterns/cross-architecture.md) does not arise.

## Why this size

A driver campaign over a peptide library is how PLUMED gets used for analysis. Researchers compute
the same CVs over every trajectory a study produced, to build Ramachandran maps, propensity scales
or training sets. The size here is the whole of one complete, natural slice of the published
library: all 8,000 sequences of one family, every replica that carries a topology. The full RODA
library (3.2 M pentapeptides) is this run repeated with a different sequence filter. The cost
scales linearly in trajectories, at **about $0.010 of Graviton4 compute per 1,000**.

## The dataset's replicas are not independent samples

Every one of the 16,000 `.out` files records the same Langevin seed, `ig = 71277`. For **5,596 of
the 8,000 sequences, R2 and R3 are byte-identical trajectories**, so the cohort holds 10,404
distinct trajectories, not 16,000. One sequence checked by hand (Leu-Ser-Cys) has R1, R2 and R3
all identical. Where R2 and R3 differ, they still share the seed. Their `.out` headers record
different run dates and working directories, so they were separate runs. Treat a replica label in this dataset
as "maybe the same run", and deduplicate by trajectory hash before counting samples. The
Ramachandran observations below are over the distinct trajectories. Identical trajectories are
harmless to every check above, since each is still a published file with a correct answer.

## Observations (paths, reported, not asserted)

These come from the trajectories, which are sampled paths. They are a property of the dataset and
need no cross-chip agreement, though they matched on all three anyway.

- Central-residue φ/ψ over the distinct trajectories (α_R: φ < 0, −120° < ψ < 50°; α_L: φ > 0).
  **Gly** α_L 0.614. **Pro** α_L 0.002. **Lys / Arg** have the lowest α_R (0.176 / 0.201). Ala,
  Leu and Glu have the highest (0.68–0.69). Full table: `results/*/summary.json` (`rama`).
- **191,096 of 12,800,000 ω-frames (1.5%) are cis** (\|ω\| < 90°). 72,753 of them are X-Pro bonds.

## Caveats

- In vacuum, a cutoff of 999 Å, and 2 ns per run: the dataset's protocol, not a solvated
  production. That changes the science but not what PLUMED is asked to compute.
- The bundle stores RODA's untouched `.tar.bz2` objects. Each one's MD5 equals the ETag RODA serves
  (single-part uploads), and the bundle and its per-object manifest are pinned by sha256 in
  `inputs.sha256`.
- The cohort is fetched by `00-fetch.task.json` (55 s for 16,000 objects in-region). RODA lists the
  dataset as "not updated", and the bundle in the bucket is the durable copy.

## Files

`fetch.py` / `00-fetch.task.json` build and pin the cohort. `real.py` is the per-trajectory
pipeline and every check. `run.sh` is the task wrapper. `real.task.json` is Graviton4.
`real-xchip.task.json` is the x86 legs, which also stage Graviton4's COLVARs and compare them file
by file. `probe.task.json` is the 320-trajectory sizing probe. `startup.task.json` is the
`--no-mpi` measurement. `results/<chip>/` holds `check.txt`, `host.txt` and `summary.json`.
`per_traj.tsv` and `colvars.tar.gz` stay in the bucket under `runs*/plumed-real/`.
