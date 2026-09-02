# BLAST+ — protein search against the human proteome

One task. `makeblastdb` builds a protein database from all 382,428 Ensembl 116
human proteins, then `blastp` searches 20 queries against it. `makeblastdb` and
`blastp` are both BLAST+ in the same pinned image, so this is one tool and one task.

## The queries are drawn from the database on purpose

The 20 queries are the first 20 records **of the database itself**. That is not
laziness — it is what gives the smoke check a deterministic assertion instead of a
guessed threshold. Every query is guaranteed to find itself: a full-length
alignment at exactly 100.000% identity that nothing else can beat on bitscore,
because no alignment of a sequence scores higher than its alignment to itself.

That property is algorithmic, not empirical, so these are exact assertions with no
band at all — the strongest smoke check of the five genomics recipes.

**What a "best hit is itself" check would have gotten wrong.** The obvious version
of this assertion is flaky, and measurement caught it: for one of the 20 queries the
self-hit came back **second**, tied with a paralog on both bitscore (241) and
e-value (1.39e-83). BLAST breaks such ties arbitrarily. Asserting the self-hit ranks
first would have failed on 19 of 20 for reasons that say nothing about correctness.
The recipe asserts "nothing beats itself" instead — the same claim, stated correctly.
A flaky smoke check is worse than no smoke check, because it teaches people to
ignore failures.

## Pins

| | |
|---|---|
| image | `quay.io/aarchbio/blast@sha256:377bfb5dc686ed9df1ce95f31835225c573bb6b75c17b0da80dfeb0c3b747b0c` |
| | tag `2.16.0--h6a93c2d_5`, cosign-signed, manifest is `linux/arm64` only |
| database | Ensembl release-116 `Homo_sapiens.GRCh38.pep.all.fa.gz`, byte for byte |
| | `sha256:9b43da92651b35814597af6a8b18f500b768679a49fa4678224f384917ce7668` (23,319,936 B, 382,428 proteins) |
| queries | first 20 records of that same file |
| | `sha256:6b43cff7568a5dead256262b159fe4b1d7fd7f97128efbcc8613d4dbedaacfa9` (7,915 B) |

**Data tier: stable public source with a durable id.** Ensembl `release-116/` is an
immutable path, which is what makes it pinnable; `pub/current_*` is not and does not
qualify. This recipe shares its database file with `recipes/hmmer`, deliberately —
same pinned release, one fewer thing to keep in sync.

UniProt would have been the more natural protein database and was rejected: its
archived releases ship only tarballs and `current_release` is mutable, so it cannot
be pinned. Under this project's rule — if it can't be pinned it doesn't qualify,
drop a tier — Ensembl `pep.all` is the input.

## Smoke check

Measured in this image, on this input, before any launch.

| observable | assertion | observed |
|---|---|---|
| database sequences | exactly 382428 | 382428 |
| hit rows | 300–400 | **400** |
| queries with any hit | exactly 20 | 20 |
| full-length 100% self-hits | exactly 20 | 20 |
| self-hit beaten by another hit | exactly 0 | 0 |
| worst self-hit e-value | informational | 1.10e-76 |

Only the row count is banded, and only because `-max_target_seqs 20` caps it: 400 is
20 queries × 20 targets, i.e. every query saturated the cap. A query with fewer
homologs in a future release would return fewer rows without anything being wrong,
so the floor sits at 300.

`-max_target_seqs 20` rather than the default 500, or a tighter 5: at 5 the self-hit
for the tied query above sat at rank 2 of 5 and could plausibly be pushed out of the
list entirely by a few more paralogs, which would break an assertion that ought to
be about correctness. 20 is enough headroom that the self-hit cannot be crowded out.

## Resources, and what the timings mean

8 vCPU / 16 GiB, `c8g` (resolves to `c8g.2xlarge`), TTL 20m. Measured work: **54s** for `makeblastdb`, **2.8s**
for `blastp` on 4 threads.

**These timings are not compute cost.** Each task pays instance boot, image pull and
S3 staging before the tool starts — on recipe #1 that overhead was ~5 minutes
against 78 seconds of work. Boot dominates every recipe here; a 57-second recipe is
almost entirely boot.

Disk is trivial: 23 MiB input, ~160 MiB decompressed proteome, 299 MiB of database
files, against ~6.1 GiB usable. `makeblastdb` cannot read gzip, so the task
decompresses first; `gzip` is a base-image utility, not a second scientific tool.

## Running it

```sh
spawn task run --spec recipes/blast/01-search.task.json --wait
aws s3 ls s3://scicookbook-942542972736-us-east-1/runs/blast/r1/
```

`--wait` exiting 0 does **not** prove the outputs exist: a task whose declared
output fails to stage is still recorded `completed` / `exit_code: 0`
(spore-host/spawn#561). The smoke check runs *inside* the task, where it can fail
the task; the bucket listing is the second half of the same check.

**Re-running.** `task_id` is fixed, so a re-run overwrites the previous
`completion.json` and `command.log`. Bump the `-r1` suffix in both `task_id` and the
output prefix to keep both records.
