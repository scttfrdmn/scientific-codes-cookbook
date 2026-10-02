---
tool: rdkit
tool_version: 2026.03.1
env: comp-chem
image: quay.io/aarchsci/comp-chem@sha256:a06f130ca3c8b514de1aa872536c9822c3ccb5322d594b935ae11627c5c80b09
spawn_version: 0.111.4
last_verified: 2026-10-02
---
# RDKit — reproduce ChEMBL's published InChIKeys for 100,000 compounds

Regenerates ChEMBL 37's own `standard_inchi_key` and formula from its SMILES, in 37 seconds. For anyone running RDKit over a real compound library.

## Run it

```bash
make stage RECIPE=rdkit   # once: 100,000 ChEMBL 37 compounds WITH their published keys
make run   RECIPE=rdkit   # 37 s on c8g.xlarge, self-terminating
make ls    RECIPE=rdkit   # rdkit_keys.tsv + smoke-check.txt

python3 -c "from rdkit import Chem; from rdkit.Chem import inchi; \
  print(inchi.MolToInchiKey(Chem.MolFromSmiles('CC(=O)Oc1ccccc1C(=O)O')))"
```

## Make it yours

| In the recipe | Swap for | What to know |
|---|---|---|
| ChEMBL 37's first 100k | your own library | the input must carry a reference identifier, or there is nothing to check against. |
| InChIKey + formula | `Descriptors`, fingerprints, substructure | those have no published reference, so a check becomes self-consistency — which is why this recipe does not use them. |
| 100,000 compounds | more | 0.37 ms each, so the whole of ChEMBL is minutes, not hours. |

**Leave the library** — it ships the answer alongside the question, which is what makes an exact
check possible. **Scale it** by compound count; it is linear and cheap.

## Shape, size, cost

`c8g.xlarge` (4 vCPU): **37 s, ~$0.01.** Single-threaded Python over 100k molecules, ~6 MB in.
No generation table: at 37 s a four-chip ladder would measure boot, not RDKit
([the same call as mash](../mash/README.md)).

<details>
<summary>As shipped: three graded checks against one published reference, and why one needed restricting</summary>

### The checks

| observable | assertion | observed |
|---|---|---|
| compounds | exactly 100,000 | **100,000** |
| parsed | exactly 99,997 | **99,997** |
| rows written | == compounds | **100,000** |
| **element counts** | **== comparable count, no exceptions** | **97,665 / 97,665** |
| **connectivity block** | **exactly 99,996** | **99,996** (99.999%) |
| full InChIKey | exactly 99,914 | **99,914** (99.917%) |

**Three claims of different strength against one source, which is more informative than one rate.**
An InChIKey is `<14-char skeleton>-<8-char stereo/isotope/proto>-<charge>`, and the first block is
connectivity alone. Comparing it separately separates *"we disagree about the molecular graph"* from
*"we disagree about stereochemistry"* — and the answer is that RDKit and ChEMBL agree on the graph for
**99,996 of 99,997** compounds, one disagreement in a hundred thousand. The 82 remaining full-key
differences are stereo-block only, and the pattern is RDKit returning `UHFFFAOYSA` — the canonical
"no stereo" block — where ChEMBL has stereochemistry. The SMILES column does not always carry the
stereo that ChEMBL's molblock encodes, so that shortfall is information missing from the input rather
than a disagreement about chemistry.

An InChIKey comparison needs **no tolerance at all**: InChI is canonical by construction, so the
result is a 27-character string that matches or does not. That puts it with
[bedtools' interval algebra](../bedtools/README.md) rather than with the rank correlations elsewhere
in this catalog.

### The formula check had to be restricted twice, and the second time made it exact

Comparing formula *strings* compares a convention. Measured on the 4,452 initial mismatches:

| cause | count |
|---|---|
| InChI writes a salt dot-separated (`C24H34N4O2S.ClH`) where RDKit merges it | 3,827 |
| RDKit writes a trailing charge sign; InChI uses separate `/q` and `/p` layers | 532 |
| neither | 11 |

Summing element counts across components fixed most of it (95.5% → 98.0%), but 1,954 still differed.
Splitting by whether the InChI defers composition to a charge layer explains the rest:

| subset | compared | match |
|---|---|---|
| **no `/q` and no `/p`** | **97,665** | **97,665 — 100.0000%, zero exceptions** |
| has `/q` or `/p` | 2,332 | 378 (16.21%) |

InChI's formula layer carries the **neutral** formula and `/p` moves the hydrogens, so for those
2,332 it is not the full atom inventory — comparing it against a complete formula compares different
quantities. Restricted to where the formula layer *is* the whole story, agreement is exact with no
exceptions, which is a stronger statement than any percentage. The restriction is principled rather
than convenient, the same move [sourmash](../sourmash/README.md) needed for its rank statistic.

### Pins

| | data tier |
|---|---|
| RDKit | 2026.03.1, in `quay.io/aarchsci/comp-chem@sha256:a06f130ca3c8…` (`linux/arm64`) |
| compounds | ChEMBL `releases/chembl_37/chembl_37_chemreps.txt.gz`, first 100,000 data rows; sha256 `6fa986de4223948a…` |

The release directory is immutable, so the release is the durable id; the subset is the first 100,000
rows, which anyone can reproduce from the same release. ChEMBL ids are roughly registration-ordered,
so this is an older slice rather than a random sample. The version is read from inside the run
because a package version is not a binary version.

[Open Babel](../openbabel-pdbfixer/README.md) reads this same subset and this recipe's own
`rdkit_keys.tsv`, and the asymmetry there is worth knowing before trusting either rate.

### Run + verify

```sh
make stage RECIPE=rdkit
make run   RECIPE=rdkit
make ls    RECIPE=rdkit
```

Expect `smoke-check.txt` with `skeleton_match 99996` and `atoms_match 97665`.

</details>
