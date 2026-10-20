# bwa-mem2 on a real workload — a whole genome, three chips, one SAM

> **bwa-mem2 and bwa wrote the same 148,188,350 records on Graviton4, Intel and AMD: one SAM
> per run, six times over, once bwa's post-0.7.17 `MQ` tag is set aside.** bwa-mem2 was 1.6×
> faster on Graviton4 and about 2× on both x86 chips, where it runs an AVX-512 kernel that the
> arm64 build doesn't have. On the same `r8*.4xlarge` it cost less per genome than bwa on every chip.

Workload: **HG00096's whole low-coverage genome**. That is every paired run 1000 Genomes lists for
the sample (`SRR062634`, `SRR062635`, `SRR062641`): **73,305,772 pairs, 14.66 Gbp, ~4.7×**,
aligned to the complete `GRCh38_full_analysis_set_plus_decoy_hla` with ALT contigs (3,171) in
force. It is one `mem` per run, each with its own read group, which is how 1000 Genomes aligned it.
An aligner's cost and output are per read, so a 30× genome is this job ~6× over, and nothing about
the shape changes.

## Run it

```sh
export AWS_PROFILE=aws B=$COOKBOOK_BUCKET N=$(date +%s)
bash stage-inputs.sh $B                     # 2 runs server-side from RODA + the scripts
for a in arm64 x86_64 amd; do
  for s in $(python3 ../../scripts/spec_arch.py bwa-mem2 $a $B $N 01-bwa.task.json 02-mem2.task.json); do
    spawn task run --spec "$s" --wait       # bwa, then bwa-mem2 against bwa's output on the same chip
  done
done
bash collect.sh $B $N                       # results/<chip>/<tool>/ and results/summary.txt
```

`mkspec.py` writes the two arm64 specs, which pin `align.sh`, `lib.sh` and `sample.tsv` by sha256.
The bwa index is the one RODA publishes, and the bwa-mem2 index is `recipes/bwa-mem2`'s 00-index
tar. Every chip reads the same bytes, and `collect.sh` checks each input's sha256 on all three.

## Results

16 threads on an `r8*.4xlarge` (16 vCPU, 128 GiB) of each chip, using the recipe's images: aarch.bio
on Graviton4 and bioconda on both x86 chips. *Align* is the three `mem` invocations summed, index
load included. *Billed* runs from launch to terminate. A *result* is one genome aligned.

| chip | instance | tool | kernel | align | billed | peak RSS | $/hr | compute $/result | billed $/result |
|---|---|---|---|---|---|---|---|---|---|
| Graviton4 | `r8g.4xlarge` | bwa | — | 2,383 s | 2,662 s | 8.70 GiB | 0.9426 | 0.624 | 0.697 |
| Graviton4 | `r8g.4xlarge` | **bwa-mem2** | `AVX` (128-bit) | 1,517 s | 2,027 s | 25.8 GiB | 0.9426 | 0.397 | **0.531** |
| Xeon 6975P-C | `r8i.4xlarge` | bwa | — | 3,080 s | 3,612 s | 8.70 GiB | 1.1114 | 0.951 | 1.115 |
| Xeon 6975P-C | `r8i.4xlarge` | bwa-mem2 | `avx512bw` | 1,505 s | 2,183 s | 25.7 GiB | 1.1114 | 0.465 | 0.674 |
| EPYC 9R45 | `r8a.4xlarge` | bwa | — | 1,992 s | 2,440 s | 8.71 GiB | 1.2781 | 0.707 | 0.866 |
| EPYC 9R45 | `r8a.4xlarge` | bwa-mem2 | `avx512bw` | **1,030 s** | 1,632 s | 25.7 GiB | 1.2781 | **0.366** | 0.579 |

- **bwa-mem2 over bwa: 1.57× on Graviton4, 2.05× on Intel, 1.93× on AMD.** The x86 builds dispatch
  to `bwa-mem2.avx512bw`, with 64 lanes of 8-bit SIMD. The arm64 build has one binary that reports
  `Executing in AVX mode` at 16 lanes. So the x86 margin is bwa-mem2's vector kernels on top of its
  re-engineering, and the Graviton margin is the re-engineering alone.
