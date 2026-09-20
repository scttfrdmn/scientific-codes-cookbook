---
tool: featurecounts-htseq
tool_version: "featureCounts v2.1.1 (subread 2.1.1) / HTSeq 2.1.2"
images:
  - quay.io/aarchbio/subread@sha256:0c2f26ea5f115a11e0178d6f9680001015bddd1fd1339e8f706e68bcd8fe306a
  - quay.io/aarchbio/htseq@sha256:a578d1c21dffe43dfc0075ade4f1862881ec825eab61b67197993bb609aa9811
spawn_version: 0.111.1
last_verified: 2026-09-20
---
# Counting reads per gene — featureCounts and HTSeq, on counts we planted

featureCounts and HTSeq each count the same aligned reads against the same annotation on Graviton4, checked against counts that were planted rather than measured. The step that produces the matrix [differential expression](../rnaseq-de/README.md) consumes.

> **What this covers.** 110 single-end reads against three non-overlapping single-exon genes, 100 placed inside genes and 10 deliberately intergenic. Gene-level union counting, unstranded. Not multi-exon models, multimappers, ambiguity resolution, or paired-end fragments.

## Run it

```bash
featureCounts -a genes.gtf -o fc.txt -t exon -g gene_id reads.sam
htseq-count -f sam -t exon -i gene_id -s no reads.sam genes.gtf
```

Two tasks: featureCounts builds the fixture and counts it, then HTSeq counts the same bytes and the two are compared against the planted truth.

## Make it yours

| In the recipe | Swap for | What to know |
|---|---|---|
| synthetic SAM + 3-gene GTF | your BAM + real annotation (GENCODE, Ensembl) | the planted counts are the whole point — real data has no answer key, only the two tools' agreement. |
| unstranded (`-s no`) | your library's strandedness | featureCounts defaults to unstranded, HTSeq to `-s yes`. **Get this wrong and counts differ for reasons that have nothing to do with the tools.** |
| single-end, unique, unambiguous | paired-end (`-p`), multimappers, overlapping genes | this is where the two tools genuinely diverge — their defaults for ambiguity and fragment counting differ, and that is a real decision, not a bug. |

**Leave the fixture:** 110 reads is enough to assert exact integer counts and to check that intergenic reads are excluded rather than absorbed. **Scale it** to your alignment — and settle strandedness first, because it is the single most common cause of a count matrix that looks broken.

## Shape, size, cost

Two tasks on `c8g.large` (2 vCPU / 4 GiB), TTL 12m each, caps $0.05 each. Recorded windows well under a minute of counting each; **1m10s** for the HTSeq + comparison task. **These timings are not compute cost.**

<details>
<summary>As shipped: five exact checks, a conservation identity, the stranding question settled by measurement, pins</summary>

### Five checks, all exact — integers, so no tolerance is needed

| observable | assertion | observed |
|---|---|---|
| featureCounts vs planted counts | exact match, every gene | **yes** (50 / 30 / 20) |
| HTSeq vs planted counts | exact match, every gene | **yes** (50 / 30 / 20) |
| the two tools against each other | identical, gene for gene | **yes** |
| intergenic reads | HTSeq's `__no_feature` == the 10 planted intergenic reads | **10** |
| conservation | assigned + all unassigned classes == total reads | **110 of 110** |

The conservation check is the one that would catch a silent loss: counting tools can *drop* a read without complaining, so requiring HTSeq's whole accounting (`__no_feature`, `__ambiguous`, `__too_low_aQual`, `__not_aligned`, `__alignment_not_unique` and the genes) to sum to exactly the input read count means nothing vanished. `__ambiguous` and `__alignment_not_unique` both come back **0**, confirming the fixture is unambiguous by construction rather than by luck.

### The stranding question, settled by running it rather than asserting it

featureCounts defaults to **unstranded**; HTSeq defaults to **`-s yes`**. That mismatch is the classic way two correct counters disagree — the same "[match the modes](../../practices/cross-checks.md)" trap that made bowtie2 and bwa look 82% concordant until `--local` was set.

So `-s no` is passed to HTSeq deliberately. But the honest finding is that **it makes no difference to this fixture**: running HTSeq with its stranded default returns the same 50 / 30 / 20, because every read here is forward-strand and every gene is on `+`. That was verified, not assumed — and it matters, because it means **the agreement reported above is not an artifact of a lucky mode choice**. On real data with a reverse-stranded library it would matter a great deal, which is why the swap table flags it.

### Pins (data tier: synthetic / in-code)

| | |
|---|---|
| featureCounts | `quay.io/aarchbio/subread@sha256:0c2f26ea…` (subread 2.1.1) |
| HTSeq | `quay.io/aarchbio/htseq@sha256:a578d1c2…` (2.1.2) |
| input | none — GTF, SAM and the planted counts are generated in-task by awk from `srand(7)` |

**One-tool-per-image consequence, worth knowing:** the comparison task runs in the HTSeq image, which has **no `featureCounts` binary** — so a `featureCounts -v` probe there does not fail loudly, it returns `bash: line 1: featureCounts: command not found` and a naive field extraction silently reports the word `line` as a version. featureCounts' version is therefore taken from the subread image tag. The same shape bit [bwa-mem2](../bwa-mem2/README.md), where `bwa` was absent from the bwa-mem2 image.

Also: spawn's task shell does not inherit the image's `PATH`, so `/opt/conda/bin` must be exported before either tool is callable.

### Run + verify

```sh
make run RECIPE=rnaseq-counts
make ls  RECIPE=rnaseq-counts
```

Assertions are `test` calls inside the second task ([exit 0 isn't proof](../../practices/container-path.md)). Expect `smoke-check.txt` with `tools_agree yes` and `htseq_accounting_sum 110 of 110`.

</details>
