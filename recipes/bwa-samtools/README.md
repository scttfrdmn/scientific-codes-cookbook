---
tool: bwa
tool_version: "0.7.19-r1273"
image: quay.io/aarchbio/bwa@sha256:19f0eceab80740b821be7ada082d4434acf778912aac658dd1b4c6692dd2e9ba
last_verified_x86_64: 2026-10-24
last_verified_amd: 2026-10-24
images_x86_64:
  - quay.io/biocontainers/bwa@sha256:99a35e5ee4e9c329e8746c4689890b97a3ac5620cb36d374cba69ba52016e72a
  - quay.io/biocontainers/samtools@sha256:a130447589651ed09252aa95a5e4f4132942cdb54d835d81a04a9a930d656561
spawn_version: 0.142.0
last_verified: 2026-10-24
---
# bwa mem — a whole sequencing run against GRCh38

Aligns **24.1M read pairs (4.83 Gbp)** to the complete GRCh38 analysis set on Graviton, with cost per result measured across four Graviton generations. For anyone aligning short reads and choosing a box.

> **Scope.** One 1000 Genomes run (`SRR062634`, HG00096) against the *published* GRCh38 index, ALT-aware — no index build. Alignment only.

## Run it

```bash
make stage RECIPE=bwa-samtools   # once — caches the RODA index + reads into your bucket
for s in $(make -s spec RECIPE=bwa-samtools); do spawn task run --spec "$s" --wait; done   # align (~19 min) then sort+index (~2 min), self-terminating
make ls    RECIPE=bwa-samtools   # aln.sam.gz, aln.sorted.bam + .bai, smoke checks
bwa mem -t 16 -R '@RG\tID:SRR062634\tSM:HG00096\tPL:ILLUMINA' \
  GRCh38_full_analysis_set_plus_decoy_hla.fa \
  SRR062634_1.filt.fastq.gz SRR062634_2.filt.fastq.gz > aln.sam   # then, untimed: gzip -1 < aln.sam > aln.sam.gz
```

## Which box — measured (same bytes, 16 threads, pre-`.alt` so all rows are comparable; ALT-aware adds ~4.6%)

| generation | instance | wall | reads/s | $/hr | **billed $/result** |
|---|---|---|---|---|---|
| Graviton2 | `c6g.4xlarge` | 1086 s | 44,560 | 0.5440 | 0.1841 |
| Graviton3 | `c7g.4xlarge` | 890 s | 54,373 | 0.5800 | 0.1621 |
| Graviton4 | `c8g.4xlarge` | 757 s | 63,926 | 0.6381 | 0.1508 |
| **Graviton5** | `c9g.4xlarge` | **590 s** | **82,021** | 0.6955 | **0.1320** |
| Graviton4, SAM to disk | `c8g.4xlarge` | 756 s | 64,011 | 0.6381 | 0.1572 |
| Intel Xeon 6975P-C, SAM to disk | `c8i.4xlarge` | 1081 s | 44,766 | 0.7497 | 0.2580 |
| AMD EPYC 9R45, SAM to disk | `c8a.4xlarge` | 718 s | 67,399 | 0.8622 | 0.2038 |

**Take the newest generation, and 16 cores.** Graviton2→5: rate card +28%, wall −46%, result **28% cheaper**; peak RSS is 8.7 GiB on all four, so 32 GiB is right. More cores scale almost perfectly (3.8× on 4×) and compute-only cost barely moves (+5.7%) — but **billed cost rises 40%**, because ~95 s of boot-and-staging is charged whatever the box costs. 64 cores buys wall-clock, not savings. Same generation on x86 (bioconda's x86 build of the same version, compared against Graviton4's SAM-to-disk row, whose billed time includes counting the SAM afterwards): over two clean runs AMD is 1.05–1.19× faster than Graviton4 but 14–28% dearer per result on compute, and Intel is 1.22–1.43× slower, on 8 SMT cores. The SAM record counts are identical on all three ([measured](../../measurements/bwa-real/README.md)).

## Make it yours