- **Graviton4 is the cheapest place to run either aligner, billed.** AMD is fastest, at 1.47× Graviton4
  on bwa-mem2 and 1.20× on bwa, but its rate card is 36% higher. That leaves AMD cheapest on compute
  alone for bwa-mem2 ($0.366 against $0.397) and second once boot and staging are billed. Intel's
  bwa row is the outlier: 16 vCPUs here are 8 cores × 2 threads (`threads_per_core` 2), and bwa
  takes 29% longer there than on Graviton4's 16 full cores. bwa-mem2's vector kernel makes that gap disappear.
- **On one box size, bwa-mem2 is cheaper per genome on every chip**, by 24–40% billed. That does not
  overturn [the recipe's](../../recipes/bwa-mem2/README.md) cost finding. bwa's 8.7 GiB fits a
  smaller box than `r8*`, and bwa-mem2's 25.8 GiB process, plus a 17.7 GB index to stage, does not.
  So the ranking flips with the box you are allowed to pick. These rows hold the box fixed.
- **Peak RSS is a property of the index:** 8.70–8.71 GiB for bwa and 25.7–25.8 GiB for bwa-mem2
  on all three chips, read from `VmHWM`.
- n = 1 per cell. Both tools are deterministic here, and every output byte matched across chips
  (below), so a second run would repeat the science and only re-time it. Overhead (billed − align)
  is 279–678 s: 194–350 s of it is this measurement's own checks (md5s, input hashes, hashing
  and counting 52 GB of SAM), and the rest is boot and staging 17 GB (bwa) or 52 GB (bwa-mem2, which also stages bwa's records). So
  [the billed column is not compute cost](../../patterns/cost-per-result.md).

## What held at scale, on all three chips

| assertion | observed, every chip |
|---|---|
| reads match the md5s 1000 Genomes publishes in `sequence.index` | 6 of 6 files |
| primary R1 records == published `READ_COUNT`, and R2 the same, per run | 24,148,993 / 24,332,665 / 24,824,114 |
| records == primary + supplementary + secondary | 148,188,350 = 146,611,544 + 1,576,806 + 0 |
| ALT contigs read, both aligners | 3,171 |
| **bwa-mem2 SAM body == bwa's, sha256, per run, `MQ:i` removed from bwa's** | 3 of 3 runs, 0 lines differ |
| bwa-mem2 writes no `MQ` tag; bwa writes one on every record | 0 / 148,188,350 |

**The identity is exact over every field, SEQ and QUAL included, minus one tag.** bwa added the
mate-MAPQ `MQ` tag in 2021 (`lh3/bwa` d8dd308a, "Add the mate mapping quality tag"), after the
0.7.17 that bwa-mem2 tracks. The recipe pins bwa 0.7.19, so removing `MQ:i:` from bwa's lines is
what puts the two codes on like-for-like output ([cross-checks](../../practices/cross-checks.md)).
Hashing bwa-mem2's raw body gives exactly the digest of bwa's body with `MQ` removed, and on each
run that digest is the same on all three chips. The field-by-field differ in `align.sh` runs only
when the two hashes disagree. Here it never ran.

**Across chips, every output byte is a fixed point.** For each run, bwa's full SAM body hashes the
same on Graviton4, Intel and AMD. bwa-mem2's does too, under three different SIMD kernels. Every
count matched, as did every staged input's sha256, including the 17.7 GB bwa-mem2 index. So the
time difference between chips is the only difference. Nothing here is a path: alignment is integer
dynamic programming with a fixed batch size at a fixed `-t 16`.

## Caveats

- **The recipe's timed command pipes the SAM through `gzip -1`, and on x86 that times BusyBox's
  gzip, not bwa-mem2.** The recipe's AMD run took 1,114 s for `SRR062634`, with bwa-mem2 reporting
  337 s of compute. Here the same run took 337 s, writing plain SAM to disk. On Graviton4 (GNU gzip)
  the pipe cost little: 529–542 s there, 495 s here. This measurement keeps compression off the
  timed path. Put `samtools` or a multithreaded compressor behind it in production.
- `bwa-mem2 version` prints **2.2.1** from the package bioconda and aarch.bio both call 2.3.
- Rates are us-west-2 on-demand, taken from spawn's own cost record (`completion.json`).

Spend: $4.46 for the six tasks.
