---
tool: rsem
tool_version: "1.3.1"
images:
  - quay.io/aarchbio/rsem@sha256:76f449f796624084750956f93d40cc50e59184990e7fd82e36fed2837dd40847
  - quay.io/aarchbio/bowtie2@sha256:a6807f0611a1c276235f47d175471ebbaec863aa751a0c3771c324be57d8fc59
spawn_version: 0.111.1
last_verified: 2026-09-22
---
# RSEM — what EM buys you over counting the unambiguous reads

RSEM quantifies two overlapping transcripts on Graviton4 from an expression ratio planted before the reads existed, next to the unique-only count of the same data. For anyone quantifying transcripts where isoforms share sequence.

> **What this covers.** Two transcripts sharing an 800 bp block, 2000 × 100 bp reads at a planted 3:1 ratio, bowtie2 to the transcriptome and RSEM's EM. Not genome alignment, paired-end, gene-level aggregation across real isoforms, or `--star`/`--bowtie2` mode (this image ships no aligner — see below).

## Run it

```bash
rsem-prepare-reference txome.fa idx                    # no aligner needed for this step
bowtie2 --dpad 0 --gbar 99999999 --mp 1,1 --np 1 --score-min L,0,-0.1 \
        --no-unal -k 200 --sensitive -x txidx -U reads.fq -S aln.sam
rsem-calculate-expression --alignments --no-bam-output aln.bam idx out
```

Three tasks: RSEM builds the fixture and its reference, bowtie2 aligns to the transcriptome, RSEM runs the EM and the result is compared to the planted ratio and to unique-only counting.

## Make it yours

| In the recipe | Swap for | What to know |
|---|---|---|
| two synthetic transcripts | your transcriptome FASTA | add a GTF to `rsem-prepare-reference` for real isoform→gene mapping; without one, each FASTA entry is its own gene. |
| `-k 200` | — | **keep it.** Those are RSEM's own recommended bowtie2 settings; report fewer alignments per read and the EM has less to redistribute. |
| `--alignments` with your own BAM | `--bowtie2` / `--star` | **this image ships no aligner**, so the BAM must be made separately. If you do use RSEM's built-in mode, it must align to the *transcriptome*, not the genome. |
| comparing `expected_count` | `TPM` | pick one deliberately — they answer different questions and differ by the effective length, as measured below. |

**Leave the fixture:** a planted 3:1 with deliberately lopsided unique regions is what makes the EM's advantage measurable rather than asserted. **Scale it** to a real transcriptome whenever; the identities below hold at any size.

## Shape, size, cost

Three tasks on `c8g.large` (2 vCPU / 4 GiB), TTL 12m each, caps $0.05 each. Every step is seconds at this size; the windows are almost entirely image pull. **These timings are not compute cost.**

<details>
<summary>As shipped: three exact identities, the naive method inverting the answer, why TPM ≠ the read ratio, the missing aligner, pins</summary>

### The checks

| observable | assertion | observed |
|---|---|---|
| fixture | txA 900 bp, txB 1600 bp, 2000 reads, planted ratio exactly 3 | **holds** |
| reference | 2 transcripts | **2** |
| alignment | all reads aligned; multi-mappers > 0 | **100%**, 1570 multi |
| `Σ expected_count` | == the read count (**exact**) | **2000.00** |
| `Σ TPM` | == 1,000,000 (**exact**) | **1000000.00** |
| recovered ratio | within 2% of the planted 3.0 | **3.0201** |
| TPM ratio | == count ratio × `effB/effA` (**exact**) | **5.6593 vs 5.6594** |
| naive unique-only ratio | **< 1** while the planted ratio is **> 1** | **0.6412** |

### The naive method does not mis-estimate — it inverts the answer

78.5% of the reads (1570 of 2000) align to both transcripts, because the two share an 800 bp block. Count only the unambiguous ones and you get:

```text
uniquely aligned:  txA 168    txB 262     ratio 0.64
planted:           txA 1500   txB 500     ratio 3.00
RSEM (EM):         txA 1502.5 txB 497.5   ratio 3.02
```

