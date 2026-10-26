# RSEM on a whole RNA-seq run — Graviton4, Intel and AMD

> **The same quantification on all three chips, byte for byte: 465,769 isoforms and 41,145 genes
> from 394 million alignments.** Every read is accounted for, the expected counts add back to the
> alignable reads, every gene is the sum of its isoforms, and RSEM ranks transcripts like salmon
> does on the same reads (Spearman 0.944, genes 0.981). AMD is fastest, Graviton4 cheapest. Intel
> is slowest by 1.4×, and its EM step by 1.7×.

Workload: the **complete** Geuvadis run `ERR188026` (15,800,127 read pairs) against **all 465,769**
Ensembl 116 cDNA transcripts, with RSEM's isoform-to-gene map taken from each record's `gene:`
field. bowtie2 aligns to the transcriptome with the exact command `rsem-calculate-expression
--bowtie2 --paired-end` builds, `-k 200` included. One sample against the whole transcriptome is
the unit RSEM is run on, once per sample before any differential-expression step, so this is the
real per-sample job at its real size. These are the same reads and the same transcriptome as
[salmon-real](../salmon-real/README.md) and [quant-depth](../quant-depth/README.md).

**Data tier: durable public ids, plus two built intermediates.** The reads are pinned by ENA's
`fastq_md5`, the cDNA by Ensembl release-116's `CHECKSUMS`, and both are checked on the box
([`results/align/pins.txt`](results/align/pins.txt), [`results/prepare/pins.txt`](results/prepare/pins.txt)).
The RSEM reference and the 14.3 GB gzipped SAM are built once on Graviton4 (tasks 00 and 01) and
pinned by sha256 in [`inputs.sha256`](inputs.sha256), which every quant task checks before it runs.

## Run it

```sh
export AWS_PROFILE=aws B=<your bucket>
bash measurements/rsem-real/stage-inputs.sh "$B"                # scripts + salmon-real's quant.sf; the data is already staged
for s in 00-prepare 01-bowtie2-align; do                         # once, Graviton4
  spawn task run --wait --spec "$(python3 scripts/spec_arch.py rsem arm64 "$B" r1 measurements/rsem-real/$s.task.json)"
done
for a in arm64 x86_64 amd; do
  spawn task run --wait --spec "$(python3 scripts/spec_arch.py rsem $a "$B" q2 measurements/rsem-real/02-quant.task.json)"
done
spawn task run --wait --spec "$(python3 scripts/spec_arch.py rsem arm64 "$B" c2 measurements/rsem-real/03-crosschip.task.json)"
bash measurements/rsem-real/collect.sh "$B" r2 q2               # results/<chip>/ and the table below
```

```sh
rsem-prepare-reference --transcript-to-gene-map gene_map.tsv cdna.fa ens116
bowtie2-build --threads 32 ens116.transcripts.fa ens116
bowtie2 -q --phred33 --sensitive --dpad 0 --gbar 99999999 --mp 1,1 --np 1 --score-min L,0,-0.1 \
  -I 1 -X 1000 --no-mixed --no-discordant -p 32 -k 200 --reorder -x ens116 -1 r1.fq.gz -2 r2.fq.gz | gzip -1 > aln.sam.gz
rsem-calculate-expression --alignments --paired-end --no-bam-output -p 16 --seed 12345 aln.sam.gz ens116 ERR188026
```

The scripts ([`prepare.sh`](prepare.sh), [`align.sh`](align.sh), [`quant.sh`](quant.sh),
[`check.R`](check.R), [`crosschip.R`](crosschip.R)) are staged as files. [`mkspec.py`](mkspec.py)
writes the four specs and pins those files by sha256. The bowtie2 image has no samtools, so the SAM
travels gzipped and RSEM's htslib reads it directly.

## Results

Same image digests on each chip: aarch.bio's RSEM on Graviton4 and bioconda's x86 build of the same
version on both x86 chips (package 1.3.3, binaries report `v1.3.1`). Same staged bytes. 16 threads
on a `4xlarge` (16 vCPU, 64 GiB) everywhere.

