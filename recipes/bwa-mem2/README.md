---
tool: bwa-mem2
tool_version: "2.2.1"
image: quay.io/aarchbio/bwa-mem2@sha256:f9759b09a39aab57d879babbcb5a876a9ed4a55a698eb10b476f70a9cec81c15
images:
  - quay.io/aarchbio/bwa@sha256:19f0eceab80740b821be7ada082d4434acf778912aac658dd1b4c6692dd2e9ba
  - quay.io/aarchbio/bwa-mem2@sha256:f9759b09a39aab57d879babbcb5a876a9ed4a55a698eb10b476f70a9cec81c15
last_verified_x86_64: 2026-10-23
last_verified_amd: 2026-10-23
images_x86_64:
  - quay.io/biocontainers/bwa-mem2@sha256:374f4b910b4b04d32772fccd4cab1cdcd0758356856a960cfa8b1edebfd38c9f
  - quay.io/biocontainers/bwa@sha256:99a35e5ee4e9c329e8746c4689890b97a3ac5620cb36d374cba69ba52016e72a
spawn_version: 0.140.0
last_verified: 2026-10-23
---
# bwa-mem2 — 1.6–2.1× faster than bwa, with byte-identical output; whether it is cheaper depends on the box

Aligns a whole sequencing run (24.1M pairs) to all of GRCh38 with both aligners on one box, and prices the trade. For anyone deciding whether to swap `bwa mem` for `bwa-mem2`.

> **On the box each aligner needs, it costs the same; on the same box, it costs less.** Its 3.14× index forces 4× the RAM, yet bwa-mem2 on `r8g` came out within 1% of bwa on `c8g` per result. With both on the same `r8` box, bwa-mem2 was 24–40% cheaper on every chip ([measured](../../measurements/bwa-mem2-real/README.md)). The escape from the RAM cost is to stop copying the index.

## Run it

```bash
make stage RECIPE=bwa-mem2   # once: the 3.0 GB reference, then build the mem2 index (~14 min)
for s in $(make -s spec RECIPE=bwa-mem2); do spawn task run --spec "$s" --wait; done   # bwa leg then bwa-mem2 leg, same box, same reads
make ls    RECIPE=bwa-mem2   # both smoke-check.txt files

bwa-mem2 index GRCh38_full_analysis_set_plus_decoy_hla.fa      # ~14 min, 16.5 GiB out
bwa-mem2 mem -t 16 -R '@RG\tID:SRR062634\tSM:HG00096\tPL:ILLUMINA' \
  GRCh38_full_analysis_set_plus_decoy_hla.fa \
  SRR062634_1.filt.fastq.gz SRR062634_2.filt.fastq.gz > aln.sam   # compress afterwards, untimed
```

## Which box — [measured](../../measurements/bwa-mem2-real/README.md) on a whole genome (HG00096, 73.3M pairs), `r8*.4xlarge`, 16 threads

| chip | bwa-mem2 kernel | bwa align | **bwa-mem2 align** | bwa billed $ | **bwa-mem2 billed $** |
|---|---|---|---|---|---|
| **Graviton4** | `AVX` (128-bit) | 2,383 s | 1,517 s | 0.697 | **0.531** |
| Xeon 6975P-C | `avx512bw` | 3,080 s | 1,505 s | 1.115 | 0.674 |
| EPYC 9R45 | `avx512bw` | 1,992 s | **1,030 s** | 0.866 | 0.579 |

**Graviton4 is cheapest per genome for either aligner, and AMD is fastest.** bwa-mem2's x86 margin (about 2×) includes its AVX-512 kernel, which the arm64 build doesn't have. All 148,188,350 records came out identical across the three chips and between the two aligners, once bwa's newer `MQ` tag is set aside. On a fixed box bwa-mem2 is cheaper, but the box is not fixed. On one run (`SRR062634`, table below the fold) it was **1.55× faster on Graviton4 for 3.14× the index**, and the index sets the instance, not just the disk bill. Staging is [tmpfs at half of RAM](../../practices/container-path.md), so the 16.5 GiB index tar needs a 64 GiB box before the aligner's 25.8 GiB peak is counted, where bwa does the job on 32 (this recipe runs `r8g`; `m8g.4xlarge` was not tried). Per result on each tool's cheapest viable box, both writing SAM to disk, both on spawn's task window, **bwa `c8g.4xlarge` $0.1572 ([bwa-samtools](../bwa-samtools/README.md), pre-`.alt`, which runs 7% shorter than ALT-aware) vs bwa-mem2 `r8g.4xlarge` $0.1519** ([window](../../measurements/bwa-mem2-real/results/billed-windows.txt)) — 3% apart, inside the 13% between bwa's own two runs below, so at n = 1 a tie and the speedup costs nothing (EC2 launch to terminate says the same: $0.1677 vs $0.1691); on compute alone, ALT-aware, bwa-mem2 is cheaper ($0.1330 vs $0.1436). **The index build pays back in wall clock and never in cost**: mem2 saves 281 s per alignment on Graviton4 against the 831 s its index took to build, so you are ahead on time from the third alignment on, while the $0.23 build is never earned back by per-run differences of a cent or less.

