---
tool: stringtie
tool_version: "3.0.3"
images:
  - quay.io/aarchbio/hisat2@sha256:5a967484d6a941ddaba4b57f88ff892a579e0a18cd8c305b45cf9f49bacf10e8
  - quay.io/aarchbio/samtools@sha256:1191739637fb6f46ef97c02b28f693b25ca3ca61f90e1337f349b7b7cc0be4f7
  - quay.io/aarchbio/stringtie@sha256:e0eb6ef15f2c8d329c3b24381b7614b80e97f056dece5fbd2f4ec2f6121b6964
  - quay.io/aarchbio/gffcompare@sha256:9674f300816f52f289453f514c14ebcc8dd6ab1361b2f29ac1c1b283e66e3547
spawn_version: 0.111.1
last_verified: 2026-09-22
---
# Transcript assembly — a gene reassembled from its own spliced reads

hisat2 aligns transcript reads across splice junctions and StringTie rebuilds the transcript on Graviton4, scored by gffcompare against the exon structure the reads were generated from. For anyone assembling transcripts rather than counting against a reference.

> **What this covers.** A 12 kb contig, one 3-exon gene (900 bp mRNA), 30× 100 bp reads drawn from the spliced transcript. Junction discovery with no annotation supplied, de novo assembly, and scoring with gffcompare. Not multiple isoforms, alternative splicing, strand-specific libraries, `--merge` across samples, or quantification.

## Run it

```bash
for s in $(make -s spec RECIPE=transcript-assembly); do spawn task run --spec "$s" --wait; done
hisat2-build genome.fa idx
hisat2 -x idx -U reads.fq -S aln.sam            # finds junctions with NO annotation given
samtools sort -o sample.bam aln.sam && samtools index sample.bam

stringtie -o assembled.gtf -l ASM sample.bam    # de novo, no reference GTF
gffcompare -r truth.gtf -o gffcmp assembled.gtf # score it against the annotation
```

Four tasks: hisat2 builds the fixture and aligns, samtools sorts, StringTie assembles, gffcompare scores.

## Make it yours

| In the recipe | Swap for | What to know |
|---|---|---|
| one 3-exon gene | your genome + real RNA-seq | swap `truth.gtf` for a real annotation and gffcompare's numbers become the standard assembly report. |
| de novo `stringtie` | `stringtie -G annotation.gtf` | guided assembly finds known transcripts far more reliably; de novo is what you use when the annotation is the thing you doubt. |
| hisat2 | [STAR](../star/README.md) | either spliced aligner works; **an unspliced aligner ([bwa](../bwa-samtools/README.md), bowtie2) cannot produce this input at all** — it soft-clips at junctions and StringTie sees no introns. |
| 100 bp single-end | paired-end / longer reads | anchor length at the junction is what limits discovery: a read with 2 bp past the splice site is unalignable, spliced. |

**Leave the fixture:** one gene and two introns make the whole intron chain exactly assertable. **Scale it** to real data when you want sensitivity numbers — and read gffcompare's *intron chain* line before its base line, for the reason below.

## Shape, size, cost

Four tasks on `c8g.large` (2 vCPU / 4 GiB), TTL 15m / 12m / 12m / 12m, caps $0.05 each. `hisat2-build` on 12 kb and every other step are seconds; the windows are almost entirely image pull. **These timings are not compute cost.**

<details>
<summary>As shipped: an exact intron chain, why base sensitivity is the one number below 100, the assertions that are directional, pins</summary>

### The checks

| observable | assertion | observed |
|---|---|---|
| fixture | both introns canonical `GT..AG`, mRNA 900 bp | **holds** |
| hisat2 junctions | the introns read off the CIGARs **equal** the planted ones | **1301–2500, 2701–4000, exact** |
| spliced alignments | > 0 | **51** |
| assembled transcripts / exons | 1 / 3 | **1 / 3** |
| intron chain | **equal** to the planted chain | **exact** |
| internal exon | exact | **2501–2700** |
| every splice-site boundary | exact | **exact** |
| 3-prime end | exact | **4400** |
| 5-prime terminus | trimmed **inward**, never outward | **1027 (26 bp in)** |
| gffcompare intron / chain / exon level | 100.0 / 100.0 each, both directions | **100.0 / 100.0** |
| missed & novel introns | 0 and 0 | **0, 0** |
| class code | `=` (complete intron-chain match) | **`=`** |
| base precision | 100.0 | **100.0** |
| base sensitivity | == `100 × assembled / reference` | **97.1 = 100×874/900** |

