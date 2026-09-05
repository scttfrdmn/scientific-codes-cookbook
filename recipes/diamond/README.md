# DIAMOND — protein search against the human proteome, cross-checked against BLAST+

One task. `diamond makedb` builds a protein database from all 382,428 Ensembl 116 human
proteins, then `diamond blastp` searches the same 20 queries BLAST+ used. The smoke check
confirms an exact self-hit identity **and** that DIAMOND and BLAST+ — an accelerator and the
reference — recover the same self-hits, on the same bytes.

> **What this recipe does and does not cover.** It searches 20 proteins against the human
> proteome with DIAMOND and cross-checks the result against `recipes/blast` — enough to prove
> DIAMOND's DB build and `blastp` mode work on Graviton4 and agree with BLAST+. Not a
> benchmark; no all-vs-all or large query set.

## The queries are drawn from the database, and reused from BLAST+ byte for byte

The 20 queries are the first 20 records **of the database itself** — BLAST+'s design, reused
here so the cross-code check is on identical bytes. Every query therefore has a guaranteed
exact self-hit: full-length, 100% identity, and nothing can outscore it. That is a property
of the algorithm, not a measured threshold, so the assertions are exact, not bands — the
DIAMOND analog of `recipes/blast`'s self-hit identity.

## Cross-code, done like-with-like

DIAMOND is a heuristic accelerator of BLAST+, so two things would make a naive comparison
lie, and both are avoided:

- **Sensitivity mode.** DIAMOND's default is `fast`, far less sensitive than `blastp`'s
  default. Comparing them would compare *modes*, not correctness (the same error as bowtie2
  end-to-end vs `--local`). The recipe runs `diamond blastp --very-sensitive`, a regime
  comparable to `blastp` default, then compares.
- **The metric itself.** The obvious metric — does DIAMOND's #1 subject per query equal
  BLAST+'s #1 — scores **1/20** here. That is not disagreement: these first-20 proteins are
  near-identical paralogs, so each query's self-hit **ties on bitscore** with a paralog
  (measured: 19/20 are exact ties, 0 real losses), and each tool breaks the tie by its own
  arbitrary order — exactly the caveat `recipes/blast` documents ("nothing beats itself", not
  "self is first"). DIAMOND's heuristic bitscores also differ from `blastp`'s by design, so
  comparing raw scores is meaningless. **The asserted cross-code claim is therefore
  tie-agnostic and score-agnostic: both tools recover all 20 full-length 100% self-hits with
  none beaten** — the shared algorithmic truth. (This is CLAUDE.md's "compare like with like"
  rule; the 1/20 → 20/20 fix is the protein-search sibling of minimap2's 0.43 → 0.9921.)

## Pins

| | |
|---|---|
| image | `quay.io/aarchbio/diamond@sha256:6356f5b243c35bf7fd8a9b8f3962f96c2693d8d593cba90de21fa508c6ce79f5` |
| | tag `2.2.6--he0fd7ac_0`, DIAMOND 2.2.6, cosign-verified (`publish.yml@refs/heads/main`), `linux/arm64` |
| proteome | Ensembl release-116 `Homo_sapiens.GRCh38.pep.all.fa.gz`, byte for byte |
| | `sha256:9b43da92651b35814597af6a8b18f500b768679a49fa4678224f384917ce7668` (382,428 proteins) |
| queries | first 20 records of the proteome |
| | `sha256:6b43cff7568a5dead256262b159fe4b1d7fd7f97128efbcc8613d4dbedaacfa9` |
| blast reference | `runs/blast/r1/hits.tsv` (this recipe's cross-check counterpart) |

**Data tier: stable public source with a durable id — reused from `recipes/blast`.** Nothing
is staged by this recipe; run `recipes/blast/stage-inputs.sh` first if the objects aren't in
the bucket.

## Smoke check

Measured in the pinned image (`--user 1000:1000`).

| observable | assertion | observed |
|---|---|---|
| diamond self-hits | exactly 20 (full-length, 100% id) | 20 |
| diamond self beaten | exactly 0 (nothing outscores a self-hit) | 0 |
| diamond queries hit | exactly 20 | 20 |
| **blast self-hits** (cross-code) | exactly 20 (same self-hits recovered) | 20 |
| **blast self beaten** (cross-code) | exactly 0 (unbeaten in blast too) | 0 |

No bands — the self-hit is algorithmic, and the cross-code claim is the tie- and
score-agnostic truth both tools must satisfy.

## Resources, and what the timings mean

4 vCPU / 8 GiB, `c8g` (resolves to `c8g.xlarge`), TTL 5m, cap $0.02. `makedb` was
**15 s** and `blastp --very-sensitive` **11 s** locally — DIAMOND is lean (the DB is 270 MB and
the build showed no memory pressure, unlike BLAST+'s `makeblastdb`).

**These timings are not compute cost.** Boot, the Docker install, pulling the DIAMOND image,
and downloading the 23 MB proteome are much of the task. The recorded run's window was **63s**
(18:33:17 → 18:34:20 UTC), all 20 self-hits recovered by both tools, 0 beaten. TTL was
**retightened from that first real run**: 10m → **5m**, `cost_limit` $0.03 → $0.02; the recorded
run used the original 10m. Disk is modest (proteome + 270 MB DB).

## Running it

Inputs come from `recipes/blast` — stage those first if needed, then:

```sh
spawn task run --spec recipes/diamond/01-search.task.json --wait
```

Then **check the bucket**, every time:

```sh
aws s3 ls s3://scicookbook-942542972736-us-east-1/runs/diamond/r1/
```

The smoke check runs *inside* the task; the bucket listing is the second half of it. Expect
two objects (`dmnd_hits.tsv`, `smoke-check.txt`).

**Re-running.** `task_id` is fixed; bump the `-r1` suffix in both `task_id` and the output
prefix to keep both records.
