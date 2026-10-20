---
tool: bwa-mem2
tool_version: "2.3"
image: quay.io/aarchbio/bwa-mem2@sha256:f9759b09a39aab57d879babbcb5a876a9ed4a55a698eb10b476f70a9cec81c15
images:
  - quay.io/aarchbio/bwa@sha256:19f0eceab80740b821be7ada082d4434acf778912aac658dd1b4c6692dd2e9ba
  - quay.io/aarchbio/bwa-mem2@sha256:f9759b09a39aab57d879babbcb5a876a9ed4a55a698eb10b476f70a9cec81c15
last_verified_x86_64: 2026-10-13
last_verified_amd: 2026-10-19
images_x86_64:
  - quay.io/biocontainers/bwa-mem2@sha256:374f4b910b4b04d32772fccd4cab1cdcd0758356856a960cfa8b1edebfd38c9f
  - quay.io/biocontainers/bwa@sha256:99a35e5ee4e9c329e8746c4689890b97a3ac5620cb36d374cba69ba52016e72a
spawn_version: 0.111.4
last_verified: 2026-10-13
---
# bwa-mem2 — 1.6–2.1× faster than bwa, with byte-identical output; whether it is cheaper depends on the box

Aligns a whole sequencing run (24.1M pairs) to all of GRCh38 with both aligners on one box, and prices the trade. For anyone deciding whether to swap `bwa mem` for `bwa-mem2`.

> **On the box each aligner needs, it costs more; on the same box, it costs less.** Its 3.14× index forces 4× the RAM, so bwa on `c8g` against bwa-mem2 on `r8g` came out ~45% dearer per result. With both on the same `r8` box, bwa-mem2 was 24–40% cheaper on every chip ([measured](../../measurements/bwa-mem2-real/README.md)). The escape from the RAM cost is to stop copying the index.

## Run it

```bash
make stage RECIPE=bwa-mem2   # once: the 3.0 GB reference, then build the mem2 index (~14 min)
for s in $(make -s spec RECIPE=bwa-mem2); do spawn task run --spec "$s" --wait; done   # bwa leg then bwa-mem2 leg, same box, same reads
make ls    RECIPE=bwa-mem2   # both smoke-check.txt files

bwa-mem2 index GRCh38_full_analysis_set_plus_decoy_hla.fa      # ~14 min, 16.5 GiB out
bwa-mem2 mem -t 16 -R '@RG\tID:SRR062634\tSM:HG00096\tPL:ILLUMINA' \
  GRCh38_full_analysis_set_plus_decoy_hla.fa \
  SRR062634_1.filt.fastq.gz SRR062634_2.filt.fastq.gz | gzip -1 > aln.sam.gz
```

## Which box — [measured](../../measurements/bwa-mem2-real/README.md) on a whole genome (HG00096, 73.3M pairs), `r8*.4xlarge`, 16 threads

| chip | bwa-mem2 kernel | bwa align | **bwa-mem2 align** | bwa billed $ | **bwa-mem2 billed $** |
|---|---|---|---|---|---|
| **Graviton4** | `AVX` (128-bit) | 2,383 s | 1,517 s | 0.697 | **0.531** |
| Xeon 6975P-C | `avx512bw` | 3,080 s | 1,505 s | 1.115 | 0.674 |
| EPYC 9R45 | `avx512bw` | 1,992 s | **1,030 s** | 0.866 | 0.579 |

**Graviton4 is cheapest per genome for either aligner, and AMD is fastest.** bwa-mem2's x86 margin (about 2×) includes its AVX-512 kernel, which the arm64 build doesn't have. All 148,188,350 records came out identical across the three chips and between the two aligners, once bwa's newer `MQ` tag is set aside. On a fixed box bwa-mem2 is cheaper, but the box is not fixed. On one run (`SRR062634`, table below the fold) it was **≈1.6× faster for 3.14× the index**, and the index sets the instance, not just the disk bill. Staging is [tmpfs at half of RAM](../../practices/container-path.md), so 16.5 GiB of index needs ~34 GiB to stage plus ~18 for the aligner: a 128 GiB box, where bwa does the job on 32. Per result on each tool's cheapest viable box, **bwa `c8g.4xlarge` $0.1508 vs bwa-mem2 `r8g.4xlarge` $0.2186** — the speedup is real, the saving is not. Swap it in only if wall-clock is what you are buying — and note **the index build pays back in wall clock and never in cost**: mem2 saves 398 s per alignment against the 831 s its index took to build, so you are ahead on time from the third alignment on, while the cheapest-box cost gap *widens* with every run.

## Make it yours

| In the recipe | Swap for | What to know |
|---|---|---|
| the GRCh38 index + `SRR062634` | your reference + reads | both aligners read *identical* staged bytes, which is what makes the record-count identity mean anything. |
| copying the 16.5 GiB index | **mounting it** | removes the staging RAM that drives the cost above; an immutable index is the textbook case ([measured](../../measurements/star-real/README.md)). |
| `-t 16` | your core count | bwa scales near-linearly to 64 ([measured](../../measurements/bwa-real/README.md)); expect the same shape. |

**Leave the workload** — a complete run against the whole analysis set, so the ratio transfers. **Scale it** by fixing the data path before adding cores: the index, not the chip, is what makes bwa-mem2 expensive.

<details>
<summary>As shipped: the cross-implementation identity, the index build, pins</summary>

