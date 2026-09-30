---
tool: bcftools
tool_version: "1.24"
image: quay.io/aarchbio/bcftools@sha256:8171fe74464620a0585cc8998fd9bacbfc04480ac5571229f22f390ecfd5658e
spawn_version: 0.111.4
last_verified: 2026-09-30
---
# bcftools — call a whole chromosome at 36×, scored against the GIAB truth set

Pileup-calls all of chr20 in NA12878 at 36× on Graviton, then measures precision and recall against NIST's published benchmark. For anyone choosing a germline caller and what to pay for it.

> **This is the cheap one, and on SNVs it is not a downgrade.** 228 s against GATK's 7,929 s on identical bytes, for a marginally *better* SNV F1. GATK earns its 28× cost on indels, not SNVs.

## Run it

```bash
make stage RECIPE=gatk4     # shared: reference, GIAB truth slice, the 36x chr20 BAM
make run   RECIPE=bcftools  # call + score, ~5 min, self-terminating
make ls    RECIPE=bcftools  # bcftools.vcf.gz + concordance.txt

bcftools mpileup -f chr20.fa -r chr20 NA12878.chr20.30x.bam -Ou \
  | bcftools call -mv -Oz -o bcftools.vcf.gz
```

## What it costs, next to the alternatives

| caller | wall | $/result | SNV F1 | indel recall |
|---|---|---|---|---|
| **bcftools** | **228 s** | **0.0126** | **0.99395** | 0.97077 |
| freebayes | 644 s | 0.0310 | 0.97570 | 0.92146 |
| [GATK4](../gatk4/README.md) | 7,929 s | 0.3537 | 0.99272 | **0.99372** |

**SNV-driven work: bcftools first** — GATK's accuracy band for 1/28th the money. **Indels: GATK**, the one place re-assembly clearly wins ([full three-way](../../measurements/callers-real/README.md)).

## Make it yours

| In the recipe | Swap for | What to know |
|---|---|---|
| NA12878 chr20 at 36× | your BAM + `-r` region | `mpileup` auto-builds the `.fai`; no separate samtools step, and no read-group or sort-order demands ([unlike GATK](../gatk4/README.md)). |
| whole chr20 in one task | your regions | cost tracks depth × bases, so it scales predictably — unlike local-assembly callers. |
| no filtering beyond `QUAL` | `bcftools filter` expressions | the numbers above are out-of-the-box behaviour, not the tool's ceiling. |

**Leave the workload** — real depth over a whole chromosome against a published truth set, so the accuracy transfers. **Scale it** by regions in parallel; memory is modest and pileup calling stays `c`-family even genome-wide.

## Shape, size, cost

One task on `c8g.xlarge`: **228 s of calling inside a 285 s billed window** — 80% compute, **$0.0126**. Cheap enough that the data path, not the chip, is what you would optimise next.

<details>
<summary>As shipped: the GIAB accuracy numbers, what is asserted, pins</summary>

### Accuracy against a published truth set

NA12878 is GIAB **HG001**, so NIST publishes both the benchmark variants and the BED of regions
where that benchmark is confident — which turns "produce a plausible VCF" into "reproduce a
published accuracy" ([why that is stronger](../../practices/cross-checks.md)). Both sides
restricted to the **56,000,154 high-confidence bases** of chr20 and normalised identically
(split multiallelics, left-aligned against the same `chr20.fa`):

| | TP | FP | FN | precision | recall | F1 |
|---|---|---|---|---|---|---|
| **SNVs**, unfiltered | 68,858 | 580 | 352 | 0.99165 | **0.99491** | 0.99328 |
| **SNVs**, `QUAL≥30` | 68,701 | 328 | 509 | **0.99525** | 0.99265 | **0.99395** |
| indels, unfiltered | 10,290 | 86 | 214 | 0.99171 | 0.97963 | 0.98563 |
| indels, `QUAL≥30` | 10,197 | 60 | 307 | 0.99415 | 0.97077 | 0.98232 |

127,616 variants called across chr20.

**Asserted:** unfiltered SNV recall ≥ 0.95 (the caller detects the truth variants at all) and
`QUAL≥30` SNV precision ≥ 0.98 (it *can* be filtered to high precision). Both floors are claims
any working caller must meet, not shaved observations — they fail loudly on a wrong reference,
sample or depth, and they hold identically for all three callers so the pages are comparable.

**Indels are reported, never asserted.** `POS:REF:ALT` equality after left-alignment counts two
correct spellings of one indel as FP *and* FN, which `hap.py`'s haplotype comparison would credit.
Asserting it would assert a representation difference.

**QUAL is not comparable across callers**, which is why both rows exist rather than one: a single
threshold applied to three tools ranks their calibration, not their accuracy. All three are scored
both ways in [the three-way measurement](../../measurements/callers-real/README.md).

### Why this is fast, and stays predictable

A pileup's cost tracks **depth × bases**, not local complexity, so runtime scales with the
interval and TTLs are easy to size: whole chr20 took 228 s against 193 s extrapolated linearly
from a 2 Mb slice, a 1.2× miss. A local-assembly caller on the same input is off by 3.2×, because
its cost tracks repeat complexity instead — [measured](../../measurements/gatk4-real/README.md).

### Pins

| | data tier |
|---|---|
| bcftools | `quay.io/aarchbio/bcftools@sha256:8171fe74…` (1.24, cosign-verified, `linux/arm64`) |
| reads | `s3://1000genomes/1000G_2504_high_coverage/data/ERR3239334/NA12878.final.cram` — published NYGC 30× |
| truth | `s3://giab/release/NA12878_HG001/NISTv4.2.1/GRCh38/` — benchmark VCF + high-confidence BED |
| reference | `chr20.fa` staged by [bwa-samtools](../bwa-samtools/README.md) |

The 36× chr20 BAM is built once by `gatk4`'s prep task and shared by all three callers — a second
copy would be a second thing to keep true, and the comparison only means something on identical
bytes. BAM sha256 `0ad228c159e7f3b060d3476226f27b016b051f03eb73003f8929c5f954e1e02c`.

### Run + verify

```sh
make run RECIPE=bcftools
make ls  RECIPE=bcftools
```

Expect `concordance.txt` with `snv_precision 0.99525`, `snv_recall 0.99265`, `snv_f1 0.99395`. The
check runs inside the task; the bucket listing is the second half, because
[stage-out happens even when a command fails](../../practices/container-path.md).

</details>
