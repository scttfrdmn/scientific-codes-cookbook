---
tool: freebayes
tool_version: "1.3.10"
image: quay.io/aarchbio/freebayes@sha256:033f0f12b3a31db97ebceee72604c904d1436f877e288ff247f22a9eedfacdf9
spawn_version: 0.111.4
last_verified: 2026-09-30
---
# freebayes — haplotype calling on a whole chromosome at 36×, scored against GIAB

Calls all of chr20 in NA12878 at 36× on Graviton, then measures precision and recall against NIST's published benchmark. For anyone running freebayes and wanting to know where it stands.

> **Filter it or don't ship it.** Raw freebayes output on this sample carries **146,290 false positives** — precision 0.312. One `QUAL>=30` removes 146,088 of them and leaves the *best* precision of the three callers here (0.99695). That is its defaults, not a defect.

## Run it

```bash
make stage RECIPE=gatk4       # shared: reference, GIAB truth slice, the 36x chr20 BAM
for s in $(make -s spec RECIPE=freebayes); do spawn task run --spec "$s" --wait; done   # call (~11 min) then score against GIAB
make ls    RECIPE=freebayes   # freebayes.vcf.gz + concordance.txt

freebayes -f chr20.fa -r chr20 NA12878.chr20.30x.bam > freebayes.vcf
```

## Where it stands against the alternatives

| caller | wall | $/result | SNV precision (`QUAL≥30`) | SNV recall (unfiltered) |
|---|---|---|---|---|
| [bcftools](../bcftools/README.md) | 228 s | 0.0126 | 0.99525 | **0.99491** |
| **freebayes** | 644 s | 0.0310 | **0.99695** | 0.95953 |
| [GATK4](../gatk4/README.md) | 7,929 s | 0.3537 | 0.99000 | **0.99545** |

**Most precise once filtered, least sensitive** — ~4% fewer truth SNVs at defaults, real rather than a threshold artifact (recall is 0.95953 even unfiltered). Pick it when precision beats sensitivity, or for pooled samples, which is what it is for ([full three-way](../../measurements/callers-real/README.md)).

## Make it yours

| In the recipe | Swap for | What to know |
|---|---|---|
| NA12878 chr20 at 36× | your BAM + `-r` region | no read-group or sort-order demands, unlike [GATK](../gatk4/README.md). |
| default sensitivity | `--min-alternate-fraction`, `--min-alternate-count` | these are what cost recall on a single diploid sample; freebayes' defaults assume you may be calling a pool. |
| `QUAL≥30` post-filter | your own filter expression | **not optional** — the raw output is 3× larger than the other callers' and 69% of it is noise. |

**Leave the workload** — real depth over a whole chromosome against a published truth set. **Scale it** by regions in parallel, and tune sensitivity before you tune the box.

## Shape, size, cost

Two tasks on `c8g.xlarge`: **644 s of calling**, then scoring in the bcftools image ([one tool per image](../../practices/container-path.md)). 385,371 variants — 3× the others, which is the QUAL tail above.

**Generation is the biggest lever measured in this catalog: 2.65× Gv2→Gv5** (1349 → 509 s), the same call getting **52% cheaper** while `$/hr` rises 27.9% — and uniquely, with *no weak step* (1.59× / 1.32× / 1.27×). The variant count is identical on all four chips. [Full ladder](../../measurements/freebayes-real/README.md).

<details>
<summary>As shipped: the GIAB accuracy numbers, what is asserted, pins</summary>

### Accuracy against a published truth set

NA12878 is GIAB **HG001**, so NIST publishes both the benchmark variants and the BED where that
benchmark is confident. Both sides restricted to the **56,000,154 high-confidence bases** of chr20
and normalised identically:

| | TP | FP | FN | precision | recall | F1 |
|---|---|---|---|---|---|---|
| SNVs, unfiltered | 66,409 | **146,290** | 2,801 | **0.31222** | 0.95953 | 0.47114 |
| **SNVs**, `QUAL≥30` | 66,119 | 202 | 3,091 | **0.99695** | 0.95534 | **0.97570** |
| indels, unfiltered | 9,934 | 349 | 570 | 0.96606 | 0.94573 | 0.95579 |
| indels, `QUAL≥30` | 9,679 | 69 | 825 | 0.99292 | 0.92146 | 0.95586 |

The unfiltered row is the reason this page leads with a warning rather than a number.

**Asserted:** `QUAL≥30` SNV precision ≥ 0.98 — freebayes' strength — plus recall ≥ 0.94, a floor on
its own defaults rather than a cross-caller bar. A shared recall floor cannot rank callers whose
defaults differ this much, so recall is reported here and compared, identically for all three, in
[the three-way measurement](../../measurements/callers-real/README.md).

**Indels are reported, never asserted** — `POS:REF:ALT` equality after left-alignment counts two
correct spellings of one indel as FP *and* FN.

### Pins

| | data tier |
|---|---|
| freebayes | `quay.io/aarchbio/freebayes@sha256:033f0f12…` (1.3.10, `linux/arm64`) |
| bcftools (scoring) | `quay.io/aarchbio/bcftools@sha256:8171fe74…` |
| reads | `s3://1000genomes/1000G_2504_high_coverage/data/ERR3239334/NA12878.final.cram` |
| truth | `s3://giab/release/NA12878_HG001/NISTv4.2.1/GRCh38/` |
| reference | `chr20.fa` staged by [bwa-samtools](../bwa-samtools/README.md) |

The 36× chr20 BAM is built once by `gatk4`'s prep task and shared by all three callers, so the
comparison is on identical bytes. BAM sha256
`0ad228c159e7f3b060d3476226f27b016b051f03eb73003f8929c5f954e1e02c`.

### Run + verify

```sh
make run RECIPE=freebayes
make ls  RECIPE=freebayes
```

Expect `smoke-check.txt` with 385,371 variants and `sample NA12878`, and `concordance.txt` with
`snv_precision 0.99695`.

</details>
