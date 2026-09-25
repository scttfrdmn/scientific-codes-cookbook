---
tool: bwa
tool_version: "0.7.19-r1273"
image: quay.io/aarchbio/bwa@sha256:19f0eceab80740b821be7ada082d4434acf778912aac658dd1b4c6692dd2e9ba
spawn_version: 0.111.1
last_verified: 2026-09-25
---
# bwa mem — a whole sequencing run against GRCh38

Aligns **24.1M read pairs (4.83 Gbp)** to the complete GRCh38 analysis set on Graviton, with cost per result measured across four Graviton generations. For anyone aligning short reads and choosing a box.

> **Scope.** One 1000 Genomes run (`SRR062634`, HG00096) against the *published* GRCh38 index — no index build. Alignment only; sorting and calling are downstream.

## Run it

```bash
make stage RECIPE=bwa-samtools   # once — caches the RODA index + reads into your bucket
make run   RECIPE=bwa-samtools   # 14 min on c8g.4xlarge, self-terminating
make ls    RECIPE=bwa-samtools   # aln.sam.gz (5.0 GiB) + smoke-check.txt
```

```bash
bwa mem -t 16 -R '@RG\tID:SRR062634\tSM:HG00096\tPL:ILLUMINA' \
  GRCh38_full_analysis_set_plus_decoy_hla.fa \
  SRR062634_1.filt.fastq.gz SRR062634_2.filt.fastq.gz | gzip -1 > aln.sam.gz
```

## Which box — measured (same image, same bytes, 16 threads)

| generation | instance | wall | reads/s | $/hr | **billed $/result** |
|---|---|---|---|---|---|
| Graviton2 | `c6g.4xlarge` | 1086 s | 44,560 | 0.5440 | 0.1841 |
| Graviton3 | `c7g.4xlarge` | 890 s | 54,373 | 0.5800 | 0.1621 |
| Graviton4 | `c8g.4xlarge` | 757 s | 63,926 | 0.6381 | 0.1508 |
| **Graviton5** | `c9g.4xlarge` | **590 s** | **82,021** | 0.6955 | **0.1320** |

**Take the newest generation, and 16 cores.** Graviton2→5: rate card +28%, wall −46%, result **28% cheaper**; peak RSS is 8.7 GiB on all four, so 32 GiB is right. More cores scale almost perfectly (3.8× on 4×) and compute-only cost barely moves (+5.7%) — but **billed cost rises 40%**, because ~95 s of boot-and-staging is charged whatever the box costs. 64 cores buys wall-clock, not savings.

## Make it yours

| In the recipe | Swap for | What to know |
|---|---|---|
| `SRR062634` (HG00096) | your FASTQs — edit `stage-inputs.sh` | one run per task; a cohort is this task [fanned out](../../patterns/job-arrays.md), each sized at 16 cores. |
| published GRCh38 index | your own reference | `bwa index` on a human genome is ~1 h and is **not** needed here: RODA ships one. Build only for a non-model organism. |
| `-t 16` | `-t 32/48/64` | faster and *more* expensive per result — see above. Memory grows ~0.21 GiB per thread above 16 (8.7 GiB → 18.7 GiB at 64). |
| `\| gzip -1` | `-o aln.sam` | compression costs ~9% wall and takes the intermediate from ~14 GiB to 5.0 GiB. Worth it: the trip through S3 is what the next task pays for. |

**Leave the workload** — a real run against a real genome, so the numbers above transfer to your data at the same depth. **Scale it** by fanning out samples, not by growing the box.

<details>
<summary>As shipped: the exact assertions, the full knee table, the data path, pins</summary>

### The checks

| observable | assertion | observed |
|---|---|---|
| SAM records | **exactly 48,392,167** — deterministic for this input + bwa version | **48,392,167** |
| mapped | ≥ 99.9% | **99.91%** |
| `aln.sam.gz` | passes `gzip -t`, > 1 GB | **5,347,045,543 bytes** |
| reproducibility | record count identical across two independent runs on different boxes | **identical** |