| chip | instance | RSEM wall (EM) | billed | peak RSS | $/hr | compute $/result | billed $/result |
|---|---|---|---|---|---|---|---|
| Graviton4 | `m8g.4xlarge` | 2250 s (1526 s) | 2452 s | 10.88 GiB | 0.7181 | **0.4488** | **0.4891** |
| Xeon 6975P-C | `m8i.4xlarge` | 3238 s (2572 s) | 3407 s | 10.88 GiB | 0.8467 | 0.7616 | 0.8013 |
| EPYC 9R45 | `m8a.4xlarge` | **1909 s** (1457 s) | **2052 s** | 10.88 GiB | 0.9738 | 0.5164 | 0.5550 |

A *result* is one sample quantified and checked. *Billed* is spawn's task window, wrapper start to stage-out done. *$/hr* is
spawn's cost estimate over that window. *RSEM wall* is `rsem-calculate-expression` end to end; the
EM figure is RSEM's own `Time Used for EM.cpp`, and the rest is single-threaded SAM parsing.
*Peak RSS* is the kernel's `VmHWM` for `rsem-run-em`, which holds all 394M alignments. Threads per
core: 1 on Graviton4 and AMD, 2 on Intel, so `m8i.4xlarge` has 8 cores. RSEM links no BLAS.

The shared inputs, once, on Graviton4: `rsem-prepare-reference` 19 s ($0.008 billed on
`m8g.xlarge`). On `m8g.8xlarge` at 32 threads, `bowtie2-build` took 657 s at 4.5 GiB and the `-k 200`
alignment 1534 s, averaging 31.94 busy cores, so `gzip -1` was not the ceiling; $0.920 billed.

- **Compute is the bill, unlike salmon.** RSEM's step is 92% of its window on Graviton4. Add the
  alignment and a sample costs about **$1.10** (bowtie2 $0.61 compute plus RSEM $0.49 billed),
  against salmon-real's **$0.022** for the same reads on `c8g.4xlarge`.
- **AMD is 1.18× faster than Graviton4 and Graviton4 is 1.13× cheaper.** AMD's hour costs 36% more,
  which outweighs its lead.
- **Intel's gap is the EM, at 1.69× Graviton4.** Parsing is close (666 s vs 724 s). The EM runs 16
  threads on 8 SMT cores. A 16-core Intel box was not run.
- **Memory is the alignments, not the chip:** 10.88 GiB everywhere. The 4xlarge was sized by
  staging: 15.7 GB of inputs land in the half-RAM `/tmp`.
- n = 1 per chip, plus a second Graviton4 run (2230 s, earlier check script) with the same sha256
  for every output. The outputs are identical across chips too, so a repeat re-measures timing only.

## The identities, at scale, on all three chips

| observable | assertion | observed (every chip) |
|---|---|---|
| cDNA / reads | Ensembl `sum` / ENA md5 == published pin | yes |
| bowtie2 pairs | == ENA `read_count` | 15,800,127 |
| RSEM `N0 + N1 + N2` | **== `N_tot` == ENA `read_count`** | 1,882,102 + 13,918,025 + 0 |
| isoform rows, gene rows | == transcripts / distinct genes in the map, every gene_id as mapped | 465,769 / 41,145 |
| **Σ expected_count** | **== N1 − noise mass, within 0.005 × the 105,801 nonzero printed values** | 13,918,024.05 (noise 1.7e-8) |
| Σ TPM, isoforms / genes | == 1e6 within 0.005 × nonzero | 999,996.96 / 999,999.30 |
| **TPM = k · FPKM** | **every row on one line, within both roundings and k's** | 0 off, k = 1.16264 |
| **gene count, TPM, FPKM** | **== Σ of its isoforms, every gene** (bound 0.005 × (isoforms + 1)) | 0 violations |
| IsoPct, expressed genes | sums to 100 | 0 violations |
| effective_length | ≤ length; TPM 0 where it is 0 | 0 violations |
| salmon ids | ⊆ RSEM ids (same FASTA) | 453,553 of 465,769 |
| **transcript Spearman vs salmon** | **≥ 0.85**, both detected | **0.9441** (79,993 transcripts) |
| **gene Spearman vs salmon** | **≥ 0.90**, both detected | **0.9812** (17,748 genes) |
| transcript log-Pearson vs salmon | observation | 0.9565 |
| alignable fraction | observation | RSEM 88.09%, salmon 94.39% |

