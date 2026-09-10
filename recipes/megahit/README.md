---
tool: megahit
tool_version: 1.2.9
image: quay.io/aarchbio/megahit@sha256:d82953bf0096098b0b892edf7180f99b599e8ad17be14c47b1e8c2e1b6a8bdfd
spawn_version: 0.104.0
---
# MEGAHIT — de novo assembly

A fast, memory-lean assembler — the go-to when SPAdes is too heavy for the data.

## Run it

```bash
megahit -t 4 --min-count 2 -1 reads_1.fq.gz -2 reads_2.fq.gz -o out
```

The recipe assembles the **same reads [SPAdes](../spades/README.md) uses** and asserts the exact contig set; [QUAST](../quast/README.md) measures it alongside SPAdes' as the independent cross-validator.

## Make it yours

| In the recipe | Swap for | What to know |
|---|---|---|
| the 30× region reads (51,933 pairs) | your own reads | MEGAHIT is happiest on large/metagenomic sets where its low memory footprint pays off. |
| **`-t 4` (fixed)** | scale threads — but then drop the exact assertion | **determinism scaffolding** — MEGAHIT at a fixed thread count is deterministic (verified: 2 contigs / 400,811 bp twice); vary it and the exact numbers can move, so band it or pin. Same discipline as [SPAdes](../spades/README.md) and [flye](../flye/README.md). |

**Leave the fixture small:** a subsampled region assembles deterministically and the check asserts exact integers. Leave-it. (MEGAHIT recovers this ~400 kb as **2** long contigs where SPAdes fragments it into 237 — two correct assemblers, legitimately different, which is why QUAST measures each *independently* rather than comparing them.)

## Shape, size, cost

One task, ~10 s assembly. `c8g.xlarge`, ~$0.02, **~58s** wall — boot and image pull ([why](../../practices/container-path.md)). Run before [QUAST](../quast/README.md).

<details>
<summary>As shipped: the deterministic identity, pins, smoke check</summary>

MEGAHIT (`-t 4 --min-count 2`) is **deterministic** — verified by assembling the same reads twice for an identical result — so the recipe asserts exact integers. It and SPAdes produce different contig sets on the same reads (different algorithms, expected and correct) — the like-with-like rule is why they're measured independently by [QUAST](../quast/README.md), not compared to each other.

| observable | assertion | observed |
|---|---|---|
| contigs | exactly 2 (`-t 4`, deterministic) | 2 |
| total length | exactly 400811 bp | 400811 |

**Pins.** Image `quay.io/aarchbio/megahit@sha256:d82953bf0096…` (1.2.9, cosign-verified, `linux/arm64`). Reads: `inputs/highcov/HG00096.chr20_2.0-2.4Mb.30x_reads_{1,2}.fq.gz` (the shared 30× fixture, same bytes SPAdes assembles; sha256-pinned).

**Run + verify.**
```sh
spawn task run --spec recipes/megahit/01-assemble.task.json --wait
aws s3 ls s3://scicookbook-942542972736-us-east-1/runs/megahit/r1/   # expect contigs.fa, smoke-check.txt
```
[QUAST](../quast/README.md) reads `contigs.fa` from this prefix. Re-running: bump the `-r1` suffix.

</details>
