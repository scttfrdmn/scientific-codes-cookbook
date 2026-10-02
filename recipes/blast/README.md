---
tool: blast
tool_version: 2.16.0
image: quay.io/aarchbio/blast@sha256:377bfb5dc686ed9df1ce95f31835225c573bb6b75c17b0da80dfeb0c3b747b0c
spawn_version: 0.111.4
last_verified: 2026-10-01
---
# BLAST+ — 1000 proteins against the whole human proteome, and the identity that scale broke

Searches 1000 query proteins against all 382,428 Ensembl 116 peptides in 156 s. For anyone sizing a homology search, or choosing between BLAST+ and DIAMOND.

> **[DIAMOND does the same search in 19 s](../diamond/README.md)** — 8.2× faster for 0.43% of the self-hits. This page is the reference the fast tool is measured against.

## Run it

```bash
make stage RECIPE=blast   # Ensembl 116 proteome + the first 1000 records as queries
spawn task run --spec "$(make -s spec RECIPE=blast)" --wait   # makeblastdb then blastp, ~4 min total
make ls    RECIPE=blast   # hits.tsv + smoke-check.txt

makeblastdb -in pep.fa -dbtype prot -out pepdb
blastp -query queries1000.fa -db pepdb -max_target_seqs 500 -num_threads 16 \
  -outfmt '6 qseqid sseqid pident length qlen evalue bitscore' -out hits.tsv
```

## What it costs

| | search | $/search | hit rows |
|---|---|---|---|
| **BLAST+**, 16 threads | **156 s** | **0.0277** | 320,305 |
| [DIAMOND](../diamond/README.md) `--very-sensitive` | 19 s | 0.0034 | — |

**`-max_target_seqs 500`, not 20** — and that is not a performance choice. At 20 the report is
truncated before a query's own self-hit for 25 of 931 queries, which silently breaks the check
below. Raising it to 500 costs **2 seconds** (154 → 156) and restores the identity.

## Make it yours

| In the recipe | Swap for | What to know |
|---|---|---|
| the first 1000 proteome records | your own query FASTA | the queries being records *of the database* is what makes the self-hit identity available; your own queries lose that check. |
| `-max_target_seqs 500` | higher for deep families | too low silently drops self-hits, as above — it is a correctness knob here, not just a speed one. |
| 1000 queries | more | cost is near-linear in queries; 1000 is annotation scale and runs in under 3 minutes. |

**Leave the workload** — a real query set against a real proteome, so the timing and the
DIAMOND comparison both transfer. **Scale it** by query count, which is the axis that costs money.

<details>
<summary>As shipped: the identity scale broke, what replaced it, pins</summary>

### The checks

| observable | assertion | observed |
|---|---|---|
| database sequences | **exactly 382,428** | **382,428** |
| queries with any hit | ≥ 900 | **931** |
| **queries whose hit set contains itself** | **== queries with any hit** | **931** |
| **self-rows at 100.000% identity** | **== self-rows** | **931** |
| self-alignments spanning the whole query | observation | **829** |

### The identity that 20 queries hid

The earlier 20-query version of this page asserted that **every** query gets a full-length,
100%-identity self-hit that nothing outscores. At 1000 queries that is simply false: 931 of 1000
queries hit anything, and of those only **829** self-alignments span the full query — the other 102
stop **exactly one residue short**, every time.

So the claim had to be split, because two different things were bundled in it:

- **Exact and algorithmic:** a sequence is 100% identical to itself, so every reported self-row is
  at `pident 100.000`. Measured: 931 of 931. This is the assertion.
- **Data-dependent:** whether the local alignment extends to the final residue. 102 of 931 do not.
  Terminal proline is enriched 3.6× among them (50 of 102, against 137 of 1000 overall) — a clear
  signal, but the mechanism is not established here, so it is reported rather than explained.

A fixture of 20 well-behaved proteins made a false claim look exact for months. That is the
[assert-the-claim-you-mean](../../practices/cross-checks.md) rule failing in the direction that is
hardest to notice: the check passed.

Also dropped: "nothing outscores a self-hit". With truncated local alignments a paralog can
legitimately score higher than a one-residue-short self-alignment, so the exact-zero assertion was
measuring the same artifact.

### Pins

| | data tier |
|---|---|
| BLAST+ | `quay.io/aarchbio/blast@sha256:377bfb5d…` (2.16.0, `linux/arm64`) |
| proteome | Ensembl 116 `Homo_sapiens.GRCh38.pep.all.fa.gz`, sha256 `9b43da9265…` — versioned release |
| queries | its own first 1000 records, sha256 `e6732c4cf4…` — derived deterministically by the stage script |

### Run + verify

```sh
make run RECIPE=blast
make ls  RECIPE=blast
```

Expect `smoke-check.txt` with `db_sequences 382428`, `self_rows 931` and `self_rows_100pct 931`.

</details>