| In the recipe | Swap for | What to know |
|---|---|---|
| `SRR062634` (HG00096) | your FASTQs — edit `stage-inputs.sh` | one run per task; a cohort is this task [fanned out](../../patterns/job-arrays.md), each sized at 16 cores. |
| published GRCh38 index | your own reference | `bwa index` on a human genome is ~1 h and is **not** needed here: RODA ships one. Build only for a non-model organism. |
| `-t 16` | `-t 32/48/64` | faster, *more* expensive per result. Memory grows ~0.21 GiB/thread above 16 (8.7 → 18.7 GiB at 64). |
| `gzip -1` after the aligner | skipping it | takes the intermediate from 17.3 GB to 5.8 GB, which the next task pays for. Never `\| gzip` on the timed path: BusyBox gzip on the x86 images sets the pace (below). |

**Leave the workload** — a real run against a real genome, so the numbers above transfer to your data at the same depth. **Scale it** by fanning out samples, not by growing the box.

<details>
<summary>As shipped: the exact assertions, the full knee table, the data path, pins</summary>

### The checks

| observable | assertion | observed |
|---|---|---|
| **ALT contigs read** | **> 0** — the analysis-set reference must be used ALT-aware | **3171** |
| SAM records | **exactly 48,817,006** | **48,817,006** |
| mapped | ≥ 99.9% | **99.91%** |
| SAM header | `@SQ` lines > 0 | **3,366** |
| `aln.sam.gz` | > 1 GB | **5,820,842,885 bytes** |
| reproducibility | same count from two independent **data paths** | **identical** |

### The assertion this recipe got wrong first, and how

The first version of this page asserted **48,392,167** records. That number was real,
reproducible on two boxes — and **wrong**, because the staging step copied five index files and
omitted the 476 KB `GRCh38_full_analysis_set_plus_decoy_hla.fa.alt`. Without it `bwa mem` logs
`read 0 ALT contigs`, skips ALT-aware mapping, and emits **424,839 fewer records**. Exit 0, a
plausible count, an exact-looking assertion, and no warning anywhere.

It surfaced only when the same workload ran against a [lith mount of the published
prefix](../../measurements/lith-vs-copy/README.md), which exposes the directory *as deposited* —
`.alt` included — and returned a different number. So the fix was not a hash edit: `.alt` is now
staged, the recipe **asserts `ALT contigs read > 0`**, and the count was re-derived from a fresh
ALT-aware run rather than copied from the mount ([the pin-swap
rule](../../practices/cross-checks.md) — a changed input means a re-run).

**48,817,006 is now cross-validated by two independent data paths** — a lith mount and a local
copy including `.alt`, on different instances. The lesson generalises past bwa: **copying makes
you enumerate a dataset, and the file you omit is the one you did not know mattered.** The
ALT-contig assertion is the cheap guard, because it checks the *mechanism* rather than the
output.

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

**No speed knee through 64 cores, and the cost minimum at 16** — none of the three shapes in
[sizing](../../patterns/sizing.md), and the same reading as [bowtie2](../bowtie2/README.md): the
answer is stable and compute cost flat, so the only thing bending its curve is fixed overhead,
which makes the data path the lever, not the chip.

### The data path

Staging in is **8.9 GiB**, and dropping one file is what makes that tolerable: `bwa mem`
reads `<prefix>.amb/.ann/.bwt/.pac/.sa` only, so the **3.0 GiB `.fa` is never staged** —
12 GiB down to 8.9. `bwa mem` writes plain SAM (17.3 GB) to the container disk (`/var/tmp`,
`disk_gib: 40`) and is timed alone; `gzip -1` runs afterwards: 226 s on Graviton4 (GNU gzip),
1,276 s on Intel and 1,073 s on AMD (BusyBox gzip, single-threaded). An earlier version tee'd the
SAM through `gzip -1` inside the timer. On Graviton4 that cost nothing (800 s, against 810 without),
but on x86 the compressor set the pace: 1,561 and 1,180 s recorded for alignments that take 987 and
681 s. See [copy, mount, or share?](../../patterns/data-movement.md).

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

### The sort step, and the RAM it spends twice

