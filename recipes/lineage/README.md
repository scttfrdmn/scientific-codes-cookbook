---
tool: nextclade
tool_version: "3.22.0"
images:
  - quay.io/aarchbio/nextclade@sha256:7cbf30abb4fa7fbc9b9a2c608b9ed361d8527514af139fb6c1883dce16e16f62
  - quay.io/aarchbio/pangolin@sha256:32551903d08cbe39fc70fcf924326fec08c100b8416346acd0302bb0e7f2a03a
spawn_version: 0.123.0
last_verified: 2026-10-08
---
# Nextclade vs pangolin — two lineage callers, 163 of 163 compatible

Calls Pango lineages for 165 SARS-CoV-2 genomes with both tools on Graviton4 and compares them in one namespace. For anyone doing pathogen surveillance on ARM.

## Run it

```bash
make stage RECIPE=lineage       # once: a pinned Nextclade dataset + the Pango alias table
for s in $(make -s spec RECIPE=lineage); do spawn task run --spec "$s" --wait; done
make ls RECIPE=lineage

nextclade run --input-dataset dataset --output-tsv nextclade.tsv dataset/sequences.fasta
pangolin dataset/sequences.fasta --outfile pangolin.csv --skip-designation-cache
```

Two tasks: Nextclade calls and stages its results, then pangolin calls the same sequences and compares.

## Make it yours

| In the recipe | Swap for | What to know |
|---|---|---|
| the dataset's example sequences | your consensus FASTA | both tools take a multi-FASTA of assembled genomes, not reads. |
| dataset tag `2026-09-07--17-10-15Z` | `nextclade dataset list --include-old` | **pin a tag.** 25 historical versions are retained back to 2024-01-16; `nextclade dataset get` without one is a moving target. |
| **unalias before comparing** | — | **keep this.** `C.*` *is* `B.1.1.1.*` and `BA.*` is `B.1.1.529.*` — comparing printed names treats a rename as a disagreement. |
| `--skip-designation-cache` | drop it | pangolin otherwise tries to refresh its designation cache, which a recipe here may not do at run time. |
| exact string match | hierarchy-compatible | a call of `BA.2` against `BA.2.3` is a granularity difference, not an error. Decide which you actually mean. |

**Leave the fixture.** 165 sequences is the dataset's own declared example set, pinned by the same tag as the reference and tree — so there is no second source to keep true. **Scale it** to your own genomes once it passes; both tools handle thousands per run.

## Shape, size, cost

Two tasks on `m8g.large` / `m8g.xlarge`, TTL 30m and 40m, caps $0.10 and $0.15. Both callers finish 165 genomes in well under a minute; the recorded windows are mostly boot and image pull, so **these timings are not compute cost** ([layout](../../patterns/layout-and-effective-cost.md)).

<details>
<summary>As shipped: perfect compatible agreement, the naming convention that faked a disagreement, and the check that is not available</summary>

### The checks

| observable | assertion | observed |
|---|---|---|
| staged bytes | match pins | dataset + alias table match |
| **dataset self-reports its tag** | matches what staging recorded | `2026-09-07--17-10-15Z` |
| **Nextclade self-identity** | **reference vs its own dataset: 0 differences** | **0 subs, 0 dels, 0 ins, 0 missing** |
| rows | 165 from each tool | 165 / 165 |
| **shared sequences** | ≥100, or the join key is wrong | **165** |
| both assigned | — | 163 |
| exact agreement | *reported, not asserted* | **0.9571** |
| **hierarchy-compatible** | **≥ 0.80** | **1.0000 (163/163)** |

### Self-identity is the exact part

The dataset's reference **is** the coordinate system, so running it back as a query must find zero
differences. Algorithmic and independent of any lineage question — a broken alignment fails it
even if the lineage calls look fine.

### The naming convention that faked a disagreement

The first version compared lineage names after accounting for hierarchy (`BA.2` vs `BA.2.3` is a
granularity difference, not an error) and got **0.9877**, with exactly two exceptions:

