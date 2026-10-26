# FastQC + MultiQC on a real cohort: Graviton4 vs Intel vs AMD

> **446M reads in eleven whole runs, every FastQC count exact against a recount and a published
> number, every output byte-identical on all three chips.** AMD is 1.40× faster than Graviton4.
> Graviton4 is still the cheapest per billed result, because a FastQC cohort spends half of its
> billed window outside FastQC, staging and re-verifying the reads.

Workload: **eleven complete FASTQ files from seven public runs and four assays**, 22.9 GB
compressed, 446.0M reads, 32.2 Gbp. This is the size of a sequencing core's mixed project
directory, which is what FastQC + MultiQC are run on. No run is subsampled.

| files | run | assay | reads per file | already staged by |
|---|---|---|---|---|
| 2 | `SRR062634` (1000 Genomes HG00096, `.filt`) | WGS, GA II 100 bp | 24,148,993 | [bwa-real](../bwa-real/README.md) (RODA `s3://1000genomes`) |
| 2 | `ERR188026` (Geuvadis) | RNA-seq, HiSeq 2000 75 bp | 15,800,127 | [salmon-real](../salmon-real/README.md) (ENA) |
| 2 | `ERR2984773` (ZymoBIOMICS D6300) | shotgun, MiSeq 2×151 | 8,785,731 | [metaphlan-real](../metaphlan-real/README.md) (RODA SRA) |
| 2 | `ERR2935805` (ZymoBIOMICS D6310) | shotgun, HiSeq 1500 2×101 | 47,832,553 | [metaphlan-real](../metaphlan-real/README.md) (RODA SRA) |
| 3 | `SRR8297998` / `SRR8279891` / `SRR8281054` (Brunello A375) | CRISPR screen, HiSeq 2500 50 bp | 60.1M / 99.1M / 93.6M | [mageck-real](../mageck-real/README.md) (ENA) |

Nothing new was fetched. The specs read each object where its owning measurement staged it
(`cohort.tsv`). Every byte is checked against `inputs.sha256` in the task, and the five files that
are ENA's own bytes are also checked against ENA's published md5 (`ena.md5`).

## Run it

```sh
export AWS_PROFILE=aws
bash measurements/qc-aggregate-real/stage-inputs.sh "$COOKBOOK_BUCKET"   # scripts + pins only
for a in arm64 x86_64 amd; do
  for t in fastqc multiqc; do
    spawn task run --spec "$(python3 scripts/spec_arch.py qc-aggregate $a "$COOKBOOK_BUCKET" r2 \
      measurements/qc-aggregate-real/$t.task.json)" --wait
  done
done
bash measurements/qc-aggregate-real/compare.sh "$COOKBOOK_BUCKET" r2   # cross-chip sha256 of every text output
```

Task 1 runs `fastqc -t 11 --extract` over all eleven files in one invocation (`fastqc.sh`), then an
independent recount (`recount.pl`) and `check_fastqc.pl`. Task 2 runs `multiqc` over the eleven
zips and `check_multiqc.py`. Both are the recipe's own pins. `mkspec.py` writes the specs, with every
staged script sha256-pinned in the command.

## Results

11 FastQC threads, one `4xlarge` (16 vCPU, 64 GiB) per chip, same input bytes:

| chip | instance | FastQC wall | MultiQC wall | overhead | FastQC billed | peak RSS | $/hr | compute $/result | **billed $/result** |
|---|---|---|---|---|---|---|---|---|---|
| Graviton4 | `m8g.4xlarge` | 231 s | 2 s | 109 s | 482 s | 4.41 GiB | 0.7181 | 0.0461 | **0.0978** |
| Xeon 6975P-C | `m8i.4xlarge` | 220 s | 2 s | 128 s | 477 s | 3.95 GiB | 0.8467 | 0.0518 | 0.1147 |
| **EPYC 9R45** | `m8a.4xlarge` | **165 s** | 2 s | **96 s** | 361 s | 4.22 GiB | 0.9738 | **0.0447** | 0.0994 |

*Overhead* is the FastQC task's spawn window minus its command: stage-in of 22.9 GB (71–98 s),
Docker install, image pull, stage-out. *FastQC billed* is that whole window, wrapper start to
stage-out done ([`results/billed-windows.txt`](results/billed-windows.txt)); besides FastQC and the
overhead it holds 100–142 s of in-task verification (re-hashing 22.9 GB, the recount and its
bookkeeping) that a user would not run. *Billed $/result* is the FastQC window at spawn's printed
price plus the whole MultiQC task (`c8g/c8i/c8a.large`, 60–95 s, under $0.003), the two completion
records' `cost_estimate`. Peak RSS is the container's anon memory over all eleven JVMs. **These timings are not compute
cost:** boot before spawn's wrapper starts is not in any window.