`samtools sort` turns the 5.4 GiB `aln.sam.gz` into a coordinate-sorted, indexed
**4,568,357,482-byte BAM** — the form every downstream caller actually wants. Measured:

| box | threads | sort | index | tmpfs peak | billed | $ |
|---|---|---|---|---|---|---|
| `m8g.4xlarge` (64 GiB) | 16 | **65 s** | 3 s | 9,786 / 31,566 MB | 125 s | **0.0249** |
| `m8g.2xlarge` (32 GiB) | 8 | 87 s | 4 s | **12,419 / 15,746 MB** | 165 s | **0.0165** |

The sorting itself is cheap. The constraint is that on the task path `sort -T /tmp` writes its
temp files into **tmpfs, which is RAM** ([the container path](../../practices/container-path.md)) —
so the step spends the same pool twice, on `-m` × `-@` of sort buffers *and* on temp files. That
inverts the obvious economy: **shrinking the box raised the tmpfs peak**, from 9.8 GiB to 12.4 GiB,
because 8 threads buffer 8 GiB where 16 buffer 16, and whatever does not fit in buffers spills to
temp. At 79% of tmpfs, 32 GiB is the practical floor for a whole-genome BAM; 64 GiB is comfortable.

The checks are a conservation identity and two structural ones, all exact and none able to go
flaky: the record count must survive sorting (**48,817,006**, unchanged — sorting is a
permutation), a BAM begins `1f8b0804`, and `samtools quickcheck` verifies the EOF block so a
truncated file cannot pass. The BAM is *not* byte-identical across thread counts — 4,568,357,482
at 16 threads against 4,568,357,483 at 8 — which is BGZF block packing, and the reason the
assertion is on records rather than bytes.

**Two mapped percentages appear in this catalog and both are right.** The align task counts
`$3 != "*"` (a reference name is set) and reports **99.91%**; `samtools flagstat` counts the 0x4
flag unset and reports **99.77%**. The gap is 48,774,176 − 48,703,954 = **70,222**, exactly
flagstat's singleton count: unmapped mates placed at their partner's coordinates carry a reference
name while still being flagged unmapped. Different questions, not a discrepancy.

### On x86_64

The same three tasks on same-generation Intel (`c8i.4xlarge` / `m8i.4xlarge`), from bioconda's own
x86 builds of the same versions — `quay.io/biocontainers/bwa@sha256:99a35e5e…` (0.7.19) and
`quay.io/biocontainers/samtools@sha256:a1304475…` (1.24), both `linux/amd64`; not cosign-signed,
so the digest is the pin. Same staged bytes:

| | arm64 | x86_64 |
|---|---|---|
| `sam_records` / mapped | 48817006 / 48774176 | 48817006 / 48774176 |
| `pct_mapped` (align / flagstat) | 99.91 / 99.77 | 99.91 / 99.77 |
| chr20 baseline primary / mapped | 800000 / 233313 | 800000 / 233313 |
| `aln.sorted.bam` | 4,568,357,482 B | same size |
| `.bai`, `flagstat.txt`, `aln_chr20smoke.sam` | — | **byte-identical** to arm64 (sha256) |

The alignment is the same: the index and flagstat match byte for byte, and AMD (`c8a`/`m8a`) gives the
same values as Intel in every row.
`aln.sam.gz` alone differs (5.82 GB vs 5.00 GB) because the compressor does — the ARM image ships
GNU gzip 1.12, bioconda's x86 image BusyBox gzip — so the bytes inside it, which the sorted BAM is
built from, are the same. The logs and smoke-check files differ only in wall times and tmpfs
high-water marks.

### Run + verify

```sh
make run RECIPE=bwa-samtools                  # Graviton4
make run RECIPE=bwa-samtools ARCH=x86_64      # same specs, x86 pins, writes runs-x86_64/
make ls  RECIPE=bwa-samtools
```

Expect `smoke-check.txt` with `sam_records 48817006`, `sq_lines 3366`, `pct_mapped 99.91`, and
`aln_sam_gz_bytes` above 5 GB.

</details>