### The single-run trade (`SRR062634`, one `r8g.4xlarge`, 16 threads, timed through `| gzip -1`)

| | wall (n=2) | reads/s | index on disk | records |
|---|---|---|---|---|
| `bwa mem` | 817 s, 927 s | 52,101–59,116 | **5.26 GiB** | 48,817,006 |
| `bwa-mem2 mem` | **542 s, 529 s** | **89,110–91,300** | **16.48 GiB** | 48,817,006 |

### The identity, and what it does and does not say

| observable | assertion | observed |
|---|---|---|
| ALT contigs read | > 0 (the analysis set used ALT-aware) | **3171**, both aligners |
| SAM records | **exactly 48,817,006** | **48,817,006**, both aligners |
| `aln.sam.gz` | passes `gzip -t` | passes |

**bwa-mem2 documents output identical to `bwa mem`, so this is an exact assertion rather than a
band** — and it held on all four runs (two per aligner), against a count that was itself
[cross-validated by two independent data paths](../bwa-samtools/README.md).

What it does *not* say is that the files are byte-identical: `aln.sam.gz` came out
5,779,553,185 bytes from bwa-mem2 and 5,820,842,885 from bwa, 0.7% apart. Same records, same
count, same ALT handling — different compressed size. Assert the record count, which is the claim
the tool makes; asserting the bytes would assert a serialisation detail.

### The index build

`r8g.4xlarge`, 16 threads: **831 s** to produce **17,695,876,677 bytes** against bwa's
5,644,394,452 — **3.14×**. It ships as one flat `mem2-index.tar` because
[a directory output cannot work on the container path](../../practices/container-path.md), and the
consuming task untars it. Build once and reuse: at 831 s plus $0.23 it is not something to repeat
per sample.

A detail worth knowing before you expect x86 numbers here: bioconda's x86 bwa-mem2 ships
`bwa-mem2.avx512bw`, `.avx2`, `.avx`, `.sse42` and `.sse41` binaries and dispatches on CPU features at run time.
The arm64 build ships **one** binary — there is no AVX to dispatch to. The 1.6× above is therefore
what bwa-mem2's re-engineering buys *without* its x86 vector kernels, which is the number that
matters on Graviton and is not the number its README quotes.

### Run-to-run variance

Two runs per aligner on the same instance type: bwa 817 s and 927 s (13% apart), bwa-mem2 542 s
and 529 s (2.4% apart). The bwa spread is wide enough that a single-run ratio would have been
misleading either way, which is why the table gives both runs rather than a mean. Every run
returned the same 48,817,006 records, so the variance is timing, not science.

### Pins

| | data tier |
|---|---|
| bwa-mem2 | `quay.io/aarchbio/bwa-mem2@sha256:f9759b09…` (2.3, `linux/arm64`, asserted from inside via ELF `e_machine` == the host's: 183 on arm64) |
| bwa | `quay.io/aarchbio/bwa@sha256:19f0ecea…` (0.7.19-r1273) |
| reference | `s3://1000genomes/technical/reference/GRCh38_reference_genome/` — published, copied server-side |
| reads | `s3://1000genomes/phase3/data/HG00096/sequence_read/SRR062634_{1,2}.filt.fastq.gz` |

The reference `.fa` is staged here because bwa-mem2 needs it to build its index; `bwa-samtools`
never stages it, reading only `.amb/.ann/.bwt/.pac/.sa`. Both aligners read the same `.alt`, which
is what makes the ALT-aware counts comparable.

### On x86_64

All three tasks on same-generation Intel (`r8i.4xlarge`), from bioconda's own x86 builds of the
same versions — `quay.io/biocontainers/bwa-mem2@sha256:374f4b91…` (2.3) and
`quay.io/biocontainers/bwa@sha256:99a35e5e…` (0.7.19), both `linux/amd64`; not cosign-signed, so
the digest is the pin. The index was rebuilt on x86 from the same staged reference:

| | arm64 | x86_64 |
|---|---|---|
| ELF `e_machine` (asserted == host) | 183 | 62 |
| bwa-mem2 kernel that ran | the one arm64 binary | `bwa-mem2.avx512bw` |
| mem2 index bytes (5 files) | 17695876677 | 17695876677 |
| ALT contigs read, both aligners | 3171 | 3171 |
| SAM records, bwa / bwa-mem2 | 48817006 / 48817006 | 48817006 / 48817006 |

The cross-implementation identity holds on x86 as well: the dispatched AVX-512 kernel and the
arm64 build give the same record count as `bwa mem` on either chip. The x86 package ships five
kernels (`.avx`, `.avx2`, `.avx512bw`, `.sse41`, `.sse42`). The `aln.sam.gz` and index-tar sizes
differ from arm64 only because bioconda's x86 images use BusyBox `gzip` and `tar`; the ARM
images use GNU's.

### Run + verify

```sh
make run RECIPE=bwa-mem2                  # Graviton4
make run RECIPE=bwa-mem2 ARCH=x86_64      # same specs, x86 pins; derived files go to inputs-x86_64/
make ls  RECIPE=bwa-mem2
```

Expect both `smoke-check.txt` files to report `sam_records 48817006` and
`alt_contigs_read 3171`. The checks run inside the tasks; the bucket listing is the second half,
because [stage-out happens even when a command fails](../../practices/container-path.md).

</details>
