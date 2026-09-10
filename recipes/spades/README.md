---
tool: spades
tool_version: 4.3.0
image: quay.io/aarchbio/spades@sha256:f8b7ad9acda742d695be9176c1fec0e9a33579a6a19294d3d2a3516ade3de81c
spawn_version: 0.104.0
---
# SPAdes — de novo genome assembly

Assemble short reads into contigs with no reference — the standard bacterial/small-genome assembler.

## Run it

```bash
spades.py --isolate -t 4 -1 reads_1.fq.gz -2 reads_2.fq.gz -o out
```

The recipe assembles a ~400 kb region and asserts the exact contig set; [QUAST](../quast/README.md) then measures this assembly (and [MEGAHIT's](../megahit/README.md)) independently.

## Make it yours

| In the recipe | Swap for | What to know |
|---|---|---|
| the 30× region reads (51,933 pairs) | your own reads | `--isolate` is the mode for high-coverage isolate data; use `--meta`, `--rna`, etc. for other libraries. |
| **`-t 4` (fixed)** | scale threads for speed — but then drop the exact assertion | **determinism scaffolding — the load-bearing knob.** SPAdes at a *fixed* thread count is deterministic (verified: 237 contigs / 428,168 bp on two runs); change the thread count and the exact numbers can shift, so assert a band or pin the count. Same discipline as [flye](../flye/README.md) and the tree-builders. |

**Leave the fixture small:** a subsampled region assembles deterministically and lets the check assert exact integers; a whole-genome assembly is a different, slower recipe and wouldn't make the tool *more* legible. Leave-it.

## Shape, size, cost

One task, ~74 s assembly. `c8g.xlarge`, ~$0.02, **~113s** wall — boot and image pull ([why](../../practices/what-this-does-not-cover.md)). First in a chain: run SPAdes + [MEGAHIT](../megahit/README.md) before [QUAST](../quast/README.md).

<details>
<summary>As shipped: the deterministic identity, pins, smoke check</summary>

SPAdes at `-t 4 --isolate` is **deterministic** — verified by assembling the same reads twice for an identical contig set — so the recipe asserts exact integers; a nondeterministic or broken assembly changes them. SPAdes and MEGAHIT produce *different* contig sets on the same reads (different algorithms, expected) — they're **not** compared to each other, each is measured independently by [QUAST](../quast/README.md).

| observable | assertion | observed |
|---|---|---|
| contigs | exactly 237 (`-t 4`, deterministic) | 237 |
| total length | exactly 428168 bp | 428168 |

Exact-or-wrong: a deterministic function of fixed reads + fixed threads.

**Pins.** Image `quay.io/aarchbio/spades@sha256:f8b7ad9acda7…` (4.3.0, cosign-verified, `linux/arm64`). Reads: `inputs/highcov/HG00096.chr20_2.0-2.4Mb.30x_reads_{1,2}.fq.gz` — `samtools fastq` of the shared 30× fixture (`recipes/bcftools/stage-inputs.sh`), pinned by sha256.

**Run + verify.**
```sh
spawn task run --spec recipes/spades/01-assemble.task.json --wait
aws s3 ls s3://scicookbook-942542972736-us-east-1/runs/spades/r1/   # expect contigs.fasta, smoke-check.txt
```
[QUAST](../quast/README.md) reads `contigs.fasta` from this prefix. Re-running: bump the `-r1` suffix.

</details>
