# featureCounts and HTSeq on a whole RNA-seq run — Graviton4, Intel and AMD

> **HTSeq reproduces STAR's GeneCounts exactly: all 78,941 genes and all four unassigned classes,
> on all three chips.** featureCounts gives 677,651 more assigned pairs. Every one of them is a
> pair HTSeq calls ambiguous, and no gene gets fewer counts. featureCounts is 14× faster than
> HTSeq on one thread. Its whole job costs about $0.015, and HTSeq's costs $0.026–0.038. Every
> count table is byte-identical across chips.

Workload: the **complete** Geuvadis run `ERR188026` (15,800,127 read pairs), aligned by STAR against
GRCh38 with the **whole** Ensembl 116 annotation (78,941 genes), and counted per gene. That is one
sample through the step that builds a count matrix, at its real size. A cohort repeats it per sample.
These are the same reads and index as [salmon-real](../salmon-real/README.md) and
[star-real](../star-real/README.md).

**Data tier: durable public ids, plus one built intermediate.** The reads are pinned by ENA's
`fastq_md5` and the GTF by Ensembl release-116's `CHECKSUMS`. Both are checked on the box before
alignment ([`results/align/pins.txt`](results/align/pins.txt)). The index is star-real's
published `inputs/star-index-GRCh38-116/`, and its large files are hashed into
[`results/align/index.sha256`](results/align/index.sha256). The alignment is built once on Graviton4
by task 0 and pinned by sha256 in [`inputs.sha256`](inputs.sha256). Every counting task checks that
file before it counts.

## Run it

```sh
export AWS_PROFILE=aws B=<your bucket>
bash measurements/rnaseq-counts-real/stage-inputs.sh "$B"      # scripts + pins; the data is already staged
spawn task run --wait --spec "$(python3 scripts/spec_arch.py rnaseq-counts arm64 "$B" r2 measurements/rnaseq-counts-real/00-star-align.task.json)"
for a in arm64 x86_64 amd; do                                  # each 02 reads its own chip's 01 outputs
  for s in 01-featurecounts 02-htseq; do
    spawn task run --wait --spec "$(python3 scripts/spec_arch.py rnaseq-counts $a "$B" r2 measurements/rnaseq-counts-real/$s.task.json)"
  done
done
bash measurements/rnaseq-counts-real/collect.sh "$B" r2 r2     # results/<chip>/, results/crosschip.txt
```

```sh
STAR ... --outSAMtype BAM Unsorted --outSAMunmapped Within --quantMode GeneCounts
featureCounts -T 8 -p --countReadPairs -s 0 -t exon -g gene_id -a genes.gtf -o fc.txt reads.bam
htseq-count -f bam -r name -s no -t exon -i gene_id -m union --nonunique none \
  --secondary-alignments ignore --supplementary-alignments ignore reads.bam GRCh38.116.gtf.gz
```

[`align.sh`](align.sh), [`featurecounts.sh`](featurecounts.sh), [`htseq.sh`](htseq.sh) and
[`compare.py`](compare.py) are staged as files. [`mkspec.py`](mkspec.py) writes the three specs
and pins those files by sha256.

## Results

Same image digests on each chip: aarch.bio's subread 2.1.1 and HTSeq 2.1.2 on Graviton4, and
bioconda's x86 builds of the same versions on both x86 chips. Same staged bytes. featureCounts ran
on a `2xlarge` (8 vCPU, 16 GiB) at 8 threads, then again at 1 thread in the same task. HTSeq runs as
one process, so it got a `large` (2 vCPU, 16 GiB). The 16 GiB is for staging, because half of RAM
is the tmpfs that holds the 2.9 GB BAM.

| tool | chip | instance | tool wall | billed | peak RSS | $/hr | compute $/result | billed $/result |
|---|---|---|---|---|---|---|---|---|
| featureCounts `-T 8` | Graviton4 | `c8g.2xlarge` | 26 s | 168 s | 0.78 GiB | 0.3191 | 0.0023 | 0.0149 |
| | Xeon 6975P-C | `c8i.2xlarge` | 26 s | 154 s | 0.78 GiB | 0.3748 | 0.0027 | 0.0160 |
| | EPYC 9R45 | `c8a.2xlarge` | **17 s** | 124 s | 0.78 GiB | 0.4311 | **0.0020** | **0.0148** |
| featureCounts `-T 1` | Graviton4 | same box | 63 s | — | 0.51 GiB | | 0.0056 | |
| | Xeon | same box | 60 s | — | 0.51 GiB | | 0.0062 | |
| | EPYC | same box | 44 s | — | 0.51 GiB | | 0.0053 | |
| HTSeq | Graviton4 | `r8g.large` | 898 s | 988 s | 0.23 GiB | 0.1178 | 0.0294 | 0.0323 |
| | Xeon 6975P-C | `r8i.large` | 902 s | 993 s | 0.24 GiB | 0.1389 | 0.0348 | 0.0383 |
| | EPYC 9R45 | `r8a.large` | **525 s** | **580 s** | 0.24 GiB | 0.1598 | **0.0233** | **0.0257** |