Unique-only counting reports **txB as the more abundant transcript when txA has three times the reads.** The cause is structural, not statistical: txB carries **800 bp** of uniquely-mappable sequence against txA's **100 bp**, so it harvests unique reads far out of proportion to its abundance. Throwing away multi-mappers throws away a *biased* subset, and that bias is exactly what EM removes by assigning ambiguous reads in proportion to abundances estimated from all of the data.

The assertion is written as the claim that matters: not "the naive ratio is wrong by X%", but that it falls **on the wrong side of 1** while the planted ratio is above it. A percentage error would still pass if the ordering happened to survive; asserting the inversion says the thing a reader needs to know.

### Why `TPM` is not the read ratio, exactly

`expected_count` recovers 3.02 against a planted 3.00. `TPM` reports **5.66**. Both are right:

```text
effective_length   txA 801.00   txB 1501.00
TPM ratio  =  count ratio × (effB / effA)  =  3.0201 × 1501/801  =  5.6594
observed TPM ratio                                                  5.6593
```

TPM is a per-nucleotide, length-normalised quantity: the same number of reads off a longer transcript implies fewer molecules. So a reader who plants a **read** ratio and checks **TPM** sees 5.7 against 3.0 and concludes the tool is broken. The recipe asserts that relation as an identity rather than describing it, which turns the discrepancy into arithmetic — the same [compare like with like](../../practices/cross-checks.md) discipline the aligner and annotator recipes need.

The two conservation identities are the cheap ones and cost nothing: EM **redistributes** reads, so `Σ expected_count` must equal the aligned read count; and TPM is a share of a million by construction, so it must sum to 1e6. Both are exact, and the second is the same identity the [salmon](../salmon/README.md) recipe leans on.

### This image ships no aligner, and that is load-bearing

`rsem-calculate-expression --bowtie2` cannot run here: the image has **`samtools` but no `bowtie2`, `bowtie` or `STAR`**. So alignment is its own task and RSEM is invoked with `--alignments`, which also makes the pipeline honest about a detail RSEM's convenience mode hides — **the BAM must be aligned to the transcriptome, not the genome**, and with many alignments reported per read.

Two things that bit while wiring it, both worth the page:

- **The bowtie2 image has no `samtools` either.** A first pass converted SAM→BAM there with `samtools view -bS aln.sam > aln.bam 2>/dev/null`, which wrote a **0-byte BAM** and failed one task later as RSEM's `Fail to parse sam header!` — an error that blames the header of a file whose real problem is that it is empty, caused by a missing tool in a different task. The `2>/dev/null` was mine and it is what hid the cause. The SAM is now handed on as-is, converted in the RSEM task, and task 2 asserts the SAM is non-empty and carries both `@SQ` lines before it finishes.
- **bowtie2's index is built on `idx.transcripts.fa`**, the FASTA `rsem-prepare-reference` writes, not on the original — so the `@SQ` names and order cannot drift from what RSEM expects.

### Pins (data tier: synthetic / in-code)

| | |
|---|---|
| RSEM | `quay.io/aarchbio/rsem@sha256:76f449f7…` — package `1.3.3`, but the binaries report **`v1.3.1`** |
| bowtie2 | `quay.io/aarchbio/bowtie2@sha256:a6807f06…` — the same pin the [bowtie2](../bowtie2/README.md) recipe uses |
| input | none — both transcripts and all reads are generated in-task by awk from `srand(67)` |

The version mismatch is the usual reminder that [a package version is not the binary's version](../../practices/container-path.md); the page records what the tool says about itself.

Also: spawn's task shell does not inherit the image's `PATH`, so `/opt/conda/bin` must be exported in every task. And `awk` here defines its function at top level rather than inside `BEGIN`, because not every `awk` accepts the latter.

### Run + verify

```sh
make run RECIPE=rsem
make ls  RECIPE=rsem
```

Assertions are `test` calls and awk exits inside all three tasks. Expect `smoke-check.txt` with `count_conservation 2000.00`, `tpm_conservation 1000000.00`, `expected_count … ratio 3.0201`, and `naive_unique_only … ratio 0.6412`.

</details>
