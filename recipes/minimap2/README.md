---
tool: minimap2
tool_version: 2.31
image: quay.io/aarchbio/minimap2@sha256:ef4a5fb788815f5f9fd88544affa6764b5dacfc425aaf249a4adcd51416c041a
spawn_version: 0.104.0
---
# minimap2 — short-read alignment, cross-validated against bwa

The versatile aligner — here in its short-read mode, checked against bwa on identical reads.

## Run it

```bash
minimap2 -ax sr ref.fa reads_1.fq.gz reads_2.fq.gz > aln.sam
```

The recipe aligns the **same 400k read pairs bwa aligned** to the **same chr20**, then checks that where both aligners are confident, they place reads at the same locus — a two-aligner cross-check, not either tool's self-report.

## Make it yours

| In the recipe | Swap for | What to know |
|---|---|---|
| the 400k-pair HG00096 slice + chr20 (bwa's exact inputs) | your own reads + reference | reused byte-for-byte from [bwa](../bwa-samtools/README.md) — the cross-check only means something on identical input, so nothing is re-staged. |
| **`-ax sr`** | `-ax map-ont`, `-ax map-hifi`, `-ax splice`, … | scaffolding that **must match your data** — `sr` is the short-read preset; minimap2's presets are the whole point of the tool. Pick the one for your read type. |

minimap2 is deterministic on fixed input — **nothing here is determinism scaffolding**. **Leave the chr20 fixture:** unlike a mapping-*rate* claim (where chr20 would mislead, as it does for [STAR](../star/README.md)), the claim here is cross-aligner *concordance*, scoped to confident reads — a metric built to stay honest on chr20's repeat-heavy ties. Scaling wouldn't sharpen it.

## Shape, size, cost

One task, ~12 s of alignment. `c8g.xlarge`, ~$0.02, **~58s** wall — boot and image pull ([why](../../practices/what-this-does-not-cover.md)). Reuses [bwa](../bwa-samtools/README.md)'s staged inputs and output — run that first.

<details>
<summary>As shipped: the confident-concordance cross-check, pins, smoke check</summary>

minimap2 and bwa are independent seed-and-extend aligners; they agree overwhelmingly **where each is confident**, but *not* on every mapped read — most of these whole-genome reads don't belong on chr20 at all and both still report low-MAPQ placements in repeats, where they break ties differently. So the claim is scoped, not "same position for every read" (that would be the arbitrary-tie-break trap):

- **Confident concordance:** of reads *both* place at MAPQ ≥ 30, the fraction at the same locus (within 5 bp, absorbing soft-clip/indel-representation differences) is **0.9921** (19,892 / 20,051). Two unrelated aligners landing confident reads on the same base — the RAxML-NG/IQ-TREE cross-code move applied to alignment. Tolerance set by method (soft-clip/indel shift), not the observed value; a broken index collapses it far below 0.98.
- **Mapped count is reported, not equated:** minimap2 maps 169,183 primaries, bwa 233,036 — a legitimate preset-sensitivity difference, mostly low-MAPQ. Equating them would assert a coincidence; the concordance is the real claim. ([compare like with like](../../practices/cross-checks.md); the all-mapped-0.43 → confident-0.9921 fix.)

| observable | assertion | observed |
|---|---|---|
| minimap2 primary | 800000 (400k×2) | 800000 |
| minimap2 mapped | 100000–250000 (sensitivity, not = bwa) | 169183 |
| both confident (MAPQ≥30) | > 10000 | 20051 |
| confident concordance | ≥ 0.98 (same locus) | 0.9921 |

SAM parsed with `awk` arithmetic flag tests — no samtools/python in the image.

**Pins.** Image `quay.io/aarchbio/minimap2@sha256:ef4a5fb78881…` (2.31-r1302, cosign-verified, `linux/arm64`). Reads + reference are [bwa](../bwa-samtools/README.md)'s pinned `inputs/bwa-samtools/` bytes; cross-check against `runs/bwa-samtools/r1/aln.sam`. Reused — no `stage-inputs.sh`.

**Run + verify.**
```sh
make run RECIPE=minimap2
make ls RECIPE=minimap2   # expect mm.sam, smoke-check.txt
```
Re-running: bump the `-r1` suffix.

</details>
