---
tool: diamond
tool_version: 2.2.6
image: quay.io/aarchbio/diamond@sha256:6356f5b243c35bf7fd8a9b8f3962f96c2693d8d593cba90de21fa508c6ce79f5
spawn_version: 0.111.4
last_verified: 2026-10-01
---
# DIAMOND — the same search as BLAST+, 8.2× faster, 0.43% of the hits lost

Searches 1000 proteins against all 382,428 Ensembl 116 peptides in 19 s, and recovers 99.57% of the self-hits BLAST+ finds. For anyone deciding whether BLAST+ is worth the wait.

> **19 s against BLAST+'s 156**, on the same queries, same database, same box — for 4 of 931 self-hits. That is the tradeoff, measured rather than asserted.

## Run it

```bash
make stage RECIPE=blast     # shares blast's proteome and query set
make run   RECIPE=diamond   # makedb then search + the BLAST+ cross-check, ~1 min
make ls    RECIPE=diamond   # dmnd_hits.tsv + smoke-check.txt

diamond makedb --in pep.fa -d pepdb
diamond blastp -q queries1000.fa -d pepdb --very-sensitive \
  --max-target-seqs 500 -p 16 -o dmnd_hits.tsv
```

## The tradeoff, measured on identical bytes

| | search | **$/search** | queries hit | self-hits found |
|---|---|---|---|---|
| [BLAST+](../blast/README.md) | 156 s | 0.0277 | 931/1000 | 931 |
| **DIAMOND** `--very-sensitive` | **19 s** | **0.0034** | 927/1000 | 927 |

**8.2× faster and cheaper for 0.43% of the self-hits** (set agreement 0.9957) — an easy trade at annotation scale; if you need every last homolog, 137 extra seconds is the price. **`--very-sensitive` is load-bearing**: DIAMOND's default is faster still and much less sensitive, so comparing *that* against blastp's default would measure two different questions — the [like-with-like](../../practices/cross-checks.md) trap this comparison exists to avoid.

## Make it yours

| In the recipe | Swap for | What to know |
|---|---|---|
| `--very-sensitive` | `--sensitive`, or default | faster and less sensitive; re-measure the agreement if you change it, because that is the number this page is about. |
| the shared 1000 queries | your own FASTA | both tools read the same staged objects, which is what makes the comparison mean anything. |
| `--max-target-seqs 500` | higher for deep families | too low drops self-hits, exactly as it does [for BLAST+](../blast/README.md). |

**Leave the workload** — the same real query set and proteome BLAST+ uses, so the ratio transfers.
**Scale it** by query count.

<details>
<summary>As shipped: the exact identity, why top-hit agreement is the wrong metric, pins</summary>

### The checks

| observable | assertion | observed |
|---|---|---|
| queries with any hit | recorded | **927** |
| **queries whose hit set contains itself** | **== queries with any hit** | **927** |
| **self-rows at 100% identity** | **== self-rows** | **927** |
| self-alignments spanning the whole query | observation | **820** |
| **self-hit set agreement with BLAST+** | **≥ 0.95** | **0.9957** |

The first two are the same exact identity [BLAST+](../blast/README.md) asserts, holding independently
in a completely different algorithm — a sequence is 100% identical to itself, and a tool that reports
a hit set for a query must find the query in it. The full-length figure is an observation for the same
reason it is there: 107 of 927 self-alignments stop short of the final residue.

### Why top-hit agreement would be the wrong metric

Two metrics that look obvious and both mislead:

- **"Does DIAMOND's #1 hit equal BLAST+'s #1?"** Many of these proteins are near-identical paralogs
  whose self-hit *ties* on bitscore, and each tool breaks ties in its own order. That measures
  tie-breaking, not agreement.
- **"Do the bitscores match?"** DIAMOND's are heuristic and differ from blastp's by design.

What both tools must agree on is the **set of self-hits recovered**, which is score-agnostic and
tie-agnostic: 927 of BLAST+'s 931, or 0.9957. The floor is 0.95 because two sensitivity regimes
aimed at the same question should not differ by more than a few percent; a DIAMOND run in default
(fast) mode would fall well below it, which is the point of asserting it.

### Pins

| | data tier |
|---|---|
| DIAMOND | `quay.io/aarchbio/diamond@sha256:6356f5b2…` (2.2.6, `linux/arm64`) |
| proteome and queries | staged by [blast](../blast/README.md) — same objects, sha256 `9b43da9265…` / `e6732c4cf4…` |
| BLAST+ hits | `runs/blast/r1/hits.tsv` — the cross-check reads the real BLAST+ run |

Nothing is staged twice, and `make run RECIPE=blast` must run first because the cross-check reads its
output.

### Run + verify

```sh
make run RECIPE=diamond
make ls  RECIPE=diamond
```

Expect `smoke-check.txt` with `diamond_self_rows 927` and `self_hit_set_agreement 0.9957`.

</details>