## Make it yours

| In the recipe | Swap for | What to know |
|---|---|---|
| the GRCh38 index + `SRR062634` | your reference + reads | both aligners read *identical* staged bytes, which is what makes the record-count identity mean anything. |
| copying the 16.5 GiB index | **mounting it** | removes the staging RAM that drives the cost above; an immutable index is the textbook case ([measured](../../measurements/star-real/README.md)). |
| `-t 16` | your core count | bwa scales near-linearly to 64 ([measured](../../measurements/bwa-real/README.md)); expect the same shape. |

**Leave the workload** — a complete run against the whole analysis set, so the ratio transfers. **Scale it** by fixing the data path before adding cores: the index, not the chip, is what makes bwa-mem2 expensive.

<details>
<summary>As shipped: the cross-implementation identity, the index build, pins</summary>

### The single-run trade (`SRR062634`, one `r8*.4xlarge` per chip, 16 threads, plain SAM)

The timer covers the aligner alone, writing SAM to the container disk; nothing compresses on the
timed path. (An earlier version piped through `gzip -1`, which on the x86 images is BusyBox gzip
and timed the compressor: 1,114 s recorded on AMD for a 337 s alignment.) n = 1 per cell.

| chip | `bwa mem` | **`bwa-mem2 mem`** | speedup | bwa billed $ | **bwa-mem2 billed $** | records, both |
|---|---|---|---|---|---|---|
| Graviton4 `r8g` | 789 s | **508 s** (`AVX`) | 1.55× | 0.232 | **0.169** | 48,817,006 |
| Xeon 6975P-C `r8i` | 1,028 s | 489 s (`AVX512`) | 2.10× | 0.366 | 0.199 | 48,817,006 |
| EPYC 9R45 `r8a` | 660 s | **332 s** (`AVX512`) | 1.99× | 0.270 | 0.185 | 48,817,006 |

These agree with the whole-genome [measurement](../../measurements/bwa-mem2-real/README.md) to
within 4% per run (its `SRR062634` rows: 761/495, 1,014/496, 654/337 s), and so do the ratios
(1.57×, 2.05×, 1.93× over three runs). Index on disk: bwa 5.26 GiB, bwa-mem2 16.48 GiB. *Billed* is
EC2 launch to terminate, at the instance's on-demand rate.

### The identity, and what it does and does not say

| observable | assertion | observed |
|---|---|---|
| ALT contigs read | > 0 (the analysis set used ALT-aware) | **3171**, both aligners |
| SAM records | **exactly 48,817,006** | **48,817,006**, both aligners |
| SAM header | `@SQ` lines > 0 | 3,366, both aligners |

**bwa-mem2 documents output identical to `bwa mem`, so this is an exact assertion rather than a
band** — and it held on all six runs (one per aligner per chip), against a count that was itself
[cross-validated by two independent data paths](../bwa-samtools/README.md).

What it does *not* say is that the files are byte-identical: the SAM came out 16,938,011,182
bytes from bwa-mem2 and 17,325,079,973 from bwa, on every chip. The difference is bwa's mate-MAPQ
`MQ` tag, which bwa-mem2 does not write; with it removed the bodies hash the same
([measured](../../measurements/bwa-mem2-real/README.md)). Assert the record count, which is the
claim the tool makes here.

### The index build

`r8g.4xlarge`, 16 threads: **831 s** to produce **17,695,876,677 bytes** against bwa's
5,644,394,452 — **3.14×**. It ships as one flat `mem2-index.tar`, one
[staged object for a big intermediate](../../practices/container-path.md), and the consuming task
untars it. Build once and reuse: at 831 s plus $0.23 it is not something to repeat
per sample.

