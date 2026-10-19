---
tool: quast
tool_version: 5.3.0
image: quay.io/aarchbio/quast@sha256:54122e645394aa741656c54ecdde8737b2ad8cc0ef6ead72392c1be8248ae692
last_verified_x86_64: 2026-10-13
last_verified_amd: 2026-10-18
images_x86_64:
  - quay.io/biocontainers/quast@sha256:a8d5771c90e4fb6daf32885c56ccece7523344c4e7cd02aa1601a75dff885c22
spawn_version: 0.104.0
last_verified: 2026-09-10
---
# QUAST — assembly quality metrics

Score an assembly — contig counts, N50, total length — the standard "how good is this assembly?"

## Run it

```bash
spawn task run --spec "$(make -s spec RECIPE=quast)" --wait
quast.py assembly_1.fasta assembly_2.fasta -o report
```

The recipe measures **both** the [SPAdes](../spades/README.md) and [MEGAHIT](../megahit/README.md) assemblies of the same reads and asserts each assembler's exact metrics as QUAST reports them — one independent tool measuring two.

## Make it yours

| In the recipe | Swap for | What to know |
|---|---|---|
| the two sibling assemblies | your own assembly (or several) | add `-r reference.fa` for reference-based misassembly analysis — this recipe is reference-free. |

QUAST is deterministic — **nothing here is determinism scaffolding**. **Leave it:** the inputs are two deterministic assemblies of a small region, so every metric is exact-or-wrong; a larger assembly wouldn't make QUAST more legible. Leave-it.

## Shape, size, cost

One task, a few seconds of compute. `c8g.large`, ~$0.02, **~73s** wall — boot and image pull ([why](../../practices/what-this-does-not-cover.md)). The join of a chain: run [SPAdes](../spades/README.md) + [MEGAHIT](../megahit/README.md) first.

**Sizing:** no family question — QUAST's metrics are light on any assembly; a larger assembly is a longer run, not a heavier box. Any 8g box fits.

<details>
<summary>As shipped: why QUAST rather than assembler-vs-assembler, pins, smoke check</summary>

Two assemblers on the same reads produce **different** contig sets by design — comparing them to each other would measure the algorithm difference, not correctness ([compare like with like](../../practices/cross-checks.md)). So one independent tool measures each, and the recipe asserts each assembler's own deterministic numbers. QUAST's default `# contigs` applies a ≥500 bp filter (fewer than the raw FASTA), so both the raw and filtered counts are asserted.

| metric (from `report.tsv`) | SPAdes | MEGAHIT |
|---|---|---|
| `# contigs (>= 0 bp)` raw | 237 | 1 |
| `# contigs` (≥500 bp) | 78 | 1 |
| N50 | 33380 | 400429 |
| Total length | 399846 | 400429 |

Exact-or-wrong — a deterministic function of two deterministic assemblies, measured independently.

**Pins.** Image `quay.io/aarchbio/quast@sha256:54122e645394…` (5.3.0, cosign-verified, `linux/arm64`). Inputs: `runs/spades/r1/contigs.fasta` + `runs/megahit/r1/contigs.fa` (derived — the two assembler outputs; a resumable S3 chain).

### On x86_64

The same task on same-generation Intel (`c8i.large`), from bioconda's own x86 build of the same
version — `quay.io/biocontainers/quast@sha256:a8d5771c…` (5.3.0, `linux/amd64`; not
cosign-signed, so the digest is the pin). It evaluates the **x86** SPAdes and MEGAHIT assemblies,
so the chain is one architecture end to end:

| | arm64 | x86_64 |
|---|---|---|
| SPAdes raw / ≥500 bp / N50 / total | 237 / 78 / 33380 / 399846 | 237 / 78 / 33380 / 399846 |
| MEGAHIT raw / ≥500 bp / N50 / total | 1 / 1 / 400429 / 400429 | 1 / 1 / 400429 / 400429 |
| `report.tsv` sha256 | `6dd5bb82…` | `6dd5bb82…` — **byte-identical** |

Unsurprising and still worth having: both upstream assemblies are byte-identical across
architectures, so QUAST measures the same bytes and reports them identically.

**Run + verify.**
```sh
make stage RECIPE=bcftools && make run RECIPE=spades && make run RECIPE=megahit   # quast evaluates their assemblies
make run RECIPE=quast
make run RECIPE=quast ARCH=x86_64      # same spec, x86 pin, reads the x86 assemblies, writes runs-x86_64/
make ls RECIPE=quast   # expect report.tsv, smoke-check.txt
```
Re-run: `make run` launches a fresh task each time and overwrites this prefix — no spec edit needed.

</details>
