---
tool: picard
tool_version: 3.5.0
image: quay.io/aarchbio/picard@sha256:c6a742e8277b9010df9aa3b9a6bb40651792ff319627cc1c2bf8a70ac633e6bd
spawn_version: 0.111.4
last_verified: 2026-10-01
---
# Picard MarkDuplicates — a whole-genome BAM, and a partition that must add up

Marks duplicates in bwa's own 48,817,006-record sorted BAM in 591 s, and proves no record was lost without needing a second tool. For anyone putting MarkDuplicates in a real pipeline.

> **Its metrics file is a partition of the input**, so the four categories must sum to the record count. They do, exactly — and two of them independently match `samtools flagstat` on the same BAM.

## Run it

```bash
make stage RECIPE=bwa-samtools   # the chain: align -> sort -> mark duplicates
make run   RECIPE=picard         # ~10 min on r8g.2xlarge, self-terminating
make ls    RECIPE=picard         # dup_metrics.txt + smoke-check.txt

picard -Xmx24g MarkDuplicates I=aln.sorted.bam O=marked.bam M=dup_metrics.txt TMP_DIR=/tmp
```

## Shape, size, cost

`r8g.2xlarge` (8 vCPU, 64 GiB): **591 s of MarkDuplicates in a 702 s billed window, $0.105.**

**Size it on RAM, not cores.** MarkDuplicates spills read-ends to `TMP_DIR`, and on the task path
that is [tmpfs — i.e. RAM](../../practices/container-path.md), competing with its own Java heap. So
the box pays twice: `-Xmx24g` of heap plus a measured **8,834 MB** tmpfs high-water mark, on top of
the 4.5 GB staged input. That is the same double-spend [samtools sort](../bwa-samtools/README.md)
hits one step earlier in the chain, and it is why this runs on `r8g` rather than `c8g`.

## Make it yours

| In the recipe | Swap for | What to know |
|---|---|---|
| bwa's whole-genome sorted BAM | your coordinate-sorted BAM | must be coordinate-sorted; MarkDuplicates reads mate positions. |
| `-Xmx24g` | your heap | heap and `TMP_DIR` draw on the same RAM here, so raising one shrinks the other. |
| mark only | `REMOVE_DUPLICATES=true` | then the partition identity below no longer holds, because records *are* dropped — which is the point of checking it. |

**Leave the workload** — a real whole-genome BAM, so the timing and the memory shape transfer.
**Scale it** by depth; duplicate rate rises with coverage and this library is shallow.

<details>
<summary>As shipped: the partition identity, the parsing trap, pins</summary>

### The checks

| observable | assertion | observed |
|---|---|---|
| reads examined | **== flagstat primary-mapped** | **48,184,934** (= 2 × 24,057,356 + 70,222) |
| secondary/supplementary | **== flagstat supplementary** | **519,020** |
| **partition total** | **exactly 48,817,006** | **48,817,006** |
| `PERCENT_DUPLICATION` | **== recomputed from its own counts** | **0.008684**, diff **0.000000** |
| BAM magic | `1f8b0804` | `1f8b0804` |

**Two free identities, no second tool required.** MarkDuplicates partitions every input record into
examined-pairs, examined-unpaired, secondary/supplementary and unmapped, so those must sum to the
input count — which proves nothing was dropped *without* calling `samtools view -c`, and `samtools`
is not in this image anyway ([one tool per image](../../practices/container-path.md)). And
`PERCENT_DUPLICATION` is a function of counts in the same file, so recomputing it catches a corrupted
or mismatched metrics file that any range check on the percentage would wave through.

The first two lines also reconcile with `samtools flagstat` run by a different tool on the same BAM:
48,184,934 primary-mapped and 519,020 supplementary, both to the digit. That is a cross-tool
agreement obtained for free, because both tools are counting the same partition.

### The parsing trap

`dup_metrics.txt` is tab-delimited and its `LIBRARY` value is **`Unknown Library` — with a space**.
Under awk's default field splitting that shifts every column by one, and the result is not an error
but a *plausible-looking* metrics summary: on the first run it reported 206,518 optical duplicates
against 5,417 total duplicates, which is impossible, and a `PERCENT_DUPLICATION` of 0. `-F'\t'` is
load-bearing here, not style.

**Duplicate rate is 0.87%**, which is low because this library is ~1.5× genome-wide — at that depth
two reads rarely start at the same position by chance. Optical duplicates are 0, as expected when the
read names carry no flowcell coordinates. Neither number is asserted: both are properties of the
library, not of Picard.

### Pins

| | data tier |
|---|---|
| Picard | `quay.io/aarchbio/picard@sha256:c6a742e8…` (3.5.0, `linux/arm64`) |
| input BAM | `runs/bwa-samtools/r1/aln.sorted.bam` — produced by [bwa-samtools](../bwa-samtools/README.md), 48,817,006 records |

This recipe is the third link in a chain rather than a standalone: `bwa mem` → `samtools sort` →
`MarkDuplicates`, each reading the previous step's staged output.

### Run + verify

```sh
make run RECIPE=picard
make ls  RECIPE=picard
```

Expect `smoke-check.txt` with `partition_total 48817006`, `reads_examined 48184934` and
`percent_abs_diff 0.000000`.

</details>
