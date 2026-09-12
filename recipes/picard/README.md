---
tool: picard
tool_version: 3.5.0
image: quay.io/aarchbio/picard@sha256:c6a742e8277b9010df9aa3b9a6bb40651792ff319627cc1c2bf8a70ac633e6bd
spawn_version: 0.104.0
---
# Picard — MarkDuplicates

Mark PCR/optical duplicates in a coordinate-sorted BAM and write a metrics file.

## Run it

```bash
picard MarkDuplicates -I aln.bam -O marked.bam -M dup_metrics.txt
```

The recipe marks duplicates in the shared 30× fixture BAM and asserts its **exact** duplication metrics. On a fixed BAM the tool is deterministic, so every number is exact-or-wrong — no band.

## Make it yours

| In the recipe | Swap for | What to know |
|---|---|---|
| the 30× fixture BAM (chr20:2.0–2.4 Mb) | your own coordinate-sorted BAM | reused from [bcftools](../bcftools/README.md)/[freebayes](../freebayes/README.md) — nothing new to stage. Input must be coordinate-sorted. |

Duplicate status is a function of alignment coordinates + flags, not run order, so MarkDuplicates is deterministic — **nothing here is determinism scaffolding**. **Leave the fixture:** a small BAM makes every metric exact-or-wrong and exercises Picard's JVM on Graviton; a full-genome dedup is a longer run and a bigger heap (the sizing question, measured below), not a more legible one. Leave-it.

## Shape, size, cost

One task, **~2 s** (JVM start dominates the compute). `c8g.large`, ~$0.02, **~76s** wall — boot and image pull ([why](../../practices/what-this-does-not-cover.md)).

**The scale-it, measured.** MarkDuplicates' size question is the JVM heap. On a chr1 100 Mb 30× slice (measured with a generous `-Xmx12g` so the heap wasn't the limit — *not* the recommendation) it **needed ~6.1 GiB** of live heap but the JVM **held ~11.9 GiB** (it commits toward `-Xmx`), running at **~1.2 cores**. Size from the *need*, not the RSS: take the 11.9 GiB at face value and you buy ~2× the memory **and** cores that sit idle — stranding both from one misread number. So `-Xmx` a few GiB over the live set on a low-core `m8g`-class box, not a big compute one. (Live heap grows with read count, so a full genome wants its own `-Xmx`; the held-vs-need gap is what transfers.)

<details>
<summary>As shipped: the exact metrics identity, pins, smoke check</summary>

`MarkDuplicates` on a fixed, coordinate-sorted BAM is deterministic, so the metrics reproduce exactly. No cross-code check: Picard has no natural sibling to run on the same bytes, so per [compare-like-with-like](../../practices/cross-checks.md) the exact metric is the honest claim rather than a manufactured comparison (same reasoning as [fastp](../fastp/README.md)'s conservation identity).

| observable | assertion | observed |
|---|---|---|
| read pairs examined | exactly 52189 | 52189 |
| unpaired reads examined | exactly 122 | 122 |
| **read-pair duplicates** | exactly 5012 | 5012 |
| unpaired-read duplicates | exactly 25 | 25 |
| **percent duplication** | exactly 0.096163 | 0.096163 |

`LC_ALL=C` silences a harmless Picard-wrapper locale warning. The staged BAM is never removed ([sticky-`/tmp` EPERM rule](../../practices/container-path.md)); the marked BAM stays on the box — the metrics are the evidence and the only staged output.

**Pins.** Image `quay.io/aarchbio/picard@sha256:c6a742e8277b…` (3.5.0, Java, cosign-verified, `linux/arm64`). Input: the shared 30× fixture `inputs/highcov/HG00096.chr20_2.0-2.4Mb.30x.bam` (+ `.bai`) — 1000G NYGC, chr20:2.0–2.4 Mb, mean 35.2×; [bcftools](../bcftools/README.md)'s `stage-inputs.sh` documents its provenance.

**Run + verify.**
```sh
make run RECIPE=picard
make ls RECIPE=picard   # expect dup_metrics.txt, smoke-check.txt
```
Smoke check runs inside the task; bucket listing is the second half ([exit 0 isn't proof](../../practices/container-path.md)). Re-run: `make run` launches a fresh task each time and overwrites this prefix — no spec edit needed.

</details>
