---
tool: foldseek
tool_version: "8.ef4e960"
image: quay.io/aarchbio/foldseek@sha256:871c61cc7c37557742972ef4f57fecf47a9eb3494f95f5fb28b9edc132b3644b
spawn_version: 0.123.0
last_verified: 2026-10-08
---
# foldseek — structural search, checked by what cannot outscore a structure

Searches 16 PDB structures against a database built from themselves on Graviton4, so each must find itself at TM-score 1.0 with nothing scoring higher. For anyone doing structural bioinformatics on ARM.

## Run it

```bash
make stage RECIPE=foldseek      # once: 16 pinned mmCIF entries, 3 MB
spawn task run --spec "$(make -s spec RECIPE=foldseek)" --wait
make ls RECIPE=foldseek

foldseek createdb structures/ db
foldseek search db db aln tmp --alignment-type 1 -e inf --max-seqs 2000 -a
foldseek convertalis db db aln aln.tsv --format-output "query,target,alntmscore,evalue,bits"
```

## Make it yours

| In the recipe | Swap for | What to know |
|---|---|---|
| 16 pinned mmCIFs | your structures, or a prebuilt DB | **foldseek's `pdb100` cannot be pinned** — unversioned URL, and the widely-cited mirror serves HTML with HTTP 200. Build from pinned files. |
| `--alignment-type 1` | `2` (3Di+AA, the default) | type 1 is TMalign and reports a real **TM-score**; the default is faster but its score is not a TM-score. Pick by what you need to assert. |
| `-e inf --max-seqs 2000` | real thresholds | **inf keeps every pair**, which this check needs — a truncated hit list could hide a counterexample. For actual searching, filter. |
| self-hit check | your own | **this is the part worth copying** — make your queries members of your database and nothing can outscore a structure's alignment to itself. Algorithmic, so no band. |
| `.cif` | `.pdb`, `.pdb.gz` | foldseek reads all of them. Predicted structures (AlphaFold/ESMFold output) work too. |

**Leave the fixture.** 16 entries is 3 MB and hand-checkable, and it deliberately contains a structurally redundant pair so the check is tested against a real tie. **Scale it** to a real database once it passes — foldseek's whole point is searching millions of structures fast.

## Shape, size, cost

One task on `c8g.xlarge` (4 vCPU / 8 GiB), TTL 25m, cap $0.08. `createdb` plus 138 all-against-all TMalign alignments take seconds; the recorded window is boot and image pull, so **these timings are not compute cost** ([layout](../../patterns/layout-and-effective-cost.md)).

<details>
<summary>As shipped: an algorithmic check with no band, a deliberate tie, and why chains outnumber files</summary>

### The checks

| observable | assertion | observed |
|---|---|---|
| staged bytes | match pins | tar + revisions match |
| structures present | 16, from a staged count | **16** |
| **distinct structures in results** | **all 16 represented** | **16** |
| chains indexed | ≥ structures (see below) | 21 |
| alignment rows | — | 138 |
| **self-alignment present** | **every query aligns to itself** | **0 missing** |
| **self TM-score** | **= 1.0** | **0 below 1** |
| **nothing outscores a self-hit** | **0 queries beaten** | **0** |

### Why this needs no tolerance at all

The queries **are** the database. A structure aligned to itself is a perfect superposition, so its
TM-score is 1.0 by construction, and no other structure can do better than perfect. That makes the
check algorithmic rather than empirical — there is no band to justify, no reference value to source,
and nothing to re-derive if the fixture changes.

### "Nothing beats itself", not "the best hit is itself"

The wording is the check. Ties are legal, and this set contains a real one on purpose:

```text
1pga vs 2gb1   TM-score 0.9115      both are the protein G B1 domain
2gb1 vs 1pga   TM-score 0.9115
```

0.9115 is a genuinely high cross-score produced by correct biology. An **argmax** test — "the top
hit must be the query itself" — breaks as soon as a tie is exact, because the tool is free to order
equal scores arbitrarily. This project already paid for that distinction: the equivalent BLAST
check failed **19 of 20 times** on arbitrary tie-breaking.

So the assertion is the inequality `self ≥ every other`, which is the claim actually being made.
The redundant pair is staged deliberately so the recipe is tested against a near-tie rather than
only against structurally unrelated proteins.

### Chains outnumber files, and the count must account for it

`foldseek createdb` indexes **one entry per chain**, not per file. 4HHB is a hemoglobin tetramer and
1IEP a dimer, so 16 mmCIF files produce **21** queries. A guard asserting one query per file is
asserting the wrong relationship.

The recipe therefore asserts on **distinct structures** — every staged entry must be represented —
and separately that chains ≥ structures, reporting both. The self-hit check runs per chain, which
is strictly more coverage than per file.

### Pins

| | |
|---|---|
| structures | 16 RCSB entries as one flat tar, `9ae0a067…` |
| revisions | recorded per entry: 1CRN **1.5**, 4HHB **4.3**, 1UBQ 1.3, … |
| image | `quay.io/aarchbio/foldseek@sha256:871c61cc…` (foldseek 8.ef4e960) |

**PDB accessions are immutable; PDB file bytes are not.** Entries get revised and
`files.rcsb.org` serves *current*, so the pin is accession + sha256 + the revision number from the
REST API — cryptographic and human-legible. A future revision changes the sha256 and the recipe
stops, which is correct: re-derive rather than assume the numbers survived.

**The prebuilt database is unpinnable**, which is why this builds its own. The real `pdb100` is
2.17 GiB behind an unversioned Cloudflare-worker URL, and the commonly cited
`search.foldseek.com/data/pdb100.tar.gz` **returns an HTML page with HTTP 200** — a dead URL that
looks alive.

cosign-verified against `playgroundlogic/aarchbio`; the signature covers the **manifest-list**
digest, so verify the tag and pin the arm64 digest.

### Run + verify

```sh
make stage RECIPE=foldseek
spawn task run --spec "$(make -s spec RECIPE=foldseek)" --wait
aws s3 cp "s3://$(make -s print-bucket)/runs/foldseek/r1/score.tsv" -
```

Fails on a pin mismatch, a missing self-alignment, a self TM-score below 1.0, any structure
outscored by another, or a structure absent from the results — but check the bucket regardless
([exit 0 isn't proof](../../practices/container-path.md)).

### Not covered

Searching a real database (the point of the tool), `foldseek easy-search`, clustering
(`foldseek cluster`), the `prostt5` sequence-to-structure mode, comparison against **TM-align** —
now published in aarchbio and the obvious cross-code partner for TM-score — and scoring *predicted*
structures, which is where this would meet an ESMFold recipe
([#1](https://github.com/scttfrdmn/scientific-codes-cookbook/issues/1)).

</details>