- **AMD is 1.40× and Intel 1.05× faster than Graviton4.** AMD's lead covers its 36% higher hourly
  price on compute (3% cheaper). Billed, Graviton4 is cheapest (2% under AMD, 15% under Intel),
  because overhead and verification are half of every window and costs more per second on the dearer chips.
- **The cohort's wall is its largest file.** FastQC parallelises across files, one thread each,
  so the two ~95M-read CRISPR replicates set the wall (99.1M reads in ≤231 s is ≥430k reads/s on one Graviton4 core) and the
  small files finish in the first minute. Average busy cores were **6.3–6.4 of 16** on every chip.
- **The box is picked by tmpfs, not cores.** Staged reads live in host `/tmp`, which is
  [half the RAM](../../practices/container-path.md): 21.3 GiB peak needs 64 GiB of RAM, which on
  `m8g` comes with 16 vCPU. RSS was a fifth of that.
- n = 1 per cell. FastQC is deterministic here (every output byte-identical across three chips,
  below), so a repeat changes the timings and nothing else. Graviton4 ran the same cohort twice
  (r1, r2) at 230 s and 231 s.

Sizing came from `probe.sh` (`results/probe.host.txt`): one whole file (`ERR188026_1`, 15.8M
reads) took 54 s on one Graviton4 core at 0.38 GiB anon, and staging all eleven filled 70% of the
tmpfs.

## What held at scale, on all three chips

| claim | why it must hold | observed |
|---|---|---|
| FastQC `Total Sequences` == recount == published count | ENA `read_count` (seven runs) or the 1000 Genomes `.filt` count fastp and seqkit assert | **11 of 11 exact**; cohort 445,999,501 |
| recount bases == published bases | ENA `base_count`; fastp's `total_bases` for `SRR062634` | 11 of 11 exact |
| `Total Bases` == FastQC's `formatLength` of the recount | `BasicStats.java`, transcribed | 11 of 11 |
| `%GC` == ⌊100 (G+C)/(A+C+G+T)⌋ | `BasicStats.java` integer division, N excluded | 11 of 11 |
| `Sequence length`, flagged reads == 0, 0 malformed records | recount min/max | 11 of 11 |
| Σ length histogram == Σ per-sequence-quality histogram == N | each module counts every read once | 22 of 22 |
| mates hold the same read count | paired runs | 4 of 4 pairs |
| MultiQC rows == expected N | the aggregator cannot know N | 11 of 11 |
| reads, length, %GC, dedup% round-trip numerically | aggregation is lossless | 11 of 11 |
| every module's PASS/WARN/FAIL == FastQC's `summary.txt` | the colour grid is FastQC's verdict | 121 of 121 |
| `avg_sequence_length` == bases / reads | every file here is fixed-length | 11 of 11 |
| General Statistics: round(M × 1e6) == count; %GC; 100 − dedup%; % fails | the headline table | 11 of 11 each |
| withhold the largest report (99.1M reads, 22% of the cohort) | MultiQC exit **0**, 10 rows, 0 log lines name it | as the recipe found on its fixture |

Observed, not asserted:
- **The General Statistics table stores read counts in millions, as floats.** For 3 of 11 files,
  `millions × 1e6` is not the integer count without rounding, and the cohort total reads
  `445.99950099999995`. Read counts from `multiqc_fastqc.txt`, which carries them whole.
- **FAIL is not a verdict on these runs.** All seven are published, analysed datasets, and 10 of
  11 files FAIL at least one module (22 FAIL, 14 WARN, 85 PASS). The flags follow the assay: the
  CRISPR libraries fail per-base content and duplication by construction, the metagenomes fail
  per-sequence GC. Read the grid per assay.
- Deduplicated % (20.2–96.3) is FastQC's estimate from the first 100k distinct sequences.
  It is reported, not checked.

## Across chips: what is a fixed point and what is a path

Nothing differed. All 22 `fastqc_data.txt` and `summary.txt` files, the recount, and both MultiQC
tables are **byte-identical** on Graviton4, Intel and AMD (`results/compare.txt`). That includes
FastQC's floating-point per-base means and its smoothed GC curve, computed under OpenJDK 25 (the
arm64 image) and OpenJDK 17 (bioconda's x86 image). FastQC links no BLAS, so the
[OpenBLAS kernel question](../../patterns/cross-architecture.md) does not arise. The `_fastqc.zip`
files differ only as archives (member timestamps) and are not compared.

## Caveats

- Seven runs is one mixed project, not a fleet. Per-file throughput should transfer. The overhead
  is per task, so QC a cohort in one task rather than one per file.
- `SRR062634` is the 1000 Genomes `.filt` release, not ENA's raw run (24,476,109 spots), so its
  published count is the one fastp and seqkit already assert.
- Prices are on-demand us-west-2, from spawn's cost record for these instances.
