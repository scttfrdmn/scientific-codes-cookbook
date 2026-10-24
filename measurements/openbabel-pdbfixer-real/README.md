# Open Babel on all of ChEMBL 37, PDBFixer on a PDB cohort — Graviton4, Intel and AMD

> **Open Babel converts all 2,897,819 ChEMBL 37 compounds through a molfile and back without losing
> an atom, a bond, a charge or a hydrogen. It agrees with RDKit's formula and net charge on every
> compound both read. The 2.9M-row output is byte-identical on all three chips.** But a
> molfile written the way `obabel -ismi -osdf` writes it reads back with no InChI stereo for
> 1,076,033 compounds (37%).
> **PDBFixer prepares 240 of 247 deposited PDB entries without moving a deposited atom, and Amber14
> parametrises 230 of them. Every count matches across chips.** On ChEMBL, AMD is fastest and
> costs the same per result as Graviton4. On the PDB cohort Graviton4 is fastest and cheapest.

Two workloads, the two jobs the recipe's tools do at archive scale:

- **Open Babel: every compound in ChEMBL 37** (`chembl_37_chemreps.txt.gz`, 2,897,819 rows), the
  same staged bytes [rdkit-real](../rdkit-real/README.md) read. Each SMILES goes to InChI,
  InChIKey and canonical SMILES. It is written as an MDL molfile the way `obabel -ismi -osdf` writes
  it, read back, and compared. ChEMBL's own InChI is also read and written back. Whole-library
  conversion is how Open Babel meets a compound collection. The recipe's 100,000-row slice is the
  first 3.5% of this file.
- **PDBFixer: every mmCIF in hash directory `ab`** of the RCSB's 2026-01-01 snapshot on RODA. That
  is 247 entries (200 X-ray, 32 cryo-EM, 15 NMR; 1ab0 to 9abp), prepared for MD the way PDBFixer's manual
  does it. The steps are missing residues (chain-terminal ones dropped), non-standard residues
  replaced, heterogens removed (water kept), missing atoms and hydrogens added at pH 7, then Amber14 +
  TIP3P-FB. This is the cohort [gemmi-real](../gemmi-real/README.md) read and
  [cctbx-real](../cctbx-real/README.md) rescored: 181 of these files are byte-identical to
  cctbx-real's. An ID-hash slice is not chosen by property. Up to 261,604 atoms prepared per entry,
  3.87M in all.

## Run it

```sh
export AWS_PROFILE=aws B=<your bucket>
bash measurements/openbabel-pdbfixer-real/stage-inputs.sh "$B"    # copies the cohort from RODA, checks cohort.sha256
for a in arm64 x86_64 amd; do
  for s in obabel pdbfixer; do
    spawn task run --wait --spec "$(python3 scripts/spec_arch.py openbabel-pdbfixer $a "$B" "$(date +%s)" \
      measurements/openbabel-pdbfixer-real/$s.task.json)"
  done
done
bash measurements/openbabel-pdbfixer-real/collect.sh "$B" <obabel nonce> <pdbfixer nonce>   # results/, compare.txt
```

[`ob_real.py`](ob_real.py) and [`fix_real.py`](fix_real.py) are the workloads and their checks.
Graviton4 runs the recipe's `comp-chem` image. The x86 chips run its linux-64 env twin (same
Open Babel 3.2.1, PDBFixer 1.12, OpenMM 8.6.1).

## Results

**Open Babel, whole ChEMBL 37.** 8 workers on `c8g/c8i/c8a.2xlarge` (8 vCPU, 16 GiB), rdkit-real's
box and worker count:

