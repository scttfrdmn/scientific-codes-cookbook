# AutoDock Vina on the PoseBusters redocking benchmark — Graviton4, Intel and AMD

> **Vina redocks all 513 PoseBusters complexes on all three chips. Its top pose is within 2 Å of the
> crystal ligand on 49–50 of 85 Astex complexes, where PoseBusters' own Vina run got 49, and on 213–218
> of 428 PoseBusters complexes, where it got 224. Every crystal-pose score is identical on all three
> chips. The docked poses are identical on Intel and AMD and differ on Graviton4.** AMD runs it 1.31×
> faster than Graviton4 at about the same price per result. Intel is 1.35× slower and 57% dearer.

Workload: **every complex** in the PoseBusters paper's two redocking sets
([Buttenschoen, Morris & Deane, *Chem. Sci.* 2024](https://doi.org/10.1039/D3SC04185A)). These are the
85-complex Astex Diverse Set and the 428-complex PoseBusters Benchmark Set, whose 308-complex subset is
the one the journal paper reports. Each ligand is docked from PoseBusters' own RDKit starting conformer
into its deposited protein. The box is a 25 Å cube on the crystal ligand's centroid, the search uses
exhaustiveness 32, and the top-ranked pose is scored by RMSD to the crystal ligand. This is PoseBusters'
Vina protocol from their SI. A redocking benchmark is how a lab checks a docking setup before it trusts
a screen, and these are the sets the docking field currently reports on. The recipe's single 1iep
complex is one of the tutorial's examples.

**Data tier: a durable public id.** Zenodo record [8278563](https://doi.org/10.5281/zenodo.8278563)
is immutable and publishes each file's md5. [`00-fetch.task.json`](00-fetch.task.json) downloads the
archive on AWS and checks it against that md5 (sha256 `495a8f432ee5612c…`, 53.7 MB). The same record's
`posebusters_paper_results.csv` holds PoseBusters' per-complex Vina RMSDs, and the 308 subset is the
authors' [published id list](https://github.com/maabuu/posebusters/files/14516485/posebusters_pdb_ccd_ids.txt),
committed as [`posebusters_308_ids.txt`](posebusters_308_ids.txt). [`pins.sha256`](pins.sha256) pins every file.

## Run it

```sh
export AWS_PROFILE=aws B=<your bucket>
bash measurements/vina-real/stage-inputs.sh "$B"     # fetch + prep on AWS, once, if not staged
for a in arm64 x86_64 amd; do
  spawn task run --spec "$(python3 scripts/spec_arch.py vina $a "$B" r2 \
    measurements/vina-real/real.task.json)" --wait
done
bash measurements/vina-real/collect.sh "$B" r2         # results/<chip>/, results/compare.txt
```

[`prep.py`](prep.py) builds the inputs once, on Graviton4, so all three chips dock the same bytes
(`prep.tar`, sha256 `c5e0c6b4344f1556…`). Each receptor is the deposited protein with waters removed,
hydrogens added at pH 7.4 and written as rigid PDBQT by Open Babel 3.2.1. Each ligand is converted
from PoseBusters' SDF the same way. [`dock.py`](dock.py) then runs 32 single-threaded Vina processes,
one complex each with `cpu=1, seed=42`, longest first:

```python
v = Vina(sf_name="vina", cpu=1, seed=42); v.set_receptor("rec.pdbqt"); v.set_ligand_from_file("start.pdbqt")
v.compute_vina_maps(center=centroid_of_crystal_ligand, box_size=[25, 25, 25])
v.dock(exhaustiveness=32, n_poses=9)   # top pose -> symmetry-corrected heavy-atom RMSD to the crystal ligand
```

## Results

32 workers on `c8g.8xlarge`, `c8i.8xlarge` and `c8a.8xlarge` (32 vCPU, 64 GiB). Graviton4 runs the
recipe's image and the two x86 chips run the `comp-chem` env's linux-64 twin:

| chip | instance | tool wall | billed | peak RSS | $/hr | compute $/result | billed $/result |
|---|---|---|---|---|---|---|---|
| Graviton4 | `c8g.8xlarge` | 3,609 s | 3,685 s | 2.12 GiB | 1.2762 | 1.279 | 1.306 |
| Xeon 6975P-C | `c8i.8xlarge` | 4,857 s | 4,910 s | 2.12 GiB | 1.4994 | 2.023 | 2.045 |
| **EPYC 9R45** | `c8a.8xlarge` | **2,754 s** | **2,801 s** | 2.12 GiB | 1.7243 | 1.319 | 1.342 |

A result is the whole benchmark: 513 docks and every check below. "Billed" is spawn's completion
window, and $/hr is spawn's `cost_estimate` over that window. Peak RSS is the largest single docking
process (`ru_maxrss`). The box's cgroup peak was 27–30 GiB, most of it page cache.

- **The wall is one ligand.** The run takes as long as its longest dock, 7PK0_BYC (56 heavy atoms, 28
  torsions), which took 3,596 s of Graviton4's 3,609. The median dock took 101 s, and the 5% largest
  ligands account for 38% of the CPU time. A box with more cores would not finish sooner, and giving
  that ligand more threads would change its search path.
- **AMD and Graviton4 cost the same per result**, 1.342 against 1.306 billed. AMD is 1.31× faster at a
  35% dearer hour.
- **Intel's 32 vCPU are 16 cores × 2 threads.** Graviton4's and AMD's are 32 full cores. Intel ran the
  32 workers on half the cores. It is 1.35× slower than Graviton4 and 57% dearer per result billed.
- **A different path is not a different amount of work.** Per complex, Graviton4's dock time is
  1.22–1.50× AMD's (5th–95th percentile). Intel, on the identical path, is 1.76–2.07×. The spread is
  about the same, so the timings compare like with like even though Graviton4's poses differ.
- n = 1 per chip. Each run re-docked 10 complexes in fresh processes and got the same bits, and two
  separate x86 boxes produced identical output. Graviton4's own reproducibility across boxes rests on
  the recipe's three-instance check.

## What held at scale, on all three chips

[`dock.py`](dock.py) asserts these inside the task, so a failure fails the run:

| claim | why it must hold | Graviton4 | Intel | AMD |
|---|---|---|---|---|
| all 513 complexes dock and return ranked poses | completion | 513 | 513 | 513 |
| crystal ligand → PDBQT → RMSD to the crystal SDF is 0 | proves the atom map and the RMSD code | 0.00000 Å | 0.00000 Å | 0.00000 Å |
| starting conformer → PDBQT → RMSD is 0 (< 0.002 Å; PDBQT keeps 3 decimals) | same, on the docked ligand | 0.00062 Å | 0.00062 Å | 0.00062 Å |
| 10 complexes re-docked in fresh processes give identical poses | `seed` and `cpu` pin the search | 10/10 | 10/10 | 10/10 |
| top-1 ≤ 2 Å on each set ≥ 25% | a floor: wrong maps or a misplaced box land near 0 | 58.8 / 49.8 / 56.8% | 57.6 / 50.9 / 57.5% | 57.6 / 50.9 / 57.5% |

**Against PoseBusters' own Vina run** (reported, not asserted: their receptors were protonated with
`reduce` and their ligands prepared with Meeko, ours with Open Babel):

| set | PoseBusters' Vina | Graviton4 | Intel / AMD | any of 9 poses ≤ 2 Å (G4 · x86) |
|---|---|---|---|---|
| Astex Diverse, 85 | 49 (57.6%) | 50 (58.8%) | 49 (57.6%) | 70 · 68 |
| PoseBusters, 308 | 184 (59.7%) | 175 (56.8%) | 177 (57.5%) | 254 · 246 |
| PoseBusters, 428 | 224 (52.3%) | 213 (49.8%) | 218 (50.9%) | 317 · 310 |

- **The crystal pose sits in a Vina minimum.** Locally optimised, it stays within 2 Å of itself on all
  513. Of the 210–215 PoseBusters-428 misses, 124–128 have a top pose that Vina scores at or below the
  optimised crystal pose. Those are scoring failures. The other 86–87 score above it: the search,
  starting from a conformer whose rings Vina cannot flex, did not reach the crystal basin.
- **Rescoring the written top pose** reproduces its docked energy to within 0.009–0.012 kcal/mol.
  The written pose's coordinates are rounded to 3 decimals. The optimised crystal pose scores above
  the unoptimised one on 7 complexes, so a reported score that rises under `optimize()` is not, by
  itself, a defect.

## Across chips

[`compare.py`](compare.py) checks the fixed point and reports the rest. Output is in
[`results/compare.txt`](results/compare.txt).

| observable | kind | Intel vs Graviton4 | AMD vs Graviton4 | Intel vs AMD |
|---|---|---|---|---|
| **crystal-pose score** | fixed point, **asserted** | **513/513 identical** | **513/513 identical** | 513/513 |
| crystal pose, locally optimised | short minimisation, reported | 512/513 (7MSR_DCA: 0.008 kcal/mol) | 512/513 | 513/513 |
| docked top energy | path, reported | 4/513 identical, mean +0.010 | same | **513/513 identical** |
| docked poses | path, reported | 0/513 byte-identical | 0/513 | **513/513** |
| top-1 ≤ 2 Å verdict | path, reported | differs on 24 | differs on 24 | 0 |

- **The scoring function is chip-independent. The search path depends on the build, not the vendor.**
  Every crystal-pose score agrees to Vina's reported 0.001 kcal/mol on all three chips. Intel and AMD
  run one linux-64 binary and agree on every pose, byte for byte. Graviton4's aarch.science build takes
  a different Monte Carlo path from the same seed. The recipe's 1iep check fails on x86 for the same
  reason (−13.286 against −13.207).
- **The path difference washes out in aggregate.** Success rates move by 0–5 complexes per set, in both
  directions, and 24 of 513 individual verdicts flip. A seeded docking result is reproducible on one build. It is not a
  cross-platform reference value.
- OpenBLAS is not on this path, because Vina's scoring and search are its own C++, so no kernel name
  is recorded.

## Caveats

- **Receptor preparation is not reproducible run to run, so `prep.tar` is pinned.** Running `prep.py`
  twice on one box gave different bytes for all 513 receptor files and none of the ligand files. In
  the files inspected, the only differences were the coordinates of some ARG and HIS polar hydrogens
  placed by Open Babel. Between two preparations, on the 502 complexes whose box and atoms were
  otherwise unchanged, that moved 27 crystal-pose scores (by up to 0.19 kcal/mol) and 201–204 docked
  top energies, on both Graviton4 and Intel. Treat a re-prepared tar as a new input.
- Eleven complexes needed an exception, listed in [`results/prep/prep_notes.tsv`](results/prep/prep_notes.tsv).
  Five receptors carry an element Vina has no type for (B, Mo, V, Xe), and those atoms are dropped. Six
  ligands do not fit a 25 Å box about their own centroid, so their box is widened to 26–30 Å.
- `dock.py`'s docstring calls the optimised crystal pose a fixed point. 7MSR_DCA shows it is not one at
  Vina's reported precision, so `compare.py` reports it rather than asserting it. The docstring is
  left as run, because the script is pinned.
- One protocol: exhaustiveness 32, rigid receptor, Vina scoring. Vinardo, AD4 maps, flexible side chains
  and a virtual screen were not run.
