---
tool: fastp
tool_version: 1.3.6
image: quay.io/aarchbio/fastp@sha256:061ee7c6b8e5af265dfed6f25c51e482e3bb403c51f167561405010e5c5f632a
spawn_version: 0.111.4
last_verified: 2026-10-01
---
# fastp — QC and trim a complete 48M-read run in 36 seconds

Filters and trims the whole SRR062634 run (4.83 Gbp) and balances its books exactly. For anyone putting read QC in front of an aligner.

> **The compute is free; the staging is what you pay for.** 36 s of fastp sits in a 226 s billed
> window, and the box is chosen by RAM, not cores — see below before you rent a big one.

## Run it

```bash
make stage RECIPE=bwa-samtools   # fastp reads the same reads bwa aligns
spawn task run --spec "$(make -s spec RECIPE=fastp)" --wait   # ~4 min on m8g.2xlarge, self-terminating
make ls    RECIPE=fastp          # fastp.json + fastp.html + smoke-check.txt

fastp -i SRR062634_1.filt.fastq.gz -I SRR062634_2.filt.fastq.gz \
      -o out_1.fq.gz -O out_2.fq.gz -j fastp.json -h fastp.html -w 8
```

## Make it yours

| In the recipe | Swap for | What to know |
|---|---|---|
| the complete SRR062634 run | your FASTQs | reused byte-for-byte from [bwa](../bwa-samtools/README.md) — nothing re-staged. |
| default QC + trimming | `--dedup`, UMI, overrepresentation flags | the conservation identity below holds whatever you enable; the component counts change. |
| `-w 8` | more or fewer | 16 threads is **5 s faster** than 8 on this run. Threads are not the lever. |

**Leave the workload** — a complete 48M-read run, so the sizing argument below transfers. **Scale it**
by read length or depth; both move the staging footprint, which is the thing that picks the box.

## Shape, size, cost

`m8g.2xlarge` (8 vCPU, 32 GiB): **36 s of fastp in a 226 s billed window, $0.0225.**

**Size by RAM, and the reason is counter-intuitive.** Input (3.6 GiB) and output (~3.4 GiB) sit in
tmpfs *together* — a measured **7,154 MB peak** — and tmpfs is
[half of RAM](../../practices/container-path.md), so ≥16 GiB of tmpfs means ≥32 GiB of RAM. On `c8g`
the only 32 GiB box is `4xlarge`, which drags along 16 cores fastp cannot use at $0.6381/hr; `m8g.2xlarge`
has the same 32 GiB for **$0.3590**. Measured both: **$0.0401 on `c8g.4xlarge` at 16 threads against
$0.0225 on `m8g.2xlarge` at 8 — 44% cheaper for 5 s slower.** Reading the core count right would still
have picked the wrong box, because the binding resource was never compute ([measurement](../../measurements/fastp-real/README.md)).

<details>
<summary>As shipped: an exact count shared with bwa, the conservation identity, why fastp's duplicate rate is not a cross-check, pins</summary>

### The checks

| observable | assertion | observed |
|---|---|---|
| **reads in** | **exactly 48,297,986 — bwa's primary record count** | **48,297,986** |
| **accounted for** | **passed + low_quality + too_many_N + too_short + too_long == reads in** | **48,297,986** |
| out1 == out2 | mates stay paired | **22,772,943 = 22,772,943** |
| out1 + out2 | == passed_filter | **45,545,886** |

**The read count is a cross-tool identity, not a recorded constant.** 48,297,986 is bwa's
48,817,006 BAM records minus its 519,020 supplementary ones — every input read becomes exactly one
primary record. So two unrelated tools arrive at the same number from opposite directions, and if
they ever disagree, one of them did not read the file you think it did. That is strictly stronger
than hashing the FASTQs, because it is a claim about *content* rather than bytes.

Then fastp's report must balance: 45,545,886 passed + 2,741,150 low-quality + 10,950 too-many-N
accounts for all 48,297,986. Counting the two output FASTQs is what turns that report into a claim
about the files fastp actually *wrote* — a truncated or mis-split output fails the arithmetic while
still exiting 0. 4,829,798,600 bases over 48,297,986 reads is exactly 100 bp each, as this library is.

Both runs — 8 threads and 16 — produced every count identically. Unlike
[the assemblers](../flye/README.md), fastp's thread count does not move its answer, so these are
asserted exactly with no seed to pin.

### Why the duplicate rate is reported, not asserted

fastp estimates **0.274%** duplication; [picard MarkDuplicates](../picard/README.md) measures
**0.868%** on the alignments of these same reads. Both are right, and comparing them would be
[comparing a method difference](../../practices/cross-checks.md): fastp looks for identical
*sequences*, picard for pairs at identical *mapping positions* — which catches duplicates that
differ by a sequencing error, and misses nothing to adapter trimming. There is no tolerance that
makes those one number, so the page reports both and asserts neither. Q30 rate (0.90576) is the
same kind of observation: a property of the library, not of fastp.

### Pins

| | data tier |
|---|---|
| fastp | `quay.io/aarchbio/fastp@sha256:061ee7c6b8e5…` (1.3.6, cosign-verified, `linux/arm64`) |
| reads | RODA `s3://1000genomes/…/SRR062634_{1,2}.filt.fastq.gz` — HG00096, staged by [bwa](../bwa-samtools/README.md) |

The reads' tier is a RODA path rather than a hash, so the run publishes their digests:
`01b9c92fe5d197a7…` (R1) and `ec1bc2843e57db02…` (R2). The image carries no `python3` and no `jq`, so
`fastp.json` is parsed with `grep`/`awk` — and the **first** `total_reads` in that file is
`before_filtering` while the second is `after_filtering`, so `head -1` is load-bearing.

The trimmed FASTQs are deliberately **not** uploaded: nothing in the catalog consumes them (bwa
aligns the untrimmed reads, by its own pin), so storing 3.4 GiB would be paying to keep something
unread. Add them to the spec's `outputs` if your next step needs them.

### Run + verify

```sh
make stage RECIPE=bwa-samtools
make run   RECIPE=fastp
make ls    RECIPE=fastp
```

Expect `smoke-check.txt` with `reads_before 48297986`, `accounted_for 48297986` and
`out1+out2 45545886`.

**Fan out across samples.** One QC run is one task; a cohort is the same task as a
[job array](../../patterns/job-arrays.md), one instance per sample keyed by `$JOB_ARRAY_INDEX`.

</details>