A *result* is one sample's verified count table. *Billed* is spawn's task window, wrapper start to stage-out done, at
spawn's printed price, and the featureCounts window covers both thread counts. *Peak RSS* is the
tool's own `VmHWM`. Container `memory.peak` was 5.5 GB for featureCounts, which is mostly page cache
from the uncompressed GTF and the BAM, and 0.24 GB for HTSeq. Threads per core: 1 on Graviton4 and
AMD, 2 on Intel, so `r8i.large` is one core. featureCounts links no BLAS. HTSeq's numpy links
OpenBLAS (`neoversev2` on Graviton4, `Cooperlake` on both x86 chips), but counting never calls it.
STAR's alignment (task 0, Graviton4 only): 66 s on `r8g.4xlarge`, $0.080 billed, 92.47% uniquely
mapped. That matches star-real.

- **For featureCounts the boot is the bill.** 17–26 s of counting sits inside a 124–168 s window,
  so the chips differ by $0.001 per sample. Pick a box by price and staging space, not by speed.
- **For HTSeq the chip matters, and AMD wins.** HTSeq is a single Python process. EPYC finishes in
  525 s, 1.71× faster than Graviton4 (898 s) and Intel (902 s). That makes AMD the cheapest per
  result despite the dearest hour: $0.026 against Graviton4's $0.032 and Intel's $0.038.
- **featureCounts is 14× faster than HTSeq on one thread on the same chip** (63 s vs 898 s on
  Graviton4), and 35× at 8 threads. Across a cohort, that gap is wall time, not money: both cost
  pennies per sample.
- **Memory doesn't constrain either tool** (under 1 GiB). The staged BAM decides the box.
- n = 1 per cell. Both tools are deterministic and their output is identical on every chip, so a
  repeat re-measures only timing. A first run of the same counts gave 58 s at `-T 1` and 894 s for
  HTSeq on Graviton4.

## The identities, at scale, on all three chips

| observable | assertion | observed (every chip) |
|---|---|---|
| reads vs ENA / GTF vs Ensembl | md5 / `sum` == published | yes |
| STAR input pairs | == ENA `read_count` | 15,800,127 |
| HTSeq: genes + every `__` class | **== input pairs** | 15,800,127 |
| STAR ReadsPerGene, each of 3 columns | **== input pairs** | 15,800,127 |
| **HTSeq vs STAR GeneCounts (unstranded)** | **identical, every gene** | **0 of 78,941 differ** |
| HTSeq `__no_feature` / `__ambiguous` / `__alignment_not_unique` / `__not_aligned` | **== STAR's `N_` rows** | 448,202 / 2,202,143 / 898,248 / 291,590 |
| HTSeq unique classes, featureCounts assigned + no-features + ambiguity | **== STAR uniquely mapped** | 14,610,289 |
| featureCounts unmapped | **== STAR unmapped + too-many-loci** | 291,590 |
| featureCounts multimapping records | in [2, 10] × STAR multi-loci | 2,555,038 (2.84×) |
| featureCounts gene sum | **== its Assigned** | 12,637,595 |
| featureCounts `-T 8` vs `-T 1` | **byte-identical counts and summary** | yes |
| featureCounts vs HTSeq: genes differing | observation | 9,436 |
| **featureCounts ≥ HTSeq on every differing gene** | **all** | 9,436 of 9,436 |
| **Σ(featureCounts − HTSeq)** | **== HTSeq ambiguous − featureCounts ambiguity** | 677,651 == 677,651 |
| no-feature pairs, featureCounts vs HTSeq | **equal** | 448,202 |
| Spearman, featureCounts vs HTSeq | observation | 0.9901 (25,675 genes) |
| STAR stranded columns, forward / reverse | observation | 7,393,223 / 7,387,405 (1.0008) |

- **HTSeq and STAR implement the same rule, and on 15.8M pairs they agree on every gene.** STAR's
  manual says its counts coincide with `htseq-count`'s defaults. For the unstranded column that
  means HTSeq's `-s no` (its default is `-s yes`), with secondary alignments ignored. With both set, it is
  exact, down to the unassigned classes.
- **featureCounts and HTSeq disagree in one direction only.** Both see the same 14,610,289 unique
  pairs and the same 448,202 that hit no gene. Where they differ, featureCounts assigns a pair to
  a gene and HTSeq calls it ambiguous (1,524,492 ambiguous against 2,202,143). The largest case is
  `ENSG00000205542`, at 107,415 against 74,102. Which rule in featureCounts resolves
  these pairs is not established here. The identity proves the difference is ambiguity resolution,
  so a gene-by-gene comparison between the tools is comparing a [method](../../practices/cross-checks.md),
  not testing correctness.
- **The library is unstranded**, as STAR's two stranded columns show (ratio 1.0008). So `-s 0` and
  `-s no` are the right settings for this sample, not an assumption.
- **featureCounts' multimapper class counts alignment records, not pairs.** 2,555,038 records for
  898,248 multi-mapped pairs. Adding `--primary` changed no count (same sha256 `73c46d8b…`), so the
  conservation identity runs through STAR's own log instead.

## Across chips

- **Identical on all three.** `fc_counts.tsv` (`73c46d8b…`), the featureCounts summary,
  `htseq_counts.tsv` (`c2db026c…`) and both check files are byte-identical on Graviton4, Intel and
  AMD ([`results/crosschip.txt`](results/crosschip.txt)). That holds across two builds of each
  tool (aarch.bio arm64, bioconda x86). Counting is integer interval overlap with no floating
  point, so this is the expected result. It is asserted.

## Caveats

One sample, one annotation, one alignment. A different aligner, or STAR with other multimapper
settings, gives different BAM bytes and different counts. The alignment is a pinned input, not
something measured across chips. Neither instance size nor generation was swept. Spend for this
write-up: **$0.27**, including one earlier run of each counting task on Graviton4.