The record count is an exact assertion rather than a band because bwa is deterministic for a
fixed input, reference and thread-independent output ordering — it reproduced to the digit on
`r8g.4xlarge` and `c8g.4xlarge`. If it moves, the input or the version changed.

### The knee, in full — and why compute-only misleads

`c8g`, one generation, one family, so $/hr scales with size:

| cores | instance | wall | reads/s | avg cores | eff. | peak RSS | compute $ | **billed $** |
|---|---|---|---|---|---|---|---|---|
| 16 | `c8g.4xlarge` | 757 s | 63,926 | 15.21 | 95% | 8.70 GiB | 0.1342 | **0.1508** |
| 32 | `c8g.8xlarge` | 387 s | 125,044 | 28.80 | 90% | 12.02 GiB | 0.1372 | 0.1705 |
| 48 | `c8g.12xlarge` | 259 s | 186,842 | 40.43 | 84% | 15.41 GiB | 0.1377 | 0.1888 |
| 64 | `c8g.16xlarge` | 200 s | 241,961 | 51.09 | 80% | 18.65 GiB | 0.1418 | 0.2106 |

On CPU seconds alone the advice would be "take 64, the speed is nearly free." The fixed
overhead — boot, image pull, staging 8.9 GiB — is **93–97 s regardless of instance size**, so
it is 11% of the 16-core run and **33% of the 64-core run**, charged at 4× the rate. The two
measures rank the same runs differently and only one is the invoice. Full method and raw
results: [measurements/bwa-real](../../measurements/bwa-real/README.md).

**bwa is a fourth scaling shape** beyond the three in [sizing](../../patterns/sizing.md): the
answer is stable, the speedup near-linear, and compute cost flat — so the only thing bending
its curve is fixed overhead, which makes the data path the lever, not the chip.

### The data path

Staging in is **8.9 GiB**, and dropping one file is what makes that tolerable: `bwa mem`
reads `<prefix>.amb/.ann/.bwt/.pac/.sa` only, so the **3.0 GiB `.fa` is never staged** —
12 GiB down to 8.9. Staging out the 5.0 GiB `aln.sam.gz` took ~67 s (~75 MB/s). At 16 threads
single-threaded `gzip -1` keeps up with bwa (~16 MB/s of SAM); at 64 threads it would not, and
the intermediate would have to go out uncompressed. See
[copy, mount, or share?](../../patterns/data-movement.md).

### Pins (data tier: RODA, published)

| | |
|---|---|
| bwa | `quay.io/aarchbio/bwa@sha256:19f0ecea…` (0.7.19-r1273) |
| index | `s3://1000genomes/technical/reference/GRCh38_reference_genome/` — `.amb .ann .bwt .pac .sa` |
| reads | `s3://1000genomes/phase3/data/HG00096/sequence_read/SRR062634_{1,2}.filt.fastq.gz` |

Both are **published** objects on the 1000genomes RODA bucket, read with a *signed* request
(no requester-pays), and cached once into your own bucket so every run stages same-region.
`make stage` does that; the cache is byte-identical to upstream, not a derived copy.

Also: spawn's task shell does not inherit the image's `PATH`, so `/opt/conda/bin` must be
exported. And nothing in the pipeline may stop reading early — `| head` or `awk … exit` would
SIGPIPE bwa and, under `set -o pipefail`, kill the task after 13 minutes of completed work.

### Not shipped yet

**The sort/index step.** This recipe now ends at `aln.sam.gz`; the chr20-scale
`samtools sort` task it used to carry was removed with the toy fixture rather than rewritten
for 5 GiB of input. Sorting that is a real task with a real cost (temp space in a tmpfs `/tmp`
is the constraint) and it has not been measured, so it is absent rather than guessed at.

### Run + verify

```sh
make run RECIPE=bwa-samtools
make ls  RECIPE=bwa-samtools
```

Expect `smoke-check.txt` with `sam_records 48392167`, `pct_mapped 99.91`, and
`aln_sam_gz_bytes` above 5 GB.

</details>