A detail worth knowing before you expect x86 numbers here: bioconda's x86 bwa-mem2 ships
`bwa-mem2.avx512bw`, `.avx2`, `.avx`, `.sse42` and `.sse41` binaries and dispatches on CPU features at run time.
The arm64 build ships **one** binary — there is no AVX to dispatch to. The 1.55× above is therefore
what bwa-mem2's re-engineering buys *without* its x86 vector kernels, which is the number that
matters on Graviton and is not the number its README quotes.

### Run-to-run variance

Earlier Graviton4 runs (through `gzip`, n = 2) put bwa at 817 and 927 s, 13% apart, and bwa-mem2
at 542 and 529 s. The table above is n = 1 per chip; the record count is identical on every run,
so the spread is timing, not science.

### Pins

| | data tier |
|---|---|
| bwa-mem2 | `quay.io/aarchbio/bwa-mem2@sha256:f9759b09…` (package 2.3; `bwa-mem2 version` prints **2.2.1**, as does bioconda's x86 build; `linux/arm64`, asserted from inside via ELF `e_machine` == the host's: 183 on arm64) |
| bwa | `quay.io/aarchbio/bwa@sha256:19f0ecea…` (0.7.19-r1273) |
| reference | `s3://1000genomes/technical/reference/GRCh38_reference_genome/` — published, copied server-side |
| reads | `s3://1000genomes/phase3/data/HG00096/sequence_read/SRR062634_{1,2}.filt.fastq.gz` |

The reference `.fa` is staged here because bwa-mem2 needs it to build its index; `bwa-samtools`
never stages it, reading only `.amb/.ann/.bwt/.pac/.sa`. Both aligners read the same `.alt`, which
is what makes the ALT-aware counts comparable.

### On x86_64

All three tasks on same-generation Intel (`r8i.4xlarge`) and AMD (`r8a.4xlarge`), from bioconda's own x86 builds of the
same versions — `quay.io/biocontainers/bwa-mem2@sha256:374f4b91…` (package 2.3, binary 2.2.1) and
`quay.io/biocontainers/bwa@sha256:99a35e5e…` (0.7.19), both `linux/amd64`; not cosign-signed, so
the digest is the pin. The index was rebuilt on x86 from the same staged reference:

| | arm64 | x86_64 |
|---|---|---|
| ELF `e_machine` (asserted == host) | 183 | 62 |
| bwa-mem2 kernel that ran | the one arm64 binary | `bwa-mem2.avx512bw` |
| mem2 index bytes (5 files) | 17695876677 | 17695876677 |
| ALT contigs read, both aligners | 3171 | 3171 |
| SAM records, bwa / bwa-mem2 | 48817006 / 48817006 | 48817006 / 48817006 |
| SAM bytes, bwa / bwa-mem2 | 17325079973 / 16938011182 | same |

The cross-implementation identity holds on x86 as well: the dispatched AVX-512 kernel and the
arm64 build give the same record count as `bwa mem` on either chip. The x86 package ships five
kernels (`.avx`, `.avx2`, `.avx512bw`, `.sse41`, `.sse42`). The SAM is byte-for-byte the same
size on all three chips; the index tar differs from arm64 only because bioconda's x86 images use
BusyBox `tar`, the ARM images GNU's.

### Run + verify

```sh
make run RECIPE=bwa-mem2                  # Graviton4
make run RECIPE=bwa-mem2 ARCH=x86_64      # same specs, x86 pins; derived files go to inputs-x86_64/
make run RECIPE=bwa-mem2 ARCH=amd         # same x86 pins on c8a/r8a; inputs-amd/, measurements-amd/
aws s3 ls "s3://$(make -s print-bucket)/measurements/bwa-mem2-real/" --recursive   # both smoke-check.txt
```

Expect both `smoke-check.txt` files to report `sam_records 48817006` and
`alt_contigs_read 3171`, and bwa-mem2's to report `version 2.2.1`. The SAM goes to `/var/tmp`
(container disk, `disk_gib`), not the half-RAM tmpfs that holds the staged inputs. The checks run inside the tasks; the bucket listing is the second half,
because [stage-out happens even when a command fails](../../practices/container-path.md).

</details>
