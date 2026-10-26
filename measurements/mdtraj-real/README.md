# MDTraj on a real workload — the whole Folding@home FAST run of SARS-CoV-2 Mpro, Graviton4, Intel and AMD

> **MDTraj analyses 120,060 frames (2.4 µs) of the main protease, 1.69 billion coordinates, and
> MDAnalysis decodes the same bytes to the same numbers on all three chips.** Every exact check
> holds everywhere, and the decoded coordinates are bit-identical across chips. AMD is fastest
> (784 s) and cheapest, **$0.40 billed per dataset**, with Graviton4 4% behind at $0.42 and Intel
> at $0.76. Shrake-Rupley SASA is 93–95% of the compute; without it the whole dataset is about a
> minute of work.

Workload: the [Folding@home COVID-19 cryptic-pockets dataset](https://registry.opendata.aws/foldingathome-covid19/)
(RODA, `s3://fah-public-data-covid19-cryptic-pockets`): the Bowman lab's Mpro monomer set,
released with Zimmerman et al., *Nat. Chem.* 13, 651 (2021). This is all of `SARS-CoV-2/nsp5_monomer/FAST_simulations/`: 6 FAST
generations × 10 trajectories of 40 ns each, 2,001 frames at 20 ps, 4,682 protein atoms (306
residues, hydrogens included) in a rhombic-dodecahedron box. That is 2.14 GB of XTC, read against
the dataset's own `input_files/prot_masses.pdb`.

Each of the 60 files goes to one worker process, which does what an analysis of a trajectory set
does with MDTraj. It loads the file, superposes it on the start structure and takes the CA RMSD,
then computes Rg, backbone φ/ψ, DSSP, per-residue Shrake-Rupley SASA (960 points) and all 46,665
periodic CA–CA distances per frame. Finally it writes the trajectory back to XTC. MDAnalysis
decodes the same file in the same process and computes everything it shares a definition for.

## Run it

```sh
B=$(make -s print-bucket)
bash measurements/mdtraj-real/stage-inputs.sh "$B"          # RODA -> your bucket, server-side
for a in arm64 x86_64 amd; do                                # serially
  spawn task run --wait --spec "$(python3 scripts/spec_arch.py mdtraj $a $B r1a measurements/mdtraj-real/real.task.json)"
done
python3 measurements/mdtraj-real/collect.py "$B" r1 r1a r1a r1a
```

Inside each worker, the MDTraj side is just this:

```python
t   = md.load("trj_gen003_kid007.xtc", top="prot_masses.pdb")
ref = md.load("prot_masses.pdb")
ca  = t.topology.select("name CA")
rmsd = md.rmsd(t, ref, 0, atom_indices=ca)
sasa = md.shrake_rupley(t, mode="residue")
ss   = md.compute_dssp(t, simplified=True)
```

## Results

30 worker processes on an `8xlarge` (32 vCPU, 64 GiB) of each chip, one file per worker, so the 60
files go in two rounds. Same MDTraj 1.11.2 and MDAnalysis 2.10.0: the aarch.science `md` image on
arm64, its linux-64 twin on both x86 chips. Same staged bytes.

| chip | instance | tool wall | billed | peak mem | $/hr | compute $/result | billed $/result |
|---|---|---|---|---|---|---|---|
| Graviton4 | `c8g.8xlarge` | 1072 s | 1172 s | 35.4 GiB | 1.2762 | 0.380 | 0.416 |
| Intel (Xeon 6975P-C) | `c8i.8xlarge` | 1759 s | 1817 s | 35.7 GiB | 1.4994 | 0.733 | 0.757 |
| **AMD (EPYC 9R45)** | `c8a.8xlarge` | **784 s** | **832 s** | 38.8 GiB | 1.7243 | **0.376** | **0.399** |

A *result* is the whole dataset: all 60 files through every analysis and cross-check, plus the DHFR
DCD below. *Tool wall* is `real.py`, start to finish. *Billed* is spawn's task window, wrapper start to stage-out done.
*Peak mem* is the container's cgroup `memory.peak`; one worker peaks at 1.26–1.28 GiB (`ru_maxrss`).

- **AMD is 1.37× faster than Graviton4 and 2.24× faster than Intel.** Its hour costs 35% more than
  Graviton4's, so per dataset the two nearly tie: AMD is 1% cheaper in compute and 4% cheaper
  billed. Intel costs 1.9× as much. These are per-vCPU comparisons, because that is what is rented:
  `c8g` and `c8a` vCPUs are full cores (`Thread(s) per core: 1`), while `c8i`'s 32 are 16 cores × 2
  hyperthreads, so 30 workers on Intel share cores pairwise.
- **SASA is the bill.** Shrake-Rupley takes 30,293 of 31,808 worker-seconds on Graviton4 (0.25
  core-s per frame at 4,682 atoms) and 21,578 of 23,094 on AMD (0.18). Decoding, RMSD, dihedrals, DSSP, the distance matrix and the
  round trip together are ~1,500 worker-seconds, about a minute on this box. Size by whether you need
  SASA, then by frames × atoms.
- **Compute-dominated:** 48–100 s of overhead (boot, image pull or the x86 env install, staging
  2.2 GB) against 13–29 minutes of work.
- **Prices** are each run's own `cost_estimate` over its billed window. **n = 1 per chip.** The
  analysis is deterministic on fixed bytes, so a second run computes the same numbers; the timings
  are one measurement each.

## Why this size

A FAST run is a unit of analysis: each generation's ten trajectories are seeded from the previous
generation's analysis, so the run is read as a whole, and this is the whole of one. 120k frames of
a 300-residue protein is ordinary for a trajectory-analysis job. It is enough frames that the
per-frame cost is visible and the per-task overhead is not. The rest of the dataset is the same
shape at a larger N: `PROJ14582` alone holds ~730,000 XTC files. That is a fan-out of this task,
not a bigger box.

## What held at scale, on all three chips

| assertion | bound | Graviton4 | Intel | AMD |
|---|---|---|---|---|
| files; atoms in every XTC == topology == MDAnalysis | 60; one count | 60; 4,682 | same | same |
| frames, MDTraj == MDAnalysis == the round trip, per file | exact | 120,060 | same | same |
| frame times | strictly one 20 ps step | 0–40,000 ps | same | same |
| every decoded value on the XTC grid (precision 1000) | \|1000x − round(1000x)\| < 0.01 | 1.0e-3 | same | same |
| **coordinates, MDTraj vs MDAnalysis** | < 1e-5 nm | **3.8e-7** over 1,686,362,760 | same | same |
| box lengths / angles, two libraries | < 1e-5 nm / 1e-4° | 1.3e-6 / 1.1e-5 (60, 60, 90°) | same | same |
| **XTC round trip**: MDTraj writes, MDTraj and MDAnalysis read back | **exactly 0** | **0** | **0** | **0** |
| CA selection and φ/ψ quadruplets == MDAnalysis's own `phi_selection`/`psi_selection` | identical | 306 CA; 305 / 305 | same | same |
| **CA RMSD², MDTraj (float32 QCP) vs MDAnalysis (float64 QCP)** | < 1e-5 nm² | 2.96e-6 | 3.06e-6 | 3.06e-6 |
| **CA RMSD², MDAnalysis QCP vs an independent float64 Kabsch SVD** | < 1e-9 nm² | **2.1e-14** | same | same |
| CA RMSD² invariant under a random rigid motion of every frame | < 1e-5 nm² | 3.99e-6 | 3.99e-6 | 3.99e-6 |
| unweighted Rg, two libraries; under rigid motion | < 1e-5 rel | 5.5e-9; 1.5e-8 | same | same |
| φ/ψ, MDTraj vs MDAnalysis, wrapped | < 1e-3 rad | 2.38e-5 | 2.38e-5 | 2.38e-5 |
| periodic CA–CA distances (triclinic minimum image), two libraries | < 1e-5 nm | 5.97e-6 | 5.97e-6 | 5.97e-6 |
| per-atom SASA summed == per-residue SASA | < 1e-5 rel | 2.1e-7 | 2.1e-7 | 2.1e-7 |

**Why the bounds are what they are.** Two decoders of the same XTC integers should agree to
float32 at |x| ≤ 11.45 nm, ~1e-6 nm, so 1e-5 says "the same number" against a stored quantum of
1e-3 nm. RMSD is compared as a *square*. MDTraj's float32 QCP cancels terms of ~N|x|² down to
N·RMSD², which leaves ~1e-6 nm², and a square root amplifies that floor without limit as RMSD
goes to zero. The distance bound has the least headroom (1.7×): a triclinic minimum image
subtracts box vectors in float32. It is a property of these bytes, not noise, because every
analysis here is deterministic.

**Match the box and the inputs before comparing** ([cross-checks](../../practices/cross-checks.md)).
The box is a rhombic dodecahedron (60°, 60°, 90°): hand MDAnalysis only its lengths and the
minimum images disagree by **4.5 nm**. The SVD check runs on exactly MDAnalysis's inputs; given
MDTraj's coordinates, one float32 rounding away, it reads 1.9e-8 nm² instead of 2e-14.

**Reported, not asserted.** CA RMSD mean 0.253 nm (max 0.491). Rg 2.218 nm. DSSP H 25.5%,
E 26.8%. Total SASA 149.3 nm². MDAnalysis's DSSP (a pydssp port, over the 305 residues that carry
N, CA, C and O; the C-terminus has OC1/OC2) matches MDTraj's simplified DSSP on 96.8–97.5% of
residue-frames per file. That gap is between two implementations of the method, so it is an
observation.

