# sourmash — FracMinHash similarity between two assemblies of the same reads

One task. sourmash sketches the spades and megahit assemblies (both from the *same*
30×-fixture reads) and reports their Jaccard similarity. The smoke check asserts they're
near-identical — the same ~400 kb region assembled two ways — cross-validating the assembly
chain, and pairing with `recipes/mash` as a second, independent MinHash tool on the same pair.

> **What this recipe does and does not cover.** It computes a FracMinHash similarity between
> two real assemblies and confirms it's ≈ 1 — enough to prove sourmash's sketch/compare works
> on Graviton4 and to cross-check `recipes/spades` against `recipes/megahit`. Not a benchmark;
> no taxonomic database (LCA/gather) work.

## The identity, and why it's honest

`recipes/spades` (23 contigs) and `recipes/megahit` (2 contigs) assemble the **identical**
51,933 read pairs, so they represent the same sequence and their k-mers nearly coincide:
sourmash Jaccard **observed 0.99282** (containment 99.5%). Asserted **≥ 0.95** — set by "two
assemblies of one sequence share nearly all k-mers", not shaved to the observed value; a broken
sketch or mismatched pair collapses far below.

**Cross-tool note (see `recipes/mash`).** sourmash uses **FracMinHash (scaled)**; Mash uses
**bottom-sketch MinHash**. Different algorithms → different statistics (sourmash: Jaccard
≈ 0.995; Mash: distance ≈ 0.0002), so the two agree **qualitatively** (both call this pair
near-identical), which is the honest cross-code claim — not raw-value equality across two
MinHash variants.

## Pins

| | |
|---|---|
| image | `quay.io/aarchbio/sourmash@sha256:29733e7ac937dd17d8c7b84130f36b41da1a33f02abab0b2276c92c2683abd10` (cosign-verified, `linux/arm64`) |
| inputs | `runs/spades/r1/spades_contigs.fa` + `runs/megahit/r1/megahit_contigs.fa` — sibling assembly recipes (S3 chain, run those first) |

**Data tier: derived — sibling-recipe outputs.** No `stage-inputs.sh`.

## Smoke check

| observable | assertion | observed |
|---|---|---|
| **sourmash Jaccard** | ≥ 0.95 (k=31, scaled=1000) | 0.99282 |

## Resources

2 vCPU / 4 GiB, `c8g` (resolves to `c8g.large`), TTL 5m, cap $0.02. Sketch + compare is
sub-second; boot + image pull dominate — **not compute cost**. Recorded window **59s**
(21:47:59 → 21:48:58 UTC). TTL/cap already minimal.

**Local vs launch:** local validation (spades built without `--isolate`) measured Jaccard
**0.9952**; the real run against the shipped spades (`--isolate`, 237 contigs) measured
**0.99282**. Both `≥ 0.95` — the exact value shifts with fragmentation, the k-mer Jaccard
barely does, which is why the threshold is justified by the shared sequence and confirmed from
the real run.

## Running it

Run `recipes/spades` and `recipes/megahit` first, then:

```sh
spawn task run --spec recipes/sourmash/01-compare.task.json --wait
aws s3 ls s3://scicookbook-942542972736-us-east-1/runs/sourmash/r1/
```

Expect `compare.csv` and `smoke-check.txt`. **Re-running:** bump the `-r1` suffix in `task_id`
and the output prefix.
