---
tool: star
tool_version: 2.7.11b
image: quay.io/aarchbio/star@sha256:90331f64bd73eadaefbebc8d4aecfed3ebed1a7e40b761041acbbcf53b3e13ae
spawn_version: 0.104.0
---
# STAR — spliced RNA-seq alignment

Build a splice-aware index, align RNA-seq reads across exon junctions, count per gene.

> **Read this before copying the recipe for real work.** The index is **chromosome 20 only**, so **6.67% of reads map uniquely and 92.7% come back "unmapped: too short."** That is the correct result for a whole-transcriptome library aligned against one chromosome — most reads have no home in the reference — but it means this recipe proves *STAR runs and produces a real BAM*, **not** *these alignments are right*. For real work, index the whole genome (see "make it yours").

## Run it

```bash
# 1. build the splice-aware index (reusable across every sample)
STAR --runMode genomeGenerate --genomeDir idx --genomeFastaFiles ref.fa \
     --sjdbGTFfile genes.gtf --sjdbOverhang 74 --genomeSAindexNbases 11

# 2. align a sample and count reads per gene
STAR --genomeDir idx --readFilesIn r1.fq.gz r2.fq.gz --readFilesCommand zcat \
     --quantMode GeneCounts --outSAMtype BAM SortedByCoordinate
```

Two tasks, because **the index is the expensive, reusable artifact** — it doesn't depend on the reads, so task 2 re-runs against it for every new sample without rebuilding.

## Make it yours

| In the recipe | Swap for | What to know |
|---|---|---|
| **chr20-only index** | the whole-genome index | **the load-bearing limit** — chr20 is why 6.67% map; a whole-genome index gives realistic rates (and, counter-intuitively, aligns *faster*, since STAR won't burn time exhaustively failing the 92.7% that have no chr20 home). |
| `--genomeSAindexNbases 11` | `min(14, log2(genomeLen)/2 − 1)` | **must change with the reference** — 11 is right for chr20's 64 Mb; the default 14 is sized for a whole genome. Wrong value wastes memory and STAR warns. |
| `--sjdbOverhang 74` | `readLength − 1` | these reads are 75 bp (checked). Set it to your read length minus one. |
| the 200k-pair ERR188026 slice | your reads | same slice [salmon](../salmon/README.md) uses, so the two RNA-seq recipes are directly comparable. |

STAR alignment is deterministic given the index — no seed. The index is a directory, so it travels between tasks as a **tar** ([why directory outputs don't work on the container path](../../practices/container-path.md)).

## Shape, size, cost

Two tasks: `01-index` (`c8g.2xlarge`, ~25 s work) → `02-align` (`c8g.2xlarge`, ~4m37s). A cohort of samples reuses the one index and fans out the align step → [job arrays](../../patterns/job-arrays.md). Caps $0.13 / $0.18. Timings are dominated by boot + pull, [not compute](../../practices/container-path.md).

**Sizing a whole-genome index (the scale-it):** a full human STAR index is ~30 GiB and is built **in `/tmp`, which is a tmpfs ≈ ½ the instance's RAM** — so it's sized by *RAM*, not disk (an `r8g.4xlarge`, 128 GiB → ~64 GiB `/tmp`, holds it; `disk_gib` grows the container root, which the build doesn't use). That's the real constraint the chr20 fixture sidesteps.

<details>
<summary>As shipped: the chr20 caveat mechanics, the checks, pins</summary>

**Why chr20, honestly:** `too short` is STAR's catch-all for a read failing the minimum-mapped-length filter, not a statement about read length — 92.7% of a whole-transcriptome library simply has no chr20 home. Not a broken run, not tunable away honestly. The mapping bands below are wide because the rate is a property of this reference/library mismatch; still tight enough that a broken index or empty BAM fails.

| observable | assertion | observed |
|---|---|---|
| input reads | exactly 200000 | 200000 |
| uniquely mapped % | 2.0–20.0 | **6.67** (the caveat) |
| splices annotated | > 500 | 2139 (proves `--sjdbGTFfile` was used — impossible if ignored) |
| BAM magic bytes | `1f8b0804` | `1f8b0804` |
| ReadsPerGene rows | 1900–2100 | 1976 (= 4 header + 1972 chr20 genes) |
| index: chromosomes / name / length | 1 / `20` / 64444167 | matches |

No samtools in this image (one tool per image), so the BAM is checked by size + BGZF magic `1f8b0804` rather than `flagstat` — STAR's own `Log.final.out` already reports the numbers a flagstat would. `genomeGenerate` ignores `--outFileNamePrefix` and writes `Log.out` into the genome dir; the check reads it there.

**Pins.** Image `quay.io/aarchbio/star@sha256:90331f64bd73…` (2.7.11b, cosign-signed, `linux/arm64` only). Reference: Ensembl `release-116` chr20 fasta (`sha256:1b8cd336…`); annotation: chr20 records of the release-116 GTF (`sha256:2ca5f412…`, 1972 genes; `stage-inputs.sh` verifies every kept record is chr20 and Ensembl names it `20`, not `chr20`); reads: ENA `ERR188026` first 200k pairs (`sha256:1198ed07…`/`6104ee46…`), the salmon slice. `release-116/` is immutable (pinnable); `current_*` isn't.

**Run + verify.**
```sh
spawn task run --spec recipes/star/01-index.task.json --wait
spawn task run --spec recipes/star/02-align.task.json --wait
aws s3 ls s3://scicookbook-942542972736-us-east-1/runs/star/r1/
```
Task 2 must **not** `rm` the staged index tar — the container can't unlink a staged input it doesn't own (`EPERM`), and `rm -f` doesn't suppress that ([the container path](../../practices/container-path.md)). Re-running: bump the `-r1` suffix.

</details>