- **The EM conserves reads, and the noise component takes almost none of them.** RSEM gives every
  alignable read a posterior over the transcripts plus a noise component (`EM.cpp`), so Σ
  expected_count is N1 minus the noise mass. The last M-step puts that mass at 1.7e-8 reads. The
  printed sum is 0.95 below N1, which is the rounding of 105,801 values at two decimals.
- **TPM is not computed from expected_count.** `WriteResults.h` derives TPM and FPKM from the
  polished θ, so the exact identity is TPM ∝ FPKM, one constant for all 465,769 rows. A ratio test
  against expected counts would only hold to the EM's 1e-3 stopping rule.
- **salmon keeps 12,216 fewer transcripts.** It indexes without `--keepDuplicates`, so it collapses
  sequence-identical transcripts that RSEM keeps and splits reads between. salmon's set is a
  subset, and a dropped duplicate counts as 0 for salmon.
- **The floors were set before the run, from the sibling pair.** salmon and kallisto agree at
  Spearman 0.908 on these reads ([quant-depth](../quant-depth/README.md)). RSEM–salmon came in
  higher, at 0.944. Summing to genes takes isoform ambiguity out and lifts it to 0.981.
- **bowtie2's strict mode aligns fewer reads than salmon maps.** RSEM's settings forbid indels and
  cap mismatches at 10%, giving 88.09% against salmon's 94.39%. Ranks agree anyway. Absolute counts
  differ: Σ NumReads is 14.9M for salmon against 13.9M for RSEM.
- **`--seed` does nothing here.** In RSEM 1.3.1 the seed reaches only the posterior sampling for an
  output BAM and the Gibbs sampler (`EM.cpp`, `rsem-calculate-expression`). With `--no-bam-output`
  and no `--calc-pme`, the result does not depend on it. The thread count does matter: it decides
  how reads are split for the E-step and the order the per-thread counts are added. It is pinned at
  16, and whether another `-p` moves a printed value was not measured.

## Across chips

- **Every printed output is byte-identical on all three chips.** `isoforms.results`,
  `genes.results` and `.cnt` have the same sha256 on Graviton4, Intel and AMD
  ([`results/*/results.sha256`](results/arm64/results.sha256), [`results/crosschip.txt`](results/crosschip.txt)).
  Every check line is identical.
- **The EM's path differs underneath, below the printed digits.** `.theta` holds the parameters at
  `%.15g`. Between Graviton4 and either x86 chip, 116,513 of 465,770 components differ, by at most
  2.5e-13 relative. Intel and AMD are bit-identical to each other. Both arrive at round 7,726 with
  the same printed `bChange` and `totNum` at every round, and 133 of the 7,726 per-round `SUM` lines
  differ in the 15th digit, the first at round 88. The divergence is architectural (Arm against
  x86 builds of the EM), and it is a path. It stays too small to reach two decimals, so here it is
  reported rather than asserted. A run with more rounds, or a value printed at more precision,
  could show it.

## Caveats

One sample, one aligner, one transcriptome. `--star` against the genome with
`--quantMode TranscriptomeSAM` gives a different alignment set and different numbers. The alignment
is a pinned input built once on Graviton4, not measured across chips. Instance size, thread count
and generation were not swept. Spend for this write-up: **$3.26**, including the earlier Graviton4
quant run.