MDTraj's rewritten XTC is the same size as Folding@home's in all 60 files, and byte-identical in
none. The coordinates survive exactly; the bitstream is not the claim.

### The same DCD as openmm-mdanalysis-real, on identical bytes

[openmm-mdanalysis-real](../openmm-mdanalysis-real/README.md) wrote a 100-frame DHFR DCD (23,558
atoms) on Graviton4. MDAnalysis read it there and found every constrained distance within
**1.54e-05** of its constraint. The same bytes are copied under `inputs/` and pinned by sha256, so
every chip reads that one file. Here:

| assertion | bound | all three chips |
|---|---|---|
| atoms / frames, MDTraj and MDAnalysis | 23,558 / 100 | 23,558 / 100 |
| coordinates, 10 × MDTraj vs MDAnalysis | < 1e-4 Å | 1.14e-5 Å |
| every one of 2,229,000 constrained distances, MDTraj vs MDAnalysis | < 1e-4 Å | 1.57e-5 Å |
| **MDAnalysis's worst \|d−d₀\|/d₀** | **== 1.54e-05, the sibling's printed value** | **1.5416e-05** |
| MDTraj's worst \|d−d₀\|/d₀ | < 1e-4 (the sibling's float32-DCD bound) | 2.3423e-05 |

MDTraj's worst is larger because MDTraj holds nm in float32: reading an Å DCD costs it one more
rounding than MDAnalysis pays (the 1.14e-5 Å above). On a 1 Å bond that moves the worst deviation
by ~1e-5, and the per-distance check accounts for all of it.