```text
England/MILK-516E26/2020    nextclade=B.1.1.1    pangolin=C.27
England/MILK-74F4E4/2020    nextclade=B.1.1.1    pangolin=C.27
```

Those name the **same placement**. Pango assigns a lineage a new letter prefix once its dotted
name grows too long, so `C.*` **is** `B.1.1.1.*`. A prefix comparison cannot see it, because the
two strings share no prefix — **a limitation of the metric, not a difference between the tools.**

Resolving aliases first takes compatible agreement to **163/163**:

| | |
|---|---|
| hierarchy-compatible, names as printed | 0.9877 |
| hierarchy-compatible, aliases resolved | **1.0000** |

The exact rate stays **reported rather than asserted** at 0.9571: the 7 non-identical calls are
granularity differences between two methods, which is legitimate. The asserted quantity is "both
callers placed this genome in the same part of the Pango tree", which is the claim actually being
made ([cross-checks](../../practices/cross-checks.md)).

Recombinant `X*` lineages map to a **list** of parents and have no single root form, so they are
left unaliased rather than forced into one arbitrarily.

### The check this recipe cannot make

The appealing version is "reproduce the depositors' Pango lineages." It does not exist in pinnable
form:

- **GenBank** records carry no Pango lineage at all.
- **NCBI Virus**'s lineage column is **NCBI-computed**, not deposited, and comes from a
  non-pinnable UI query.
- **pango-designation**'s authoritative `lineages.csv` keys on **GISAID virus names**, whose
  sequences cannot be redistributed — you can pin the labels and never legally stage the bytes
  they label.

So the check is cross-code instead. Note the distinction that made the fix above possible:
`alias_key.json` from the *same* repository keys on lineage **prefixes**, carries no GISAID data,
and is pinnable by release tag. One file in a repo being unusable does not make the repo unusable.

### Pins

| | |
|---|---|
| dataset | `nextstrain/sars-cov-2/wuhan-hu-1/orfs` at tag `2026-09-07--17-10-15Z` (reference, tree, annotation, 165 examples) |
| alias table | `cov-lineages/pango-designation` at `v1.41`, 608 entries, spot-checked against `C`/`AY`/`BA` |
| images | nextclade `@sha256:7cbf30ab…` (3.22.0), pangolin `@sha256:32551903…` (4.4) |

Both images cosign-verified; signatures cover the **manifest-list** digest, so verify the tag and
pin the arm64 digest. The dataset's `pathogen.json` self-reports its `version.tag` and both tasks
assert it, so a swapped dataset cannot pass unnoticed.

*nextclade's published tags are 3.21.2 and 3.22.0 — not 3.24.0, as this project's roadmap recorded.*

### Two implementation notes

**The join key differs between the tools.** Nextclade keys on the full FASTA header; pangolin
truncates at the first whitespace. Joining raw keys intersects to **nothing**, so both sides key on
the first token, with a floor of 100 shared sequences so an empty intersection fails loudly rather
than producing a vacuous rate.

**The nextclade image ships no interpreter.** It is a single-tool bioconda image around a Rust
binary, so the dataset-tag check uses `grep` rather than a JSON parser — and that check must not
redirect stderr, or `command not found` surfaces as an empty value and the failure message blames
the dataset.

### Run + verify

```sh
make stage RECIPE=lineage
for s in $(make -s spec RECIPE=lineage); do spawn task run --spec "$s" --wait; done
aws s3 cp "s3://$(make -s print-bucket)/runs/lineage/r1/score.tsv" -
```

Fails on a pin or tag mismatch, a reference that differs from itself, fewer than 100 shared
sequences, or compatible agreement below 0.80 — but check the bucket regardless
([exit 0 isn't proof](../../practices/container-path.md)).

### Not covered

Nextclade's QC metrics and private-mutation counts, frameshift and stop-codon detection, pangolin's
`--analysis-mode fast` (pangoLEARN) as a third opinion, scorpio constellation calls, non-SARS-CoV-2
datasets (Nextclade ships many), and assembly from reads — these take consensus genomes as input.

</details>
