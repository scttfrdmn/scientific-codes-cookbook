---
tool: mash
tool_version: "2.3"
image: quay.io/aarchbio/mash@sha256:abad0c5f4d3365661ffc5533bc6eb5f1bd07d773ea61b1abd1bfc00c1df813fe
spawn_version: 0.104.0
---

# Mash — MinHash distance between two genomes

Sketch two sequences and get a distance without aligning them — the fast "how similar are these?" for whole genomes.

## Run it

```bash
mash sketch -o a genome_a.fa
mash sketch -o b genome_b.fa
mash dist a.msh b.msh          # → distance, and shared-hash count
```

The recipe sketches the [spades](../spades/README.md) and [megahit](../megahit/README.md) assemblies of the *same* reads and reports their distance — near zero, as it must be for one region assembled two ways.

## Make it yours

| In the recipe | Swap for | What to know |
|---|---|---|
| the two sibling assemblies (same 30× reads) | any two genomes/assemblies you want to compare | `mash dist` is symmetric and content-based; contig count doesn't matter, k-mer content does. |
| default sketch size (1000) | `-s` larger for finer resolution | bigger sketch = tighter estimate, more work; the default resolves this pair fine. |

Deterministic — no seed to pin. **Leave the fixtures small.** The point is a real distance between two real assemblies of a known-identical region; a larger genome pair would exercise the same `sketch`/`dist` and teach nothing new about correctness. Leave-it.

## Shape, size, cost

One task, sub-second. `c8g.large`, ~$0.02, **~43s** wall — boot and image pull dominate. Depends on [spades](../spades/README.md) + [megahit](../megahit/README.md) (an S3 chain — run those first).

<details>
<summary>As shipped: the near-zero identity, the honest cross-tool note, pins, smoke check</summary>

spades (237 contigs) and megahit (1 contig) assemble the **identical** 51,933 read pairs — different layouts of the same ~400 kb, so k-mer content nearly coincides and the distance sits near zero: **0.000239895** (990/1000 shared hashes). Asserted **< 0.001** — set by "two assemblies of one sequence share nearly all k-mers"; a broken sketch or mismatched pair gives ≫ 0.1. That also makes it a free cross-check on both assemblers.

**Cross-tool, stated honestly (see [sourmash](../sourmash/README.md)).** Mash uses **bottom-sketch MinHash**; sourmash uses **FracMinHash (scaled)** — different algorithms, so their numbers aren't the same quantity (Mash distance ≈ 0.0002 vs sourmash Jaccard ≈ 0.995). They agree **qualitatively** — both call this pair near-identical — which is the honest cross-code claim; asserting `mash_distance == sourmash_jaccard` would be comparing different statistics.

| observable | assertion | observed |
|---|---|---|
| mash distance | < 0.001 (same region, two assemblers) | 0.000239895 |
| shared hashes | high | 990/1000 |

The threshold is confirmed-from-the-real-run, not shaved: a local spades build (no `--isolate`, fewer contigs) measured 0.000168, the shipped one 0.000239895 — the value shifts with fragmentation, the k-mer distance barely does, which is why "same underlying sequence" sets the bound.

**Pins.** Image `quay.io/aarchbio/mash@sha256:abad0c5f4d33…` (cosign-verified, `linux/arm64`). Inputs: `runs/spades/r1/contigs.fasta` + `runs/megahit/r1/contigs.fa` (derived — sibling recipe outputs, staged locally as `spades_contigs.fa` / `megahit_contigs.fa`; no `stage-inputs.sh`).

**Run + verify.**
```sh
make stage RECIPE=bcftools && make run RECIPE=spades && make run RECIPE=megahit   # mash compares their assemblies
make run RECIPE=mash
make ls RECIPE=mash   # expect mash-dist.txt, smoke-check.txt
```
Re-run: `make run` launches a fresh task each time and overwrites this prefix — no spec edit needed.

</details>
