# Open Babel + PDBFixer — structure prep, cross-validated

One task, two tools. Open Babel handles chemical perception (SMILES ↔ SDF, formula,
InChIKey) and PDBFixer repairs a protein structure (adds missing atoms and hydrogens);
then Open Babel reads PDBFixer's output back and independently confirms the repair. The
smoke check confirms exact chemical identities, a two-engine cross-check against RDKit,
and the cross-tool agreement on the added hydrogens.

> **What this recipe does and does not cover.** It round-trips one molecule through a
> format conversion, cross-checks an InChIKey against RDKit, and repairs a small
> heavy-atom peptide — enough to prove Open Babel and PDBFixer work and hand off correctly
> on Graviton4. Not a benchmark; no large-molecule prep or docking pipeline.

## Two flows, cross-validated

- **Open Babel — chemical-format decode + a two-engine cross-check.** A SMILES →
  SDF → SMILES round-trip recovers the canonical form (the molecular graph survives the
  conversion, not just metadata), and the formula (`C9H8O4`) and heavy-atom count (13) are
  preserved. Then the headline: **Open Babel's InChIKey equals RDKit's**
  (`BSYNRYMUTXBXSQ-UHFFFAOYSA-N`, aspirin). InChIKey is an IUPAC standard, so two unrelated
  cheminformatics engines *must* agree — the RAxML-NG/IQ-TREE cross-code move. (Canonical
  *SMILES* would not work for this: it's algorithm-specific and the two engines produce
  different strings; the standardized InChIKey is what makes the cross-check valid.)
- **PDBFixer → Open Babel — a repair, confirmed by an independent reader.** PDBFixer takes
  a heavy-atoms-only ALA-ALA peptide, adds the missing C-terminal oxygen (heavy 10 → 11)
  and the hydrogens (12 at pH 7), then Open Babel reads the repaired PDB and independently
  counts **12 H** — matching PDBFixer's own count. The two tools cross-validate the repair
  rather than each self-reporting.

## Pins

| | |
|---|---|
| image | `quay.io/aarchsci/comp-chem@sha256:a06f130ca3c8b514de1aa872536c9822c3ccb5322d594b935ae11627c5c80b09` |
| | tag `2026.09.04`, Open Babel + PDBFixer + RDKit (+ pyscf, openmm, …), cosign-signed, `linux/arm64` |
| input | aspirin SMILES + an ALA-ALA heavy-atom PDB, **inline in the task** — nothing staged |

**Data tier: none / in-task.** Same `comp-chem` image as `recipes/rdkit`, `recipes/pyscf`,
`recipes/vina`; this recipe uses Open Babel, PDBFixer and RDKit.

## Smoke check

Measured in this image, before any launch.

| observable | assertion | observed |
|---|---|---|
| SMILES round-trip | canonical stable through SMILES→SDF→SMILES | True |
| formula | `C9H8O4` | C9H8O4 |
| heavy atoms | 13 | 13 |
| **InChIKey (2-engine)** | Open Babel == RDKit == `BSYNRYMUTXBXSQ-…` | match |
| PDBFixer heavy atoms | 11 (10 input + OXT added) | 11 |
| **PDBFixer H added** | 12 (ALA-ALA at pH 7) | 12 |
| **chain: Open Babel confirms H** | Open Babel counts 12 H in the repaired PDB | 12 |

All exact-or-wrong: the InChIKey is a standardized graph hash, the formula/counts are exact,
and the H-count agreement between the two tools validates the repair.

## Resources, and what the timings mean

2 vCPU / 4 GiB, `c8g` (resolves to `c8g.large`), TTL 5m, cap $0.02. The
conversions + repair are **sub-second**.

**These timings are not compute cost.** Boot, the Docker install, and pulling the
**~0.62 GB** `comp-chem` image are the whole task. The recorded run's command window was
**65s** (02:59:14 → 03:00:19 UTC), all seven checks passing (InChIKey OB == RDKit, the
PDBFixer → Open Babel chain agreeing on 12 H). TTL was **retightened from that first real
run**: 10m → **5m**, `cost_limit` $0.03 → $0.02. A loose TTL is a larger blast radius, not
caution; the recorded run used the original 10m. Disk is trivial.

## Running it

No `stage-inputs.sh` — inputs are inline.

```sh
spawn task run --spec recipes/openbabel-pdbfixer/01-prep.task.json --wait
```

Then **check the bucket**, every time:

```sh
aws s3 ls s3://scicookbook-942542972736-us-east-1/runs/openbabel-pdbfixer/r1/
```

The smoke check runs *inside* the task, and the bucket listing is the second half of it.
Expect two objects (`fixed.pdb`, `smoke-check.txt`).

**Re-running.** `task_id` is fixed; bump the `-r1` suffix in both `task_id` and the output
prefix to keep both records.
