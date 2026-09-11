---
tool: sourmash
tool_version: 4.9.4
image: quay.io/aarchbio/sourmash@sha256:29733e7ac937dd17d8c7b84130f36b41da1a33f02abab0b2276c92c2683abd10
spawn_version: 0.104.0
---

# sourmash — FracMinHash similarity between two genomes

The same "how similar are these?" as Mash, by a different sketch — sourmash's scaled MinHash, which is what its taxonomy tooling is built on.

## Run it

```bash
sourmash sketch dna -p k=31,scaled=1000 genome_a.fa genome_b.fa
sourmash compare a.sig b.sig            # → Jaccard similarity matrix
```

The recipe sketches the [spades](../spades/README.md) and [megahit](../megahit/README.md) assemblies of the *same* reads and reports their Jaccard — ≈ 1, as it must be for one region assembled two ways.

## Make it yours

| In the recipe | Swap for | What to know |
|---|---|---|
| the two sibling assemblies | any genomes/signatures to compare | `compare` scales to many signatures at once; this recipe just uses two. |
| `k=31,scaled=1000` | your own k / a finer `scaled` | k sets resolution, `scaled` sets sketch density; k=31 is the standard bacterial/genome default. |

Deterministic — no seed. **Leave the fixtures small.** A real distance between two real assemblies of a known-identical region is the point; a bigger pair exercises the same `sketch`/`compare`. Leave-it. (For a *taxonomic* database search — LCA/gather — that's a different, larger recipe, deliberately not this one.)

## Shape, size, cost

One task, sub-second. `c8g.large`, ~$0.02, **~59s** wall — boot and image pull dominate. Depends on [spades](../spades/README.md) + [megahit](../megahit/README.md) (S3 chain — run those first).

<details>
<summary>As shipped: the ≈1 identity, the honest cross-tool note, pins, smoke check</summary>

spades and megahit assemble the **identical** 51,933 read pairs, so their k-mers nearly coincide: Jaccard **0.99282** (containment 99.5%). Asserted **≥ 0.95** — set by "two assemblies of one sequence share nearly all k-mers," not shaved to the observed value; a broken sketch or mismatched pair collapses far below. A free cross-check on both assemblers.

**Cross-tool, stated honestly (see [mash](../mash/README.md)).** sourmash uses **FracMinHash (scaled)**; Mash uses **bottom-sketch MinHash** — different algorithms → different statistics (Jaccard ≈ 0.995 vs distance ≈ 0.0002). They agree **qualitatively** (both call this pair near-identical), which is the honest cross-code claim — not raw-value equality across two MinHash variants.

| observable | assertion | observed |
|---|---|---|
| sourmash Jaccard | ≥ 0.95 (k=31, scaled=1000) | 0.99282 |

Threshold confirmed from the real run: a local spades build measured 0.9952, the shipped one 0.99282 — the value shifts with fragmentation, the k-mer Jaccard barely does, so "shared sequence" sets the bound.

**Pins.** Image `quay.io/aarchbio/sourmash@sha256:29733e7ac937…` (cosign-verified, `linux/arm64`). Inputs: `runs/spades/r1/spades_contigs.fa` + `runs/megahit/r1/megahit_contigs.fa` (derived — sibling outputs, no `stage-inputs.sh`).

**Run + verify.**
```sh
make stage RECIPE=bcftools && make run RECIPE=spades && make run RECIPE=megahit   # sourmash compares their assemblies
make run RECIPE=sourmash
make ls RECIPE=sourmash   # expect compare.csv, smoke-check.txt
```
Re-running: bump the `-r1` suffix.

</details>