## Fixed point or path

Nothing here integrates anything: every number is a deterministic function of fixed bytes. What
differs across chips is the arithmetic. The arm64 and x86 builds are different binaries running
float32 SIMD kernels. Intel and AMD run one binary, and every number they produce is identical.

| | Intel vs Graviton4 | AMD vs Graviton4 | Intel vs AMD |
|---|---|---|---|
| **decoded coordinates and boxes, 60 XTCs + the DCD** | **bit-identical** | **bit-identical** | **bit-identical** |
| unweighted Rg | bit-identical, 60 of 60 | bit-identical | bit-identical |
| CA RMSD | differs on 60 of 60, max 7.4e-6 nm | the same as Intel | bit-identical |
| φ / ψ | differ on 60 of 60 (each chip within 2.4e-5 rad of float64) | the same as Intel | bit-identical |
| periodic distances | identical on 41 of 60 | the same as Intel | bit-identical |
| SASA | differs on 60 of 60, total max 2.5e-3 nm² (1.7e-5 rel) | the same as Intel | bit-identical |
| DSSP | **3 of 36,738,360** residue-frames differ | the same 3 | bit-identical |
| JAC: worst constrained deviation, both libraries | identical | identical | identical |

- **The decode is the fixed point, asserted.** XTC is integers; every chip turns them into the same
  float32 bits, so whatever a reader computes downstream starts from the same coordinates on any
  chip. `collect.py` fails if it does not.
- **The float32 analyses differ in the last bits, which is expected.** RMSD by 7e-6 nm and SASA by
  1.7e-5 relative are below any physical meaning, and each chip passes the same cross-checks
  against MDAnalysis's float64. So the like-for-like claim is the cross-library agreement on each
  chip, not bit-identity across them.
- **DSSP is where last bits become a different answer.** It is discrete (a hydrogen bond is an
  energy below a cutoff), so a last-bit difference can flip an assignment: 3 residue-frames in 36.7
  million, the same 3 on both x86 chips. Across machines, compare secondary-structure *fractions*,
  never per-residue strings.

## Caveats

- OpenBLAS reports `neoversev2` on Graviton4. The x86 twin's numpy links MKL, so there is no
  corename there. Which kernels produce the last-bit differences (MDTraj's C code, numpy's ufuncs)
  was not traced.
- The C-terminal residue has no `O`, so MDAnalysis's DSSP needs the 305-residue selection above.
  Run it on the whole protein and it refuses.

## Pins

| | |
|---|---|
| trajectories + topology | RODA `s3://fah-public-data-covid19-cryptic-pockets/SARS-CoV-2/nsp5_monomer/` `FAST_simulations/` (60 XTCs) and `input_files/` (5 files); copied server-side (ETags equal the source's) and pinned by the sha256s the probe recorded on AWS, `inputs.sha256`, re-checked in every task |
| DHFR DCD | `runs/openmm-mdanalysis-real/r1/traj-JAC.dcd` + `constraints-JAC.npz`, copied to `inputs/mdtraj-real/jac/`, sha256 in `inputs.sha256`; `JAC.prmtop` from the sibling's pinned `Amber20_Benchmark_Suite.tar.gz` (same object) |
| image | arm64 `quay.io/aarchsci/md@sha256:28537712…`, x86 the env's linux-64 twin `envs-x86_64/md-f7ed8789d24e` (the [recipe's](../../recipes/mdtraj/README.md) pins). MDTraj 1.11.2, MDAnalysis 2.10.0, numpy 2.5.4 |
| scripts | `run.sh`, `real.py`, `check.py`, `inputs.sha256`, each pinned by sha256 in the spec (`mkspec.py` writes them) |

`probe.task.json` is the sizing probe: the same analysis on 4 files at 4 workers on `c8g.2xlarge`
(~530 s per file; it ran earlier revisions of `real.py` and `check.py`). It sized the 8xlarge at two rounds of 30. `collect.py` writes
`results/<chip>/` and `results/crosschip.txt`.
