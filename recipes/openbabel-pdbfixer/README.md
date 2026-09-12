---
tool: openbabel
env: comp-chem
image: quay.io/aarchsci/comp-chem@sha256:a06f130ca3c8b514de1aa872536c9822c3ccb5322d594b935ae11627c5c80b09
spawn_version: 0.104.0
---
# Open Babel + PDBFixer — structure prep, cross-validated

Open Babel handles chemical perception (SMILES ↔ SDF, formula, InChIKey); PDBFixer repairs a protein structure (missing atoms, hydrogens), and Open Babel reads the result back.

> **What this covers.** Round-trip one molecule through a format conversion, cross-check an InChIKey against RDKit, repair a small heavy-atom peptide — proof Open Babel and PDBFixer work and hand off correctly on Graviton4. Not a benchmark; no large-molecule prep or docking pipeline.

## Run it

```bash
obabel -:"CC(=O)Oc1ccccc1C(=O)O" -osdf | obabel -isdf -oinchikey   # perception → InChIKey
pdbfixer input.pdb --add-atoms=all --add-residues --ph=7.0         # repair the peptide
# then Open Babel reads fixed.pdb back and counts the 12 H PDBFixer added — the repair cross-check
```

One task, two tools. The aspirin SMILES and an ALA-ALA heavy-atom PDB are inline, so nothing is staged.

## Make it yours

| In the recipe | Swap for | What to know |
|---|---|---|
| aspirin SMILES + ALA-ALA heavy-atom PDB (inline) | your own molecule / structure | small hand-checkable inputs; for inputs like these, Open Babel and PDBFixer are sub-second — large-molecule prep (out of scope here; see the caveat) is where size begins to matter. |
| InChIKey as the cross-check metric | keep it — don't use canonical SMILES | **load-bearing:** InChIKey is an IUPAC standard, so two engines *must* agree; canonical SMILES is algorithm-specific and the two engines produce different strings ([compare like with like](../../practices/cross-checks.md)). |

Deterministic — **nothing is determinism scaffolding**. **Leave the fixture:** the identities are exact-or-wrong for any molecule, and a small one keeps every count hand-auditable. Leave-it.

## Shape, size, cost

One task, `c8g.large` (2 vCPU / 4 GiB), TTL 5m, cap $0.02. Conversions + repair are sub-second. Recorded command window **65s** — boot, Docker install, and the ~0.62 GB `comp-chem` image pull are the whole task ([why](../../practices/what-this-does-not-cover.md)). **These timings are not compute cost.**

<details>
<summary>As shipped: the two flows, pins, smoke-check table, run + verify</summary>

### The checks — chemical decode + a cross-validated repair

- **Open Babel — format decode + a two-engine cross-check.** A SMILES → SDF → SMILES round-trip recovers the canonical form (the graph survives, not just metadata); the formula (`C9H8O4`) and heavy-atom count (13) are preserved; and Open Babel's InChIKey equals RDKit's (`BSYNRYMUTXBXSQ-UHFFFAOYSA-N`, aspirin).
- **PDBFixer → Open Babel — a repair confirmed by an independent reader.** PDBFixer adds the missing C-terminal oxygen (heavy 10 → 11) and the hydrogens (12 at pH 7); Open Babel reads the repaired PDB and independently counts 12 H. The two tools cross-validate the repair rather than each self-reporting.

### Pins (data tier: none / in-task)

| | |
|---|---|
| image | `quay.io/aarchsci/comp-chem@sha256:a06f130ca3c8b514de1aa872536c9822c3ccb5322d594b935ae11627c5c80b09` (tag `2026.09.04`, Open Babel + PDBFixer + RDKit + …, cosign-signed, `linux/arm64`) |
| input | aspirin SMILES + an ALA-ALA heavy-atom PDB, inline — nothing staged |

Same `comp-chem` image as [rdkit](../rdkit/README.md), [pyscf](../pyscf/README.md), [vina](../vina/README.md).

### Smoke check (inside the task; measured before launch)

| observable | assertion | observed | catches |
|---|---|---|---|
| SMILES round-trip | canonical stable through SMILES→SDF→SMILES | True | graph not preserved |
| formula | `C9H8O4` | C9H8O4 | wrong perception |
| heavy atoms | 13 | 13 | atoms lost |
| **InChIKey (2-engine)** | Open Babel == RDKit == `BSYNRYMUTXBXSQ-…` | match | either engine wrong |
| PDBFixer heavy atoms | 11 (10 input + OXT added) | 11 | repair failed |
| **PDBFixer H added** | 12 (ALA-ALA at pH 7) | 12 | wrong protonation |
| **Open Babel confirms H** | 12 H in the repaired PDB | 12 | handoff mangled |

All exact-or-wrong — standardized graph hashes and exact counts, no bands.

### Run + verify

```sh
make run RECIPE=openbabel-pdbfixer
make ls RECIPE=openbabel-pdbfixer
```

The smoke check runs *inside* the task, and the bucket listing is the second half of it — an exit code says the command ran, never that its output is real. Expect two objects (`fixed.pdb`, `smoke-check.txt`). Re-run: `make run` launches a fresh task each time and overwrites this prefix — no spec edit needed.

</details>