| chip | instance | cores × threads | tool wall | billed | peak RSS (worker / box) | $/hr | compute $/result | billed $/result |
|---|---|---|---|---|---|---|---|---|
| Graviton4 | `c8g.2xlarge` | 8 × 1 | 900 s | 988 s | 1.35 / 11.2 GiB | 0.3190 | 0.0798 | **0.0876** |
| Xeon 6975P-C | `c8i.2xlarge` | 4 × 2 | 1135 s | 1205 s | 1.36 / 12.9 GiB | 0.3748 | 0.1182 | 0.1255 |
| **EPYC 9R45** | `c8a.2xlarge` | 8 × 1 | **672 s** | **730 s** | 1.36 / 12.8 GiB | 0.4311 | 0.0805 | **0.0874** |

**PDBFixer, 247 entries.** 16 workers on `m8g/m8i/m8a.4xlarge` (16 vCPU, 64 GiB), one process per
entry under a 16 GiB address-space limit. The nine entries over 1 MiB run two at a time:

| chip | instance | cores × threads | tool wall | billed | summed entry time | peak RSS (entry / box) | $/hr | compute $/result | billed $/result |
|---|---|---|---|---|---|---|---|---|---|
| **Graviton4** | `m8g.4xlarge` | 16 × 1 | **1569 s** | **1660 s** | 16,586 s | 1.93 / 7.1 GiB | 0.7181 | **0.313** | **0.331** |
| Xeon 6975P-C | `m8i.4xlarge` | 8 × 2 | 3630 s | 3683 s | 40,802 s | 1.94 / 8.9 GiB | 0.8467 | 0.854 | 0.866 |
| EPYC 9R45 | `m8a.4xlarge` | 16 × 1 | 1826 s | 1869 s | 19,505 s | 1.94 / 8.8 GiB | 0.9738 | 0.494 | 0.506 |

A *result* is the whole workload with every check passing: 2.9M compounds, or 247 entries
classified and 240 prepared. *Tool wall* is the script, start to exit. *Billed* is spawn's completion
window. *$/hr* is spawn's cost estimate over that window. *Worker / entry* RSS is the largest
process's `ru_maxrss`. *Box* is the cgroup's `memory.peak`, which on x86 also counts the env install.

- **ChEMBL: AMD is 1.34× faster than Graviton4 at the same price per result** (0.0874 against
  0.0876). Intel's 8 vCPU are 4 cores, so it is 1.26× slower and 43% dearer. This is the same order
  rdkit-real found for RDKit on the same box (367 / 483 / 678 s). Open Babel takes 1.9× RDKit's
  time here because it does more per compound: three InChIs and a molfile round trip each.
- **PDB cohort: Graviton4 is fastest and cheapest.** AMD is 1.16× slower and 53% dearer. Intel is 2.3×
  slower: its 16 vCPU are 8 cores, and summed per-entry time is 2.46× Graviton4's. The work is
  OpenMM's CPU minimiser rebuilding missing atoms. But the minimiser's path differs by chip (below), so
  the per-chip timings include different iteration counts as well as different cores.
- **The PDB wall is set by a handful of entries.** 85 entries have internal gaps rebuilt (2,340
  residues). Rebuilding costs ~5–10 s per residue, so 6abh (114 residues) and 7abz (a
  261,604-atom ribosome complex) take 17–19 min each on Graviton4 while the median entry takes
  seconds. The 16 workers average 10.6 busy cores.
- **Memory is per process, not per box.** One Open Babel worker grows to 1.35 GiB over ~360,000
  compounds on every chip. A 10,000-compound probe peaked at ~95 MiB, so growth tracks compounds
  per process (cause not isolated). Size a long batch by compounds per worker. The largest PDBFixer
  entry peaked at 1.94 GiB.
- **n = 1 per chip.** Open Babel's table is byte-identical across chips and across two Graviton4
  runs. PDBFixer's counts are identical across chips. Its written coordinates are not (below),
  so a repeat would measure only timing. These timings are not compute cost in general
  ([layout](../../patterns/layout-and-effective-cost.md)).

## What held at scale, on all three chips

Asserted inside each run. A failure fails the task.

