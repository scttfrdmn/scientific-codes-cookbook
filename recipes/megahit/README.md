---
tool: megahit
tool_version: 1.2.9
image: quay.io/aarchbio/megahit@sha256:d82953bf0096098b0b892edf7180f99b599e8ad17be14c47b1e8c2e1b6a8bdfd
spawn_version: 0.104.0
last_verified: 2026-09-10
---
# MEGAHIT — de novo assembly

A fast, memory-lean assembler — the go-to when SPAdes is too heavy for the data.

## Run it

```bash
megahit -t 1 --min-count 2 -1 reads_1.fq.gz -2 reads_2.fq.gz -o out
```

The recipe assembles the **same reads [SPAdes](../spades/README.md) uses** and asserts the exact contig set; [QUAST](../quast/README.md) measures it alongside SPAdes' as the independent cross-validator.

## Make it yours

| In the recipe | Swap for | What to know |
|---|---|---|
| the 30× region reads (51,933 pairs) | your own reads | MEGAHIT is happiest on large/metagenomic sets where its low memory footprint pays off. |
| **`-t 1` (fixed)** | scale threads — but then drop the exact assertion | **determinism scaffolding** — MEGAHIT's assembly depends on thread count (it lands on a different contig set at `-t 4`), so the recipe pins `-t 1` for a byte-identical result (verified across two runs: 1 contig / 400,429 bp). Vary it and the exact numbers move, so band it or pin. Same discipline as [flye](../flye/README.md) and [SPAdes](../spades/README.md). |

**Leave the fixture small:** a subsampled region assembles deterministically and the check asserts exact integers. Leave-it. (MEGAHIT recovers this ~400 kb as **1** long contig where SPAdes fragments it into 237 — two correct assemblers, legitimately different, which is why QUAST measures each *independently* rather than comparing them.)

## Shape, size, cost

One task, ~10 s assembly. `c8g.xlarge`, ~$0.02, **~58s** wall — boot and image pull ([why](../../practices/what-this-does-not-cover.md)). Run before [QUAST](../quast/README.md).

<details>
<summary>As shipped: the deterministic identity, pins, smoke check</summary>

MEGAHIT (`-t 1 --min-count 2`) is **deterministic at a fixed single thread** — verified by assembling the repinned reads twice for a byte-identical result — so the recipe asserts exact integers. At `-t 4` it lands on a *different* contig set (thread count changes the assembly), so `-t 1` is the determinism scaffolding, same as [flye](../flye/README.md). It and SPAdes produce different contig sets on the same reads (different algorithms, expected and correct) — the like-with-like rule is why they're measured independently by [QUAST](../quast/README.md), not compared to each other.

| observable | assertion | observed |
|---|---|---|
| contigs | exactly 1 (`-t 1`, deterministic) | 1 |
| total length | exactly 400429 bp | 400429 |

**Pins.** Image `quay.io/aarchbio/megahit@sha256:d82953bf0096…` (1.2.9, cosign-verified, `linux/arm64`). Reads: `inputs/highcov/HG00096.chr20_2.0-2.4Mb.30x_reads_{1,2}.fq.gz` (the shared 30× fixture, same bytes SPAdes assembles; `make stage RECIPE=bcftools` derives them — repinned to that reproducible build, since the prior sha256 came from an unrecorded command).

**Run + verify.**
```sh
make run RECIPE=megahit
make ls RECIPE=megahit   # expect contigs.fa, smoke-check.txt
```
[QUAST](../quast/README.md) reads `contigs.fa` from this prefix. Re-run: `make run` launches a fresh task each time and overwrites this prefix — no spec edit needed.

</details>
