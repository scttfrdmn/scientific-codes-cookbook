---
tool: openbabel
tool_version: 3.2.1
env: comp-chem
image: quay.io/aarchsci/comp-chem@sha256:a06f130ca3c8b514de1aa872536c9822c3ccb5322d594b935ae11627c5c80b09
spawn_version: 0.111.4
last_verified: 2026-10-02
---
# Open Babel — 100,000 InChIKeys, checked against RDKit and against ChEMBL

Regenerates ChEMBL 37's published keys with a second engine, so the two toolkits check each other. For anyone picking a cheminformatics toolkit.

> **Do not use the `obabel` CLI for a batch.** Measured: it aborts at the first malformed SMILES and
> still exits 0. Use `pybel` — details below.

## Run it

```bash
for s in $(make -s spec RECIPE=openbabel-pdbfixer); do spawn task run --spec "$s" --wait; done   # produces rdkit_keys.tsv; this recipe reads it
make run RECIPE=openbabel-pdbfixer   # 24 s + the structure-prep task
make ls  RECIPE=openbabel-pdbfixer

python3 -c "from openbabel import pybel; \
  print(pybel.readstring('smi','CC(=O)Oc1ccccc1C(=O)O').write('inchikey').strip())"
```

## Make it yours

| In the recipe | Swap for | What to know |
|---|---|---|
| `pybel` per molecule | the `obabel` CLI | **don't.** One bad record truncates the run silently, exit 0. |
| ChEMBL 37's first 100k | your library | reused byte-for-byte from [rdkit](../rdkit/README.md); the comparison needs identical bytes. |
| InChIKey | canonical SMILES | SMILES canonicalisation is algorithm-specific, so two engines legitimately differ — there is nothing to assert. |

**Leave the library** — 100k compounds with a published key is what makes a two-engine comparison
checkable. **Scale it** freely: 0.24 ms per molecule.

## Shape, size, cost

`c8g.xlarge` (4 vCPU): **24 s, ~$0.01**, plus a sub-second structure-prep task. No generation table —
at 24 s a ladder would measure boot ([same call as mash](../mash/README.md)).

<details>
<summary>As shipped: the CLI trap measured, and why the published-reference rate is not neutral</summary>

### The checks

| observable | assertion | observed |
|---|---|---|
| compounds / rows written | exactly 100,000 each | **100,000** |
| parsed by Open Babel | exactly 100,000 | **100,000** (RDKit: 99,997) |
| vs ChEMBL, full key | exactly 99,846 | **99,846** (99.846%) |
| vs ChEMBL, connectivity | exactly 99,930 | **99,930** (99.930%) |
| **vs RDKit, full key** | **exactly 99,926** | **99,926** (99.929%) |
| **vs RDKit, connectivity** | **exactly 99,928** | **99,928** (99.931%) |

No tolerance is involved anywhere: InChI is canonical by construction, so each comparison is a
27-character string that matches or does not.

### The published-reference rate is not a neutral referee

RDKit agrees with ChEMBL's connectivity on **99,996 of 99,997**. Open Babel disagrees on **70** — a
seventyfold difference against the same reference. That is not a defect in Open Babel: those
canonical SMILES were written by *some* toolkit, and whichever one it was round-trips its own output
best. So **the toolkit-versus-toolkit number is the neutral one** (99.929% full key, 99.931%
connectivity across the 99,997 both engines parsed), and the ChEMBL rates should be read as "how
close is this engine to the one that produced the file".

Open Babel is also the more permissive parser — it produced a key for all 100,000 where RDKit rejected
3. "More permissive" and "closer to this particular reference" point in opposite directions, which is
the useful thing when choosing between them.

### The CLI trap, measured

`obabel -ismi x.smi -oinchikey` is the obvious batch invocation and it is unsafe. Three files, same
four valid molecules, one malformed record moved around:

| input | molecules in | keys out | exit |
|---|---|---|---|
| no bad record | 4 | 4 | 0 |
| **bad record 3rd of 5** | 5 | **2** | **0** |
| bad record last of 5 | 5 | 4 | 0 |

**It aborts at the first malformed SMILES and reports success.** Bad-in-the-middle lost the broken
record *and both valid molecules after it*. `--append title` emits no identifier, so there is nothing
to join on either — matching 100k results by row position would have produced a confident wrong
agreement rate that looked like the two engines disagreeing about chemistry. `pybel` costs exactly one
row per bad molecule, catchably, and gave 4 ok / 1 failed on both orderings.

This is why the recipe iterates in Python with explicit ids. The trap was found with a one-cent probe
before the real run, which is [cheap-to-expensive sequencing](../../patterns/sizing.md) earning its
place.

### Task 2 — structure prep, deliberately small

`02-prep` keeps a hand-checkable fixture: aspirin through a SMILES→SDF→SMILES round-trip with an
exact formula and InChIKey, and a two-residue peptide through `pdbfixer`, with Open Babel
independently counting the hydrogens pdbfixer added. Small is the point there, as with
[bedtools' intervals](../bedtools/README.md) — the answers are checkable by hand, and a real protein
would run the same code paths while proving less.

### Pins

| | data tier |
|---|---|
| Open Babel | 3.2.1, in `quay.io/aarchsci/comp-chem@sha256:a06f130ca3c8…` (`linux/arm64`) |
| compounds | ChEMBL 37 first 100,000 rows, sha256 `6fa986de4223948a…` — staged by [rdkit](../rdkit/README.md) |
| RDKit's keys | `runs/rdkit/r1/rdkit_keys.tsv` — the comparison reads the real run's output |

Nothing is staged twice, and the cross-check reads RDKit's actual output rather than recomputing it.

### Run + verify

```sh
make run RECIPE=rdkit
make run RECIPE=openbabel-pdbfixer
make ls  RECIPE=openbabel-pdbfixer
```

Expect `inchikey-smoke-check.txt` with `rdkit_key_match 99926`, and `prep-smoke-check.txt` from the
structure task.

</details>