| Open Babel claim | why it must hold | result (every chip) |
|---|---|---|
| one row out per row in, unique ids, every key a well-formed InChIKey | the release | 2,897,819 |
| **molfile round trip keeps atoms, bonds, elements, isotopes, formal charges, bond orders and every hydrogen** | a molfile stores each explicitly | **2,897,819 / 2,897,819** |
| molfile round trip keeps the InChI connectivity (stereo layers aside) | the graph survived | 2,897,819 / 2,897,819 |
| re-canonicalising never changes the molecule | canonicalisation | holds; 1 compound's string is not idempotent (below) |
| RDKit's input is rdkit-real's verified output | the cross-check reads real bytes | sha256 `9a84f0ad…` |
| first 100,000 rows reproduce the recipe: 100,000 parsed, 99,846 / 99,930 vs ChEMBL, 99,926 / 99,928 vs RDKit | the recipe's published values | yes |

Reported, not asserted (identical on all three chips):

| observable | count | of |
|---|---|---|
| **element counts (with H) and net charge == RDKit's formula** | **2,897,804** | 2,897,804 both parsed |
| full InChIKey == RDKit's / == ChEMBL's | 2,897,029 / 2,893,572 | both parsed / 2,897,819 |
| connectivity block == RDKit's / == ChEMBL's | 2,897,204 / 2,897,203 | |
| ChEMBL's InChI read and written back unchanged, and keyed to ChEMBL's key | 2,897,809 | 2,897,809 parsed |
| **InChI after the molfile round trip has lost every stereo layer** | **1,076,033** | 2,897,819 |

**All 600 connectivity disagreements with RDKit are perchlorate salts.** Every one contains
`[Cl+3]([O-])([O-])([O-])[O-]`, ChEMBL's charge-separated perchlorate, and the two engines' InChI
normalisations of it differ. The element counts and charge still agree on all 600. That is why the
formula row is complete while the key rows are not.

**The stereo loss is the molfile's header, not the graph.** Open Babel writes every SMILES-derived
molfile with all-zero coordinates and a header claiming `2D` (2,897,819 of 2,897,819). It reads the
file back as 2D (all of them). The canonical SMILES of the read-back molecule keeps its stereo in all
but ~100 compounds, but the InChI writer drops the `/b /t /m /s` layers from 1,076,033 InChIs.
Nothing else in any InChI changes. A 2D layout (`--gen2D`) was probed as the alternative and
dropped: it took over 14 minutes per 10,000 compounds on the release's large macrocycles.

| PDBFixer claim | why it must hold | result (every chip) |
|---|---|---|
| every entry classified: prepared, refused by the force field, or skipped by a declared limit, each with its reason | nothing lost | 247: 230 / 10 / 7 |
| **every deposited heavy atom kept, and not moved** | preparation adds, it does not edit | **1,907,001 atoms, max shift 0.0 Å** |
| heavy atoms out == kept + what PDBFixer said it would add | bookkeeping | 240 / 240 (1,934,594) |
| **fixed point: PDBFixer on its own output finds nothing missing and adds no hydrogens** | a prepared structure is prepared | 240 / 240 |
| **Amber14's total charge == the charge counted from the protonation states' atom names** | two independent derivations | 230 / 230, max deviation 3.4e-13 e |
| Open Babel reads PDBFixer's PDB with the same atoms, hydrogens and elements | a second code reads it | 237 / 237; 3 entries exceed PDB's 99,999 atoms |
| written PDBx reads back with the same atoms | format round trip | 240 / 240 |

**The 17 that are not parametrised, and why.** Six entries need more than 200 internal residues
rebuilt (3ab2, 3ab4, 7abf, 7abg, 7abi, 7abr), and one needs 9,705 atoms built onto 17,922
deposited (7abh, a side-chain-truncated cryo-EM model). These are the two declared limits in
`fix_real.py`. Without them 7abh ran 1 h 50 min and did not finish, and 7abi and 7abg exhaust a
16 GiB limit. Ten more PDBFixer prepares, but Amber14 refuses them, which is what an MD user would
hit. Six are nucleotides whose atoms match a 3′-terminal template but whose external
bonds do not ("missing 1 P atom and has 1 O atom too many": 7abs, 7abz, 8ab0, 8abd, 8aby, 8abz).
The other four are an ARG with one C–N bond too many (1abi), a C-terminal MET with two H too many
(1abw), a HIS no template matches (2abe), and a CYS bonded to one S too many (5abz). All are in
[`results/arm64/fix/entries.tsv`](results/arm64/fix/entries.tsv).

