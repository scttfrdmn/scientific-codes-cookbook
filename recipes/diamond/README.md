---
tool: diamond
tool_version: 2.2.6
image: quay.io/aarchbio/diamond@sha256:6356f5b243c35bf7fd8a9b8f3962f96c2693d8d593cba90de21fa508c6ce79f5
spawn_version: 0.104.0
---
# DIAMOND — fast protein search, cross-checked against BLAST+

The accelerator you reach for when BLAST+ is too slow — same job, heuristic speed.

## Run it

```bash
diamond makedb --in proteome.fa -d db
diamond blastp -q queries.fa -d db --very-sensitive -o hits.tsv
```

The recipe searches the same 20 queries against the same Ensembl 116 human proteome as [blast](../blast/README.md), then checks that the accelerator and the reference recover the same self-hits on the same bytes.

## Make it yours

| In the recipe | Swap for | What to know |
|---|---|---|
| the Ensembl 116 proteome DB (reused from blast) | your own reference proteome | `diamond makedb` reads gzip directly, unlike `makeblastdb`. |
| queries = first 20 DB records | your own queries | same load-bearing trick as blast — guarantees an exact self-hit to assert against. |
| **`--very-sensitive`** | keep it for a fair comparison | scaffolding that **must stay** for the cross-check: DIAMOND's default `fast` mode is far less sensitive than `blastp` default, so comparing them would compare *modes*, not correctness. For your own work, pick the sensitivity your search needs. |

DIAMOND is deterministic — **nothing here is determinism scaffolding** (its arbitrary tie order is a comparison subtlety, handled below). **Leave the DB full-size:** the real proteome and the byte-identical reuse of blast's inputs are what make the cross-check meaningful.

## Shape, size, cost

One task. `c8g.xlarge` (4 vCPU), ~$0.02, **~63s** wall. Local work: `makedb` 15 s, `blastp --very-sensitive` 11 s — DIAMOND is lean (270 MB DB, no memory pressure). Boot + pull dominate ([why](../../practices/container-path.md)). Inputs come from [blast](../blast/README.md) — stage those first.

<details>
<summary>As shipped: the self-hit identity, the like-with-like cross-code metric, pins, smoke check</summary>

Queries are the first 20 DB records (blast's design, reused byte-for-byte), so each has a guaranteed exact self-hit — algorithmic, not empirical, so exact assertions with no band.

**The cross-code metric, made like-with-like** — two ways a naive comparison would lie, both avoided:
- **Mode:** `--very-sensitive` puts DIAMOND in a regime comparable to `blastp` default (the same error as bowtie2 end-to-end vs `--local`).
- **Metric:** "does DIAMOND's #1 subject equal BLAST+'s #1" scores **1/20** — but that's not disagreement: these first-20 proteins are near-identical paralogs, so each self-hit **ties on bitscore** (19/20 exact ties, 0 real losses) and each tool breaks the tie its own way; DIAMOND's heuristic bitscores also differ from blastp's by design. So the asserted claim is **tie-agnostic and score-agnostic: both tools recover all 20 full-length 100% self-hits, none beaten** — the shared algorithmic truth. ([compare like with like](../../practices/cross-checks.md); the 1/20 → 20/20 fix is the protein-search sibling of minimap2's 0.43 → 0.9921.)

| observable | assertion | observed |
|---|---|---|
| diamond self-hits / beaten / queries hit | 20 / 0 / 20 | 20 / 0 / 20 |
| blast self-hits / beaten (cross-code) | 20 / 0 | 20 / 0 |

**Pins.** Image `quay.io/aarchbio/diamond@sha256:6356f5b243c3…` (2.2.6, cosign-verified, `linux/arm64`). Proteome + queries reused from blast (`sha256:9b43da92…` / `6b43cff7…`); blast reference `runs/blast/r1/hits.tsv`.

**Run + verify.**
```sh
spawn task run --spec recipes/diamond/01-search.task.json --wait
aws s3 ls s3://scicookbook-942542972736-us-east-1/runs/diamond/r1/   # expect dmnd_hits.tsv, smoke-check.txt
```
Re-running: bump the `-r1` suffix.

</details>
