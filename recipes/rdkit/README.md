---
tool: rdkit
env: comp-chem
image: quay.io/aarchsci/comp-chem@sha256:a06f130ca3c8b514de1aa872536c9822c3ccb5322d594b935ae11627c5c80b09
spawn_version: 0.104.0
---
# RDKit — exact cheminformatics identities on aspirin

`rdkit` parses a molecule and computes its canonical SMILES, formula, InChIKey, and ring/atom counts — cheminformatics perception.

> **What this covers.** Parse one molecule and check exact graph properties — proof RDKit's native cheminformatics core works correctly on Graviton4. Not a benchmark; no conformer generation, fingerprinting at scale, or reaction handling.

## Run it

```python
from rdkit import Chem
m = Chem.MolFromSmiles("CC(=O)Oc1ccccc1C(=O)O")   # aspirin
Chem.MolToInchiKey(m)                              # BSYNRYMUTXBXSQ-UHFFFAOYSA-N
```

One task, single-threaded parsing. The aspirin SMILES is inline, so nothing is staged.

## Make it yours

| In the recipe | Swap for | What to know |
|---|---|---|
| aspirin (`CC(=O)Oc1ccccc1C(=O)O`, inline) | your own molecule (SMILES) | aspirin is the hand-checkable case; RDKit's outputs are exact-or-wrong for any molecule. |
| InChIKey against its published value | keep it as the primary check | the InChIKey is a hash of the standardized graph — effectively a checksum verified against an external reference (PubChem CID 2244). |

Deterministic — **nothing is determinism scaffolding**. **Leave the fixture:** molecular graphs have definite canonical forms and counts, so there's nothing a bigger molecule makes more legible. Leave-it.

## Shape, size, cost

One task, `c8g.large` (2 vCPU / 4 GiB), TTL 5m, cap $0.02. Parsing and descriptors are sub-second. Recorded command window **81s** — boot, Docker install, and the ~0.62 GB `comp-chem` image pull are the whole task ([why](../../practices/what-this-does-not-cover.md)). **These timings are not compute cost.**

<details>
<summary>As shipped: the identities, pins, smoke check, run + verify</summary>

### The check — every value exact or published

RDKit underpins the largest user base in the `comp-chem` env, and its outputs are exact-or-wrong — no bands to argue about. The strongest check is the **InChIKey against its published value**: `BSYNRYMUTXBXSQ-UHFFFAOYSA-N` (aspirin) is effectively a checksum of the whole structure against an external reference, so a wrong perception of aromaticity, tautomer, or connectivity changes it. The canonical-SMILES round-trip (canonicalize → reparse → recanonicalize → identical) is an internal idempotence identity on top.

### Pins (data tier: none / in-task)

| | |
|---|---|
| image | `quay.io/aarchsci/comp-chem@sha256:a06f130ca3c8b514de1aa872536c9822c3ccb5322d594b935ae11627c5c80b09` (tag `2026.09.04`, RDKit + pyscf + openmm + mdanalysis + …, cosign-signed, `linux/arm64`) |
| input | aspirin SMILES, inline — nothing staged |

Same `comp-chem` image as [pyscf](../pyscf/README.md) and [vina](../vina/README.md).

### Smoke check (inside the task; measured before launch)

| observable | assertion | observed | catches |
|---|---|---|---|
| canonical SMILES round-trip | stable through canonicalize→reparse→canonicalize | True | broken canonicalization |
| molecular formula | exactly `C9H8O4` | C9H8O4 | wrong perception |
| **InChIKey** | `BSYNRYMUTXBXSQ-UHFFFAOYSA-N` (published aspirin) | matches | wrong graph |
| ring count | exactly 1 | 1 | wrong ring perception |
| heavy atoms | exactly 13 | 13 | atoms lost |
| molecular weight | 180.16 ± 0.01 | 180.16 | wrong masses |

### Run + verify

```sh
spawn task run --spec recipes/rdkit/01-descriptors.task.json --wait
aws s3 ls s3://scicookbook-942542972736-us-east-1/runs/rdkit/r1/
```

`--wait` exiting 0 does **not** prove the outputs exist — an exit code says the command ran, never that its output is real; the smoke check runs *inside* the task, and the bucket listing is the second half of it. Expect one object (`smoke-check.txt`). Re-run: bump the `-r1` suffix.

</details>