## Across chips

- **Open Babel, Intel and AMD each against Graviton4: identical.** `ob_real.tsv` (2.9M rows, sha256
  `a27bb471…` uncompressed) is the same on all three chips and on two Graviton4 runs. So is every
  count except one. Nothing here is floating point, and OpenBLAS is not on the path.
- **The exception is not a chip effect.** After the molfile round trip, some canonical SMILES change
  their ring-exocyclic C=N stereo: 91 and 102 on two Graviton4 runs, 104 on Intel, 113 on AMD. Only 23
  compounds differ in all four runs, and 179 differ in at least one. Same chip and same bytes give a
  different set. The variation is in the molfile *writer*: its 0D up/down assignment iterates a
  pointer-ordered set, so it depends on allocation order, while reading fixed molfile bytes is
  deterministic (source and a 40-repeat repro: openbabel/openbabel#3036). RDKit
  reads each pair as different molecules. All but 2–4 per run are cyclobutene-diimine
  (squaramide-like) ring closures. The count is reported, never asserted.
- **One canonical SMILES is not idempotent**, the same on every chip. CHEMBL3229240 has two
  symmetric `[14CH3]` groups, and re-reading Open Babel's canonical string gives a different
  string. RDKit confirms it is the same molecule.
- **PDBFixer, Intel and AMD each against Graviton4: 30 of 31 per-entry columns identical on all
  247 entries.** That covers status, failure reason, every atom, residue, hydrogen and water count,
  the force-field and name-rule charges, and the fixed-point and Open Babel checks. The one that
  differs is the sha256 of the written structure (237 of 240), which holds the built atoms' and
  hydrogens' coordinates. Two Graviton4 runs also differ on 235 of 240, so those coordinates are a
  run-to-run path, not a chip property. They are reported, not asserted.

## Pins

| | |
|---|---|
| compounds | ChEMBL 37 `chembl_37_chemreps.txt.gz`, sha256 `ea6181ce…` (EBI's published checksum), staged by rdkit-real; [`pins.sha256`](pins.sha256). Data tier: a durable public id |
| RDKit's keys | `runs*/rdkit-real/chembl37/rdkit_real.tsv.gz`, checked in-run against rdkit-real's uncompressed sha256 `9a84f0ad…` |
| PDB cohort | RODA `s3://pdbsnapshots/20260101/pub/pdb/data/structures/divided/mmCIF/ab/`, 247 objects copied S3-to-S3, each sha256-pinned in [`cohort.sha256`](cohort.sha256); 181 equal cctbx-real's pins. Data tier: RODA |
| scripts | `ob_real.py`, `fix_real.py`, `real.sh` in `scripts.sha256`, checked in-task |
| image | the recipe's `comp-chem@sha256:b5c1b1ec…` (arm64) and its `comp-chem-1243f25ebfec` x86 twin |

`probe.task.json`, `probe2.task.json` and `gaps.task.json` are the sizing runs. They covered 50,000
compounds plus a 53-entry sample, then the 14 slowest entries, then a census of every entry's gaps and
missing atoms, which set the two limits. [`mkspec.py`](mkspec.py) writes all the specs.
Per-chip metrics, mismatch lists, entry tables, logs and completion records are in `results/`.

## Spend

About **$5.05** by spawn's estimates. The six verifying runs cost $2.00. The rest was three probes,
the gap census, a Graviton4 Open Babel run whose idempotence assertion failed on the one isotope
case above, and two Graviton4 PDBFixer runs stopped on the long-tail entries that the limits now
classify.
