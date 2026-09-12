---
tool: blast
tool_version: 2.16.0
image: quay.io/aarchbio/blast@sha256:377bfb5dc686ed9df1ce95f31835225c573bb6b75c17b0da80dfeb0c3b747b0c
spawn_version: 0.104.0
---
# BLAST+ — protein search against the human proteome

Build a protein database, search sequences against it — the canonical homology search.

## Run it

```bash
makeblastdb -in proteome.fa -dbtype prot -out db
blastp -query queries.fa -db db -outfmt 6 -max_target_seqs 20 -num_threads 8
```

The recipe builds a database from all 382,428 Ensembl 116 human proteins and searches 20 queries against it. `makeblastdb` and `blastp` are the same BLAST+ image, so it's one task.

## Make it yours

| In the recipe | Swap for | What to know |
|---|---|---|
| the whole Ensembl 116 human proteome as the DB | your own reference proteome | `makeblastdb` can't read gzip — decompress first (the recipe does). |
| **queries = the first 20 DB records** | your own queries | **load-bearing** — each query then must find itself at 100% identity, unbeatable: an algorithmic check, not a guessed threshold. Swap it and you lose that. |
| `-max_target_seqs 20` | scale to your needs | scaffolding that **stays** — 20 is deliberate headroom so a query's self-hit can't be crowded out by paralogs (at 5 it can). |

BLAST is deterministic, but ties break **arbitrarily**: assert "nothing beats the self-hit," never "ranks first." **Leave the DB full-size:** the real proteome is the point (a toy DB wouldn't exercise a real search), and it's shared with [hmmer](../hmmer/README.md) — one pinned release, one fewer thing to sync.

## Shape, size, cost

One task. `c8g.2xlarge` (8 vCPU), TTL 20m, cap $0.13. Measured work: `makeblastdb` **54 s**, `blastp` **2.8 s** on 4 threads. Boot + pull dominate even so; [a short task is mostly overhead](../../practices/what-this-does-not-cover.md).

<details>
<summary>As shipped: the self-hit identity, the flaky-check lesson, pins, smoke check</summary>

**The queries are the first 20 records of the database**, so every one is guaranteed to find itself: a full-length alignment at exactly 100.000% identity that nothing outscores, because no alignment of a sequence beats its alignment to itself. That's *algorithmic*, not empirical — exact assertions, no band, the strongest check in the genomics set.

**A "best hit is itself" check would have been flaky, and measurement caught it:** for one query the self-hit came back *second*, tied with a paralog on bitscore (241) and e-value (1.39e-83) — BLAST breaks ties arbitrarily. Asserting "ranks first" would have failed 19-of-20 for reasons unrelated to correctness. The recipe asserts **"nothing beats itself"** — the same claim, stated correctly. A flaky check is worse than none.

| observable | assertion | observed |
|---|---|---|
| database sequences | exactly 382428 | 382428 |
| hit rows | 300–400 (cap = 20×20) | 400 |
| queries with any hit | exactly 20 | 20 |
| full-length 100% self-hits | exactly 20 | 20 |
| self-hit beaten by another | exactly 0 | 0 |

Only the row count is banded (the `-max_target_seqs 20` cap; a query with fewer homologs in a future release returns fewer rows without anything being wrong).

**Pins.** Image `quay.io/aarchbio/blast@sha256:377bfb5dc686…` (2.16.0, cosign-signed, `linux/arm64` only). Database: Ensembl `release-116/Homo_sapiens.GRCh38.pep.all.fa.gz` (`sha256:9b43da92…`, 382,428 proteins) — an *immutable* release path (what makes it pinnable; `pub/current_*` isn't, and UniProt was rejected for the same reason). Queries: first 20 records of that file (`sha256:6b43cff7…`). Shared with [hmmer](../hmmer/README.md).

**Run + verify.**
```sh
make stage RECIPE=blast          # build DB + queries from Ensembl (public)
make run RECIPE=blast
make ls RECIPE=blast
```
Smoke check runs inside the task; bucket listing is the second half ([exit 0 isn't proof](../../practices/container-path.md)). Re-run: `make run` launches a fresh task each time and overwrites this prefix — no spec edit needed.

</details>
