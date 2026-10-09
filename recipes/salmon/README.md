---
tool: salmon
tool_version: "2.7.0"
image: quay.io/aarchbio/salmon@sha256:7134f5116644d29ab5b8fbc1c1199214842d7391094438ba6166e5631ecb7a5e
spawn_version: 0.111.1
last_verified: 2026-09-25
---
# salmon — a complete RNA-seq run against the whole human transcriptome

Quantifies a full **15.8M-read** Geuvadis run against all **453,553** Ensembl 116 transcripts on Graviton, with cost per result measured across four Graviton generations. For anyone quantifying RNA-seq.

> **Scope.** One complete run (`ERR188026`) and the entire Ensembl 116 cDNA set. Quantification only — no DE testing, no transcript assembly, no single-cell.

## Run it

```bash
make stage RECIPE=salmon   # once: Ensembl 116 cDNA + the full ERR188026 run
for s in $(make -s spec RECIPE=salmon); do spawn task run --spec "$s" --wait; done   # index 50 s, then quant ~1 min; self-terminating
make ls    RECIPE=salmon   # quant.sf + smoke-check.txt
```

```bash
salmon index -t ensembl116_cdna.fa.gz -i sidx -p 16 --ramLimit 8
salmon quant -i sidx -l A -1 ERR188026_1.fastq.gz -2 ERR188026_2.fastq.gz -o squant -p 16
```

## Which box — [measured](../../measurements/salmon-real/README.md) (same image, same bytes, 16 threads)

| generation | instance | quant wall | $/hr | compute $ | **billed $/result** | overhead |
|---|---|---|---|---|---|---|
| Graviton2 | `c6g.4xlarge` | 116 s | 0.5440 | 0.0175 | 0.0287 | 74 s |
| Graviton3 | `c7g.4xlarge` | 83 s | 0.5800 | 0.0134 | 0.0259 | 78 s |
| Graviton4 | `c8g.4xlarge` | 74 s | 0.6381 | 0.0131 | 0.0222 | 51 s |
| **Graviton5** | `c9g.4xlarge` | **57 s** | 0.6955 | **0.0110** | **0.0193** | **43 s** |

**Take the newest generation — it wins twice:** Graviton5 is **2.0× faster** than Graviton2, **33% cheaper per result billed**, and it *stages* faster too (overhead 74 s → 43 s, more network for the same 3.7 GB). But note **this job is overhead-dominated** — 43–78 s of boot and staging against 57–116 s of compute, so **40% of the bill is not salmon**, the opposite of [bwa](../bwa-samtools/README.md) at 89% compute. More cores buys nothing here; only a shorter data path does.

## Make it yours

| In the recipe | Swap for | What to know |
|---|---|---|
| `ERR188026` | your FASTQs — edit `stage-inputs.sh` | one sample per task; a cohort is this task [fanned out](../../patterns/job-arrays.md) against one shared index. |
| Ensembl 116 cDNA | your transcriptome / a different release | the index is **built once and reused** — 50 s, 1.68 GB. Rebuild only when the annotation changes. |
| `-p 16` | fewer cores | quant is ~1 minute at 16; the box is already faster than the data path, so more cores buys nothing. |

**Leave the workload** — a complete run against a complete transcriptome, so these numbers transfer to your samples. **Scale it** by fanning out samples against the one index, not by growing the box.

<details>
<summary>As shipped: the exact identities at full scale, why overhead dominates, pins</summary>

### The checks

| observable | assertion | observed |
|---|---|---|
| `sum(TPM)` | **exactly 1,000,000** — TPM is a per-million share | **1000000.00** on all four generations |
| `sum(NumReads)` | == `num_mapped` from salmon's own `meta_info.json` | **14,913,565** |
| reads processed | > 5M (a real run, not a slice) | **15,800,127** |
| mapping rate | recorded | **94.39%** |
| transcripts quantified | the whole annotation | **453,553** |

Both identities are **exact and cost nothing**. `sum(TPM) == 1e6` is true by construction, so a
`quant.sf` truncated on a zero-count tail fails it even though a row count would pass —
[the identity beats the band](../../practices/cross-checks.md). And `sum(NumReads)` equalling
salmon's independently-reported mapped count catches a mismatch between the table and the run
that produced it. Both held identically on Graviton 2, 3, 4 and 5 — the numerics are
generation-independent, which is what makes the timing comparison meaningful.

Peak RSS was **6.46–6.59 GiB** across all four generations: footprint is the index, not the chip.

### Why this job is overhead-dominated, and what that changes

Staging is **3.7 GB** — a 1.68 GB index tar plus 2.06 GB of reads — against 57–116 s of
compute. So the fixed cost is 40% of the bill, and two things follow:

- **More cores is not the lever.** At 16 threads salmon already finishes in about a minute. The
  [knee](../../patterns/sizing.md) is irrelevant; the data path is the whole story, which is the
  case [copy, mount, or share?](../../patterns/data-movement.md) exists for.
- **The generation still wins** — and partly *because* of the overhead, not despite it: newer
  instances have more network, so the same 3.7 GB stages in 43 s instead of 74. A generation
  step buys compute *and* bandwidth.

Contrast with [bwa](../bwa-samtools/README.md) on the same platform: there compute is 89% of
the bill and the recommendation is about cores. Same measurement method, opposite conclusion —
which is why each recipe carries its own table instead of inheriting a rule of thumb.

### The index is a separate task on purpose

`salmon index` takes **50 s** and produces a **1.68 GB** index; `salmon quant` consumes it. They
are two tasks because the index is built once and reused by every sample, so it should not be
rebuilt per run — and because a salmon index is a *directory*, which
[cannot be staged as such](../../practices/container-path.md), it travels as a flat
`salmon-index.tar` and is untarred by the consumer.

### Pins

| | data tier |
|---|---|
| salmon | `quay.io/aarchbio/salmon@sha256:7134f511…` (2.7.0) |
| transcriptome | Ensembl 116 `Homo_sapiens.GRCh38.cdna.all.fa.gz` — versioned release, copied byte for byte |
| reads | ENA `ERR188026_{1,2}.fastq.gz` — the complete run, 1.03 GiB per side |

Also, two things this image does **not** have, both of which cost a run to learn: no
`python3` and no `jq`, so `meta_info.json` is parsed with `awk`. And nothing in the pipeline may
stop reading early — a `sed … | head -1` on a small file usually wins the race and SIGPIPEs on a
large one, which is worse than failing outright, so there is no pipe there at all.

### Run + verify

```sh
make run RECIPE=salmon
make ls  RECIPE=salmon
```

Expect `smoke-check.txt` with `tpm_sum 1000000.00`, `numreads_sum 14913565`, and
`reads_processed 15800127`.

</details>