### Assembly pins the intron chain exactly and the transcript ends only approximately

This is the whole shape of the recipe. Both introns come back to the base, both internal splice boundaries are exact, the 3-prime end is exact — and the 5-prime end is **26 bp short** (1027 against a planted 1001).

Nothing is broken. A splice junction is determined by split reads, which is a discrete, exact observation. A transcript *terminus* is determined by where coverage falls off, and coverage necessarily ramps up at the start of a transcript — only reads beginning in the first few bases cover base 1 — so StringTie trims where support drops. So the assertions split accordingly:

- **exact** for everything a splice junction defines: the intron chain, the internal exon, every boundary that is a splice site;
- **directional containment** for the terminus: it may be trimmed *inward*, never extended past the planted gene. Asserting `1027` would pin an artefact of coverage shape and go flaky on any StringTie version that trims differently; asserting a symmetric band would permit an assembly running off the end of the gene, which is a real error. The directional claim is the one that means something — [assert the claim you mean](../../practices/cross-checks.md).

### gffcompare's base sensitivity is not a defect to chase

Every structural metric is 100% in both directions. Exactly one number is below 100:

```text
        Base level:    97.1     |   100.0
        Exon level:   100.0     |   100.0
      Intron level:   100.0     |   100.0
Intron chain level:   100.0     |   100.0
  Transcript level:   100.0     |   100.0
```

`97.1` is not an error rate — it is `100 × 874/900`, the 26 trimmed bases and nothing else. The recipe asserts that identity rather than the literal `97.1`, which turns a number that looks like a flaw into a measurement that explains itself. **Base precision at 100.0 is the load-bearing half of that pair**: it says no assembled base lies outside the gene, i.e. StringTie invented nothing. Sensitivity below 100 with precision at 100 is a *trim*; the reverse would be a fabrication, and only the second is a bug.

`Novel introns: 0/2` carries the same weight in the structural direction — a transcript assembler's characteristic failure is inventing junctions, so checking that nothing was added matters as much as checking nothing was missed.

### Junction discovery is its own claim, asserted separately

Task 1 reads the introns straight out of the `N` operations in hisat2's CIGARs and requires them to equal the planted introns, *before* StringTie runs. That keeps two different questions apart: did the **aligner** find the junctions, and did the **assembler** chain them into the right transcript. Collapsing them would mean an alignment failure and an assembly failure arrive as the same red check.

The fixture plants canonical `GT..AG` at both introns because that is what de novo junction discovery scores on — with no annotation supplied, non-canonical junctions are found far less readily, and a fixture that ignored this would fail for reasons about the fixture rather than the tool.

One read of 268 does not align (99.25%): with reads every 3 bp, one lands with a 1 bp anchor past a junction, which is genuinely unalignable. The recipe records the rate and does not assert 100% — that would be asserting an accident of the stride.

### Pins (data tier: synthetic / in-code)

| | |
|---|---|
| hisat2 | `quay.io/aarchbio/hisat2@sha256:5a967484…` (2.2.3) |
| samtools | `quay.io/aarchbio/samtools@sha256:11917396…` |
| StringTie | `quay.io/aarchbio/stringtie@sha256:e0eb6ef1…` (3.0.3) |
| gffcompare | `quay.io/aarchbio/gffcompare@sha256:9674f300…` (0.12.10) |
| input | none — genome, gene structure, mRNA, reads and the planted annotation are generated in-task by awk and bash from `srand(41)` |

Four tools, four images, four tasks: hisat2 cannot sort, StringTie cannot align, gffcompare does neither. The GTF and BAM hand along through S3.

Also: spawn's task shell does not inherit the image's `PATH`, so `/opt/conda/bin` must be exported in every task.

### Run + verify

```sh
make run RECIPE=transcript-assembly
make ls  RECIPE=transcript-assembly
```

Assertions are `test` calls inside tasks 1, 3 and 4. Expect `smoke-check.txt` with `intron_chain_level 100.0 / 100.0`, `novel_introns 0`, `class_code =`, and `base_sensitivity 97.1`.

</details>
