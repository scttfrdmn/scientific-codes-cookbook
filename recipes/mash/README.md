# Mash — MinHash distance between two assemblies of the same reads

One task. Mash sketches the spades and megahit assemblies (both built from the *same*
30×-fixture reads) and reports their distance. The smoke check asserts the two are
near-identical — which they must be, since they're the same ~400 kb region assembled two
ways, and which cross-validates the assembly chain at the same time.

> **What this recipe does and does not cover.** It computes a MinHash distance between two
> real assemblies and confirms it's near zero — enough to prove Mash's sketch/dist works on
> Graviton4 and to cross-check `recipes/spades` against `recipes/megahit`. Not a benchmark;
> no large-scale genome database search.

## The identity, and why it's honest

`recipes/spades` (23 contigs) and `recipes/megahit` (2 contigs) assemble the **identical**
51,933 read pairs. Different assemblers lay the sequence out in different numbers of contigs,
but the underlying ~400 kb is the same, so their k-mer content is nearly identical and Mash's
distance sits near zero: **observed 0.000239895** (990/1000 shared hashes). Asserted **< 0.001** —
a threshold set by "two assemblies of one sequence share nearly all k-mers", not shaved to the
observed value; a broken sketch or a mismatched pair gives distance ≫ 0.1. This is also a free
cross-check on the assemblers: garbage from either would not sketch as near-identical.

**Cross-tool note (see `recipes/sourmash`).** Mash uses **bottom-sketch MinHash**; sourmash
uses **FracMinHash (scaled)**. They are different algorithms, so their *numbers* aren't the
same quantity (Mash reports a distance ≈ 0.0002; sourmash a Jaccard ≈ 0.995). The two agree
**qualitatively** — both call this pair near-identical — which is the honest cross-code claim;
asserting `mash_distance == sourmash_jaccard` would be comparing different statistics.

## Pins

| | |
|---|---|
| image | `quay.io/aarchbio/mash@sha256:abad0c5f4d3365661ffc5533bc6eb5f1bd07d773ea61b1abd1bfc00c1df813fe` (cosign-verified, `linux/arm64`) |
| inputs | `runs/spades/r1/spades_contigs.fa` + `runs/megahit/r1/megahit_contigs.fa` — produced by the sibling assembly recipes (an S3 chain, run those first) |

**Data tier: derived — sibling-recipe outputs.** No `stage-inputs.sh`; the assemblies come
from `recipes/spades` and `recipes/megahit`, which assemble the reused 30×-fixture reads.

## Smoke check

| observable | assertion | observed |
|---|---|---|
| **mash distance** | < 0.001 (same region, two assemblers) | 0.000239895 |
| shared hashes | high | 990/1000 |

## Resources

2 vCPU / 4 GiB, `c8g` (resolves to `c8g.large`), TTL 5m, cap $0.02. Sketch + dist is
sub-second; boot + image pull dominate — **not compute cost**. Recorded window **43s**
(21:48:27 → 21:49:10 UTC). TTL/cap already minimal.

**Local vs launch, and why the threshold is right.** Local validation (against a spades
assembly built without `--isolate`) measured distance **0.000168**; the real run, against
the shipped spades output (`--isolate`, 237 contigs), measured **0.000239895**. Both are
`< 0.001` — the exact value shifts with the assembler's fragmentation but the k-mer distance
doesn't, which is exactly why the threshold is set by "same underlying sequence" and confirmed
from the real run, not shaved to a local number.

## Running it

Run `recipes/spades` and `recipes/megahit` first (they publish the contigs this reads), then:

```sh
spawn task run --spec recipes/mash/01-dist.task.json --wait
aws s3 ls s3://scicookbook-942542972736-us-east-1/runs/mash/r1/
```

Expect `mash-dist.txt` and `smoke-check.txt`. **Re-running:** bump the `-r1` suffix in
`task_id` and the output prefix.
