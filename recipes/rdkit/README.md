# RDKit — exact cheminformatics identities on aspirin

One task. `rdkit` parses a molecule and computes its canonical SMILES, formula, InChIKey,
and ring/atom counts, and the smoke check confirms each against an exact or published
value.

> **What this recipe does and does not cover.** It parses one molecule and checks exact
> graph properties — enough to prove RDKit's native cheminformatics core works correctly
> on Graviton4. Not a benchmark; no conformer generation, fingerprinting at scale, or
> reaction handling.

## Why RDKit, and why every check is exact

RDKit underpins the largest user base in the `comp-chem` env (cheminformatics), and its
outputs are **exact-or-wrong** — molecular graphs have definite canonical forms and
counts, so there are no bands to argue about. The molecule is aspirin
(`CC(=O)Oc1ccccc1C(=O)O`), written inline; zero staging.

The strongest check is the **InChIKey against its published value**. An InChIKey is a
hash of the standardized molecular graph, so `BSYNRYMUTXBXSQ-UHFFFAOYSA-N` (aspirin,
PubChem CID 2244) is effectively a checksum of the whole structure verified against an
external, published reference — a wrong perception of aromaticity, tautomer, or
connectivity changes it. The canonical-SMILES round-trip (canonicalize, re-parse,
re-canonicalize → identical) is an internal idempotence identity on top of that.

## Pins

| | |
|---|---|
| image | `quay.io/aarchsci/comp-chem@sha256:a06f130ca3c8b514de1aa872536c9822c3ccb5322d594b935ae11627c5c80b09` |
| | tag `2026.09.04`, RDKit (+ pyscf, openmm, mdanalysis, …), cosign-signed, `linux/arm64` |
| input | aspirin SMILES, **inline in the task** — nothing staged |

**Data tier: none / in-task.** Same `comp-chem` image as `recipes/pyscf` and
`recipes/vina`; this recipe invokes only RDKit.

## Smoke check

Measured in this image, before any launch.

| observable | assertion | observed |
|---|---|---|
| canonical SMILES round-trip | canonicalize→reparse→canonicalize is stable | True |
| molecular formula | exactly `C9H8O4` | C9H8O4 |
| **InChIKey** | `BSYNRYMUTXBXSQ-UHFFFAOYSA-N` (published aspirin) | matches |
| ring count | exactly 1 | 1 |
| heavy atoms | exactly 13 | 13 |
| molecular weight | 180.16 ± 0.01 | 180.16 |

No thresholds — the InChIKey and formula are exact structural identities, the counts are
exact, and the MW matches the standard value.

## Resources, and what the timings mean

2 vCPU / 4 GiB, `c8g` (resolves to `c8g.large`), TTL 5m, cap $0.02. Parsing and descriptors
are **sub-second**, single-threaded.

**These timings are not compute cost.** Boot, the Docker install, and pulling the
**~0.62 GB** `comp-chem` image are the whole task. The recorded run's command window was
**81s** (01:14:05 → 01:15:26 UTC), the InChIKey matching the published aspirin value. TTL
was **retightened from that first real run**: 10m → **5m**, `cost_limit` $0.03 → $0.02. A
loose TTL is a larger blast radius, not caution; the recorded run used the original 10m.
Disk is trivial.

## Running it

No `stage-inputs.sh` — the molecule is in the task.

```sh
spawn task run --spec recipes/rdkit/01-descriptors.task.json --wait
```

Then **check the bucket**, every time:

```sh
aws s3 ls s3://scicookbook-942542972736-us-east-1/runs/rdkit/r1/
```

`--wait` exiting 0 does **not** prove the outputs exist (spore-host/spawn#561): the smoke
check runs *inside* the task, and the bucket listing is the second half of it. Expect one
object (`smoke-check.txt`).

**Re-running.** `task_id` is fixed; bump the `-r1` suffix in both `task_id` and the output
prefix to keep both records.

**Note on parallel launches.** A transient AWS `Invalid IAM Instance Profile name` on a
parallel launch is the IAM-propagation race (spore-host/spawn#572), not a recipe fault —
re-run.
