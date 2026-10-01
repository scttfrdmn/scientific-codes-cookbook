---
tool: kallisto
tool_version: 0.52.0
image: quay.io/aarchbio/kallisto@sha256:b8f0e24c8a014b202f7ef9eeafffd4a6fa1cd27ac4214850cd65b0b1f684f18d
spawn_version: 0.111.4
last_verified: 2026-09-30
---
# kallisto — a complete RNA-seq run quantified, and how it compares to salmon

Pseudoaligns the full 15.8M-fragment ERR188026 run against all 465,769 Ensembl 116 transcripts, and agrees with salmon on abundance rank to 0.9083. For anyone choosing a quantifier.

> **salmon is 3.1× faster and 3.1× cheaper on the same reads** (74 s against 231 s), and builds its index in 50 s where kallisto takes 491. The two agree on the science; the cost difference is the reason to choose.

## Run it

```bash
make stage RECIPE=salmon     # shares salmon's cDNA + reads; nothing kallisto-specific to stage
make run   RECIPE=kallisto   # index (~8 min, once) then quant (~4 min) + the salmon cross-check
make ls    RECIPE=kallisto   # abundance.tsv + smoke-check.txt

kallisto index -i kallisto.idx ensembl116_cdna.fa.gz
kallisto quant -i kallisto.idx -o out -t 16 ERR188026_1.fastq.gz ERR188026_2.fastq.gz
```

## Next to salmon — same reads, same reference, same box

| | index build | index size | quant (16t) | **$/quant** | mapped |
|---|---|---|---|---|---|
| [salmon](../salmon/README.md) | **50 s** | 1.6 GiB | **74 s** | **0.01312** | 94.39% |
| kallisto | 491 s | **0.87 GiB** | 231 s | 0.04094 | 91.8% |

Both on `c8g.4xlarge` at 16 threads, both reading 15,800,127 fragments. **salmon wins on time and money
at every step**; kallisto's only edge is a smaller index. The abundances agree (below), so this is a
cost decision, not an accuracy one — and if you already have kallisto in a pipeline, the agreement is
your evidence that switching will not move your results.

## Make it yours

| In the recipe | Swap for | What to know |
|---|---|---|
| `ERR188026` | your FASTQs | one sample per task; a cohort is this task [fanned out](../../patterns/job-arrays.md) against the one index. |
| Ensembl 116 cDNA | your transcriptome | the index is built once and reused — 491 s is a one-time cost, not per sample. |
| `-t 16` | fewer threads | quant is ~4 min at 16; the data path, not the chip, is the limit at this size. |

**Leave the workload** — a complete run against a complete transcriptome, so the timings and the
agreement both transfer. **Scale it** by fanning samples out against the one index.

<details>
<summary>As shipped: the exact identity, the right cross-tool metric, pins</summary>

### The checks

| observable | assertion | observed |
|---|---|---|
| `sum(TPM)` | **exactly 1,000,000** — per-million by construction | **1000000** |
| fragments processed | **15,800,127** — same count salmon read | **15,800,127** |
| transcripts in output | 465,769 — every sequence gets a row | **465,769** |
| **Spearman vs salmon** | **≥ 0.85** on transcripts both detect | **0.9083** (n = 87,169) |
| pseudoaligned | recorded | **91.8%** |

`sum(TPM) == 1e6` is exact and free, so a table truncated on its zero tail fails it even though a row
count would pass — the same identity [salmon](../salmon/README.md) asserts. And **fragments processed
must equal salmon's exactly**, because it is a property of the input, not of the model: if the two
disagree there, one of them did not read the file you think it did.

### Rank, not raw TPM — and why that is not just a convention

salmon and kallisto use different effective-length and multimapping models, so their absolute TPMs are
not the same quantity. The honest comparison is **rank order on transcripts both tools detect**, and
the reason to prefer it is that it is *stable*:

| fragments | Spearman (both detected) | log-Pearson (both detected) | log-Pearson (all 465,769) |
|---|---|---|---|
| 200,000 | **0.9120** | 0.9118 | 0.7871 |
| 15,800,127 | **0.9083** | 0.9656 | 0.9214 |

Spearman moves **0.004 across a 79× change in depth**. The raw-value correlation swings from 0.787 to
0.966 depending on sequencing depth and on whether you include transcripts only one tool detected —
a 0.18 range produced entirely by choices about the comparison rather than by the tools. That is what
makes rank the claim worth asserting: it answers a question both tools can answer, and it does not
move when you change the question slightly. Measured in
[measurements/quant-depth](../../measurements/quant-depth/README.md).

Note what is *not* a cross-tool identity: kallisto reports 465,769 targets (every sequence in the
FASTA) while salmon's table has 453,553 rows, because salmon filters duplicate and short sequences.
Asserting those equal would assert a filtering policy, not a result.

### Pins

| | data tier |
|---|---|
| kallisto | `quay.io/aarchbio/kallisto@sha256:b8f0e24c…` (0.52.0, cosign-verified, `linux/arm64`) |
| transcriptome | Ensembl 116 `Homo_sapiens.GRCh38.cdna.all.fa.gz` — staged by [salmon](../salmon/README.md) |
| reads | ENA `ERR188026_{1,2}.fastq.gz` — the complete run, staged by salmon |
| salmon's table | `runs/salmon/r1/quant-c8g-16t.sf` — the cross-check reads the real run's output |

Nothing is staged twice: the cDNA, the reads and salmon's own quant table are the same objects salmon
produced, because the comparison only means something on identical bytes.

Two things about this image that cost a run to learn elsewhere and apply here: it has **no `python3`
and no `jq`**, so `run_info.json` is parsed with awk and the correlation is computed with
`awk`+`sort`+`join`; and the index build is the memory-bound step, which is why it runs on `r8g` while
the quant runs on `c8g`.

### Run + verify

```sh
make run RECIPE=kallisto
make ls  RECIPE=kallisto
```

Expect `smoke-check.txt` with `tpm_sum 1000000`, `fragments_processed 15800127` and
`spearman_vs_salmon 0.9083`.

</details>
