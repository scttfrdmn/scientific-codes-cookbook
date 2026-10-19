---
tool: spades
tool_version: 4.3.0
image: quay.io/aarchbio/spades@sha256:f8b7ad9acda742d695be9176c1fec0e9a33579a6a19294d3d2a3516ade3de81c
last_verified_x86_64: 2026-10-13
images_x86_64:
  - quay.io/biocontainers/spades@sha256:90a6d8d2fef8eb932e2e2712ed2ef797cce7117f7dd0403cc6e62c349408ba9a
spawn_version: 0.104.0
last_verified: 2026-09-10
---
# SPAdes — de novo genome assembly

Assemble short reads into contigs with no reference — the standard bacterial/small-genome assembler.

## Run it

```bash
spawn task run --spec "$(make -s spec RECIPE=spades)" --wait
spades.py --isolate -t 4 -1 reads_1.fq.gz -2 reads_2.fq.gz -o out
```

The recipe assembles a ~400 kb region and asserts the exact contig set; [QUAST](../quast/README.md) then measures this assembly (and [MEGAHIT's](../megahit/README.md)) independently.

## Make it yours

| In the recipe | Swap for | What to know |
|---|---|---|
| the 30× region reads (51,933 pairs) | your own reads | `--isolate` is the mode for high-coverage isolate data; use `--meta`, `--rna`, etc. for other libraries. |
| **`-t 4` (fixed)** | scale threads for speed — but then drop the exact assertion | **determinism scaffolding — the load-bearing knob.** SPAdes at a *fixed* thread count is deterministic (verified: 237 contigs / 428,168 bp on two runs); change the thread count and the exact numbers can shift, so assert a band or pin the count. Same discipline as [flye](../flye/README.md) and the tree-builders. |

**Leave the fixture small:** a subsampled region assembles deterministically and lets the check assert exact integers in two minutes. The whole-genome run is [measured separately](../../measurements/spades-real/README.md), below. Leave-it.

## Shape, size, cost

One task, ~74 s assembly. `c8g.xlarge`, ~$0.02, **~113s** wall — boot and image pull ([why](../../practices/what-this-does-not-cover.md)). First in a chain: run SPAdes + [MEGAHIT](../megahit/README.md) before [QUAST](../quast/README.md).

**The scale-it, measured.** On a real bacterial genome (~100× *E. coli*, measured on `m8g.2xlarge` (8 vCPU / 32 GiB) — oversized so SPAdes wasn't the bottleneck, *not* the recommendation) SPAdes holds a **~4.4 GiB** working set and averages **3.4 of 8 cores**, peaking briefly at 8. So `c8g.xlarge` (4 vCPU / 8 GiB) fits — 8 GiB clears the floor, 4 cores match the average; the measurement box stranded ~28 GiB and ~5 cores, and the 8-core peak would tempt you to strand more. On the *same reads* **MEGAHIT assembles in ~0.4 GiB — ~11× leaner** ([why](../megahit/README.md)); the succinct de Bruijn graph is the memory win. **Neither needs a memory (`r`) box at this scale** — the choice is leaner-vs-heavier, not a family jump. (Both climb with genome size; this is bacterial-scale.) Measured across five families for $/result — and [why the cross-arch number is a trap](../../patterns/cost-per-result.md).

## Which box — [measured](../../measurements/spades-real/README.md) (whole *E. coli* isolate, 97×, `-t 8`)

| chip | instance | SPAdes wall | $/hr | compute $ | **billed $/result** |
|---|---|---|---|---|---|
| **Graviton4** | `c8g.2xlarge` | 1,182 s | 0.3190 | 0.1047 | **0.1101** |
| Xeon 6975P-C | `c8i.2xlarge` | 1,057 s | 0.3748 | 0.1101 | 0.1155 |
| EPYC 9R45 | `c8a.2xlarge` | 1,023 s | 0.4311 | 0.1225 | 0.1276 |

**Graviton4 is cheapest per assembly; AMD is 1.16× faster and 16% dearer.** All three give the same `contigs.fasta` byte for byte: 98.6% of the closed reference, 1.11 mismatches per 100 kbp. **Put `-o` on disk, not `/tmp`:** the work directory peaks at **18 GB** against 4.4 GiB of RAM and ran out of space at K77 on a 16 GiB box's tmpfs.

<details>
<summary>As shipped: the deterministic identity, pins, smoke check</summary>

SPAdes at `-t 4 --isolate` is **deterministic** — verified by assembling the same reads twice for an identical contig set — so the recipe asserts exact integers; a nondeterministic or broken assembly changes them. SPAdes and MEGAHIT produce *different* contig sets on the same reads (different algorithms, expected) — they're **not** compared to each other, each is measured independently by [QUAST](../quast/README.md).

| observable | assertion | observed |
|---|---|---|
| contigs | exactly 237 (`-t 4`, deterministic) | 237 |
| total length | exactly 428168 bp | 428168 |

Exact-or-wrong: a deterministic function of fixed reads + fixed threads.

**Pins.** Image `quay.io/aarchbio/spades@sha256:f8b7ad9acda7…` (4.3.0, cosign-verified, `linux/arm64`). Reads: `inputs/highcov/HG00096.chr20_2.0-2.4Mb.30x_reads_{1,2}.fq.gz` — `samtools fastq` of the shared 30× fixture (`make stage RECIPE=bcftools`) — repinned to that reproducible derivation, since the prior sha256 came from an unrecorded command.

### On x86_64

The same task on same-generation Intel (`c8i.xlarge`), from bioconda's own x86 build of the same
version — `quay.io/biocontainers/spades@sha256:90a6d8d2…` (4.3.0, `linux/amd64`; not
cosign-signed, so the digest is the pin). Same staged reads, same `--isolate -t 4`:

| | arm64 | x86_64 |
|---|---|---|
| contigs / total bp | 237 / 428168 | 237 / 428168 |
| `contigs.fasta` sha256 | `2f979141…` | `2f979141…` — **byte-identical** |

Four threads and two architectures, one assembly, bit for bit — the determinism the exact
assertion relies on is not a property of the chip.

**Run + verify.**
```sh
make run RECIPE=spades                  # Graviton4
make run RECIPE=spades ARCH=x86_64      # same spec, x86 pin, writes runs-x86_64/
make ls RECIPE=spades   # expect contigs.fasta, smoke-check.txt
```
[QUAST](../quast/README.md) reads `contigs.fasta` from this prefix. Re-run: `make run` launches a fresh task each time and overwrites this prefix — no spec edit needed.

</details>
