---
tool: seqkit
tool_version: 2.13.0
image: quay.io/aarchbio/seqkit@sha256:5478aaad4dd7bf7d7f02eee168ee3ad90d17b6729ab5e889a9385b4458cde7c5
last_verified_x86_64: 2026-10-13
last_verified_amd: 2026-10-18
images_x86_64:
  - quay.io/biocontainers/seqkit@sha256:0e14f53b486c6b6e199e525f3f1e7494b59b580f835f7835e497b46f99267b6a
spawn_version: 0.111.4
last_verified: 2026-10-01
---
# seqkit — stats and format conversion over a complete 48M-read run

Summarises and converts the whole SRR062634 run (4.83 Gbp) in 93 s. For anyone reaching for seqkit as the first step of a pipeline.

## Run it

```bash
make stage RECIPE=bwa-samtools   # seqkit reads the same reads bwa aligns
spawn task run --spec "$(make -s spec RECIPE=seqkit)" --wait   # ~4 min on c8g.2xlarge, self-terminating
make ls    RECIPE=seqkit         # stats.tsv + fa_stats.tsv + smoke-check.txt

seqkit stats -T -j 8 SRR062634_1.filt.fastq.gz SRR062634_2.filt.fastq.gz
seqkit fq2fa -j 8 SRR062634_1.filt.fastq.gz -o r1.fa.gz
```

## Make it yours

| In the recipe | Swap for | What to know |
|---|---|---|
| the complete SRR062634 run | your FASTQs | reused byte-for-byte from [bwa](../bwa-samtools/README.md) — nothing re-staged. |
| `stats` + `fq2fa` | `grep`, `subseq`, `rmdup`, `sample`, … | same binary, same streaming shape — but `rmdup` holds a hash per record, so it is the one that is not footprint-free. |
| `-j 8` | fewer threads | `stats` is gzip-decode bound; threads help the decode, not the counting. |

**Leave the workload** — a complete run, so the timings and the staging footprint transfer.
**Scale it** by read count; everything here streams, so a bigger file is a longer run on the same box
until the staged input stops fitting.

## Which box — [measured](../../measurements/seqkit-real/README.md), same reads, 8 vCPU throughout

| generation | instance | `stats` + `fq2fa` | compute $ | billed $ |
|---|---|---|---|---|
| Graviton2 | `c6g.2xlarge` | 143 s | 0.0108 | 0.0227 ‡ |
| Graviton3 | `c7g.2xlarge` | 110 s | 0.0089 | 0.0186 ‡ |
| Graviton4 | `c8g.2xlarge` | 93 s | 0.0082 | **0.0121** |
| **Graviton5** | `c9g.2xlarge` | **76 s** | **0.0073** | 0.0160 ‡ |
| Intel Xeon 6975P-C | `c8i.2xlarge` (4 cores × 2) | 86 s | 0.0090 | 0.0132 |
| AMD EPYC 9R45 | `c8a.2xlarge` (8 cores) | 72 s | 0.0086 | 0.0127 |

**1.88× over four generations**; in the 8th, AMD is fastest and Graviton4 cheapest per result, and every
count is identical on all six chips. **Read the compute column:** at ~90 s of work a third of the billed window is fixed
startup, and ‡ the Graviton2/3/5 billed windows are not in the committed record, so billed is not compared across generations ([which comparison applies](../../patterns/cost-per-result.md)). Billed is spawn's task window on the 8th-generation rows.

<details>
<summary>As shipped: a read count three tools agree on, conversion conservation, pins</summary>

### The checks

| observable | assertion | observed |
|---|---|---|
| R1 records | exactly 24,148,993 | **24,148,993** |
| R2 records | == R1 (mates paired) | **24,148,993** |
| **total records** | **exactly 48,297,986** | **48,297,986** |
| **total bases** | **exactly 4,829,798,600** | **4,829,798,600** |
| read length | min == max == 100 | **100 / 100** |
| **after `fq2fa`** | **records and bases unchanged** | **48,297,986 / 4,829,798,600** |

**Neither total is a constant someone wrote down — both are shared with two other tools.**
48,297,986 is [bwa](../bwa-samtools/README.md)'s 48,817,006 BAM records minus its 519,020
supplementary ones, and it is also [fastp](../fastp/README.md)'s `before_filtering.total_reads`.
4,829,798,600 is fastp's `total_bases`. Three tools, three different ways of counting the same file,
one number each. If seqkit ever disagrees, one of the three did not read the file you think it did —
which is a stronger claim than any band on a record count, and it costs nothing.

`fq2fa` then adds a conservation check across a format change: dropping the quality lines must not
change how many sequences there are or how long they are. A conversion that truncated its output, or
mis-parsed a record boundary, fails the arithmetic while still exiting 0.

Every number came back **identical on all four Graviton generations**. seqkit is deterministic, so
that is an exact assertion rather than a tolerance, and it makes the four runs each other's check.

### Pins

| | data tier |
|---|---|
| seqkit | `quay.io/aarchbio/seqkit@sha256:5478aaad4dd7…` (2.11.0, cosign-verified, `linux/arm64`) |
| reads | RODA `s3://1000genomes/…/SRR062634_{1,2}.filt.fastq.gz` — HG00096, staged by [bwa](../bwa-samtools/README.md) |

The reads' tier is a RODA path rather than a hash, so their digests are published by
[fastp](../fastp/README.md)'s run: `01b9c92fe5d197a7…` (R1), `ec1bc2843e57db02…` (R2).

Measured tmpfs high-water mark is **5,612 MB** — the 3.6 GiB of staged reads plus the ~2.3 GiB of
FASTA this writes. That fits the 8 GiB tmpfs of a 16 GiB box
([staging is half of RAM](../../practices/container-path.md)), which is what sizes this at a 2xlarge.
The FASTA is deliberately not uploaded: nothing in the catalog consumes it, so storing it would be
paying to keep something unread.

### On x86_64

The same task on same-generation Intel (`c8i.2xlarge`), from bioconda's own x86 build of the same
version — `quay.io/biocontainers/seqkit@sha256:0e14f53b…` (2.13.0, `linux/amd64`; not
cosign-signed, so the digest is the pin). Same staged reads:

| | arm64 | x86_64 |
|---|---|---|
| total records / bases | 48,297,986 / 4,829,798,600 | 48,297,986 / 4,829,798,600 |
| after `fq2fa` | 48,297,986 / 4,829,798,600 | 48,297,986 / 4,829,798,600 |
| read length min / max | 100 / 100 | 100 / 100 |
| `stats.tsv`, `fa_stats.tsv` | — | **byte-identical** to arm64 |

Same counts on a fifth chip, so the three-tool agreement holds on x86 too. `smoke-check.txt`
differs only in the wall time; the tmpfs high-water mark is the same 5,612 MB.

### Run + verify

```sh
make stage RECIPE=bwa-samtools
make run   RECIPE=seqkit                  # Graviton4
make run   RECIPE=seqkit ARCH=x86_64      # same spec, x86 pin, writes runs-x86_64/
make ls    RECIPE=seqkit
```

Expect `smoke-check.txt` with `total_num_seqs 48297986`, `total_sum_len 4829798600` and
`fa_sum_len` equal to the latter.

</details>
