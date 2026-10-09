---
tool: clair3
tool_version: "2.0.3"
images:
  - quay.io/aarchbio/clair3@sha256:b464803435a71829228bedd4641d994018106952d746517188761d7dbd7bd289
  - quay.io/aarchbio/bcftools@sha256:8171fe74464620a0585cc8998fd9bacbfc04480ac5571229f22f390ecfd5658e
spawn_version: 0.123.0
last_verified: 2026-10-09
---
# Clair3 — deep-learning variant calling on Graviton, F1 0.99530 against GIAB

Calls variants on whole chr20 of NA12878 at 36× and scores them against the GIAB truth set with the same scorer the GATK4 recipe uses. For anyone who wants a neural variant caller on ARM.

## Run it

```bash
# nothing new to stage -- reuses the GIAB fixture from recipes/gatk4,
# and task 2 reads task 1's VCF from this recipe's own run prefix
for s in $(make -s spec RECIPE=clair3); do spawn task run --spec "$s" --wait; done
make ls RECIPE=clair3

run_clair3.sh --bam_fn=NA12878.chr20.30x.bam --ref_fn=chr20.fa \
  --threads=8 --platform=ilmn --model_path=/opt/conda/bin/models/ilmn \
  --ctg_name=chr20 --output=clair3_out
```

Two tasks: Clair3 calls, then bcftools scores against GIAB.

## Make it yours

| In the recipe | Swap for | What to know |
|---|---|---|
| `--platform=ilmn` | `ont`, `hifi` | **21 models ship in this image** at `/opt/conda/bin/models/` — `ilmn`, `hifi`, and 15+ ONT. No download; that is why the package is 429 MB. |
| `FILTER=PASS` | — | **keep this.** `merge_output.vcf.gz` is a decision log, not a variant set: it carries a `RefCall` row for every position judged reference (179,453 here against 118,312 PASS). |
| **no QUAL threshold** | — | **do not add GATK's `QUAL>=30`.** Clair3's QUAL median is 23.2; that cutoff discards 82% of its correct calls. See below. |
| `--ctg_name=chr20` | drop it, or `--include_all_ctgs` | whole-genome is ~25× this work. Clair3 parallelises well — 8 threads did chr20 in 12.5 min. |
| GIAB NA12878 | your sample | the truth set and its BED are what make this a correctness check rather than a smoke test. |

**Leave the fixture.** It is the same BAM, reference and truth set the GATK4, bcftools and freebayes recipes use, which is what makes the F1 values comparable. **Scale it** to whole genome once it passes.

## Shape, size, cost

Two tasks: `m8g.2xlarge` (8 vCPU / 32 GiB) for calling, `m8g.large` for scoring. TTL 240m as a **backstop** with `cost_limit` $1.20 as the real guard, since Clair3 had not been clocked on this fixture. Measured: **751 s** of Clair3, well inside both.

<details>
<summary>As shipped: F1 against GIAB, the shared-denominator identity, and two filters that are not interchangeable</summary>

### The checks

| observable | assertion | observed |
|---|---|---|
| BAM bytes | sha256 matches the fixture the other callers scored | `0ad228c1…` |
| bundled `ilmn` model present | asserted, not assumed | yes |
| `RefCall` rows present | > 0 — else this is not Clair3 output | 179,453 |
| PASS variants | > 50,000 on a 36× chromosome | 118,312 (97,866 SNVs) |
| all calls on chr20 / single sample / indexed | structural | 0 off-target, 1 sample, indexed |
| **truth-side SNV denominator** | **= 69,210, GATK4's value** | **69,210** |
| **SNV precision** | ≥ 0.97 | **0.99540** |
| **SNV recall** | ≥ 0.98 | **0.99519** |
| **SNV F1** | ≥ 0.975 | **0.99530** |
| indels | *reported, not asserted* | P 0.99348 / R 0.98686 / F1 0.99016 |

### The result

| caller | TP | FP | FN | precision | recall | **F1** |
|---|---|---|---|---|---|---|
| **Clair3 2.0.3** | 68,877 | **318** | 333 | 0.99540 | 0.99519 | **0.99530** |
| GATK4 HaplotypeCaller | 68,895 | 696 | 315 | 0.99000 | 0.99545 | 0.99272 |

Clair3 edges GATK4 on F1, with roughly **half the false positives** and marginally more false
negatives. This answers a question `what-this-does-not-cover.md` previously left open: DeepVariant
is architecture-blocked on arm64, which made deep-learning calling look unavailable. It is not —
and it is competitive.

**Do not read the runtimes as a speed comparison.** Clair3 ran 751 s on 8 threads; GATK4 ran
7929 s on 1, because `--native-pair-hmm-threads` is inert on Graviton (Intel GKL is x86-only).
Different thread counts, so this is not thread-matched and no speedup is claimed.

### Why these F1 values are comparable at all

**The scorer is GATK4's, reused verbatim** from `recipes/gatk4/02-concordance.task.json`: restrict
both sides to the GIAB BED, split multiallelics, left-align against the same `chr20.fa`, count
`bcftools isec`. A second scorer would produce a different number for reasons unrelated to the
caller.

**And the denominator is asserted, not assumed.** `TP + FN` is the truth-side SNV count inside the
BED — a property of the truth set and region, independent of the caller. GATK4 measured **69,210**;
Clair3 must too, or the two are scoring different truth variants and the F1 values cannot be
compared however good they look. That is an exact cross-recipe identity.

### Two filters that look similar and are not interchangeable

**`FILTER=PASS` is required.** Clair3's `merge_output.vcf.gz` is a *decision log*: alongside PASS
calls it emits a `RefCall` row for every position it examined and judged reference — **179,453** of
them against 118,312 PASS. Scoring the unfiltered file treats each as a query variant, so the
"false positives" reported are Clair3 correctly saying *no variant here*. GATK4 emits no such rows,
which is exactly why two call sets need different **preparation** before one scorer can compare
them.

**GATK's `QUAL>=30` must NOT be carried across.** Measured on this run, Clair3's PASS SNVs have
QUAL **median 23.2, p90 31.6**, and only **17.5%** reach 30 — while GATK QUAL on a 36× germline
sample runs into the hundreds, so the same cutoff keeps ~99% there. Applying it to Clair3 gives:

```text
at FILTER=PASS (Clair3's own gate)    precision 0.99540   recall 0.99519   F1 0.99530
at GATK's QUAL>=30                    precision 1.00000   recall 0.22911
```

Perfect precision with collapsed recall and **zero** false positives is the signature of a
*truncated query set*, not a bad caller — a bad caller produces false positives. Each caller is
therefore scored at **its own operating point**, which is what its authors intend and what
`hap.py` does by honouring a tool's own FILTER. An identical numeric cutoff across incomparable
scales is not a fairer comparison, just a different wrong one.

**The reusable lesson: borrow another recipe's comparison machinery, never its thresholds.** The
scorer encodes *how* to compare; a threshold encodes *what one specific tool considers confident*.

### Indels are reported, not asserted

`POS:REF:ALT` equality after left-alignment still counts two correct spellings of one indel as FP
*and* FN, which `hap.py`'s haplotype comparison credits. Asserting it would assert a representation
difference — the same decision the GATK4 recipe makes, so the indel rows stay comparable between
the two.

### Pins

| | |
|---|---|
| BAM | `NA12878.chr20.30x.bam`, sha256 `0ad228c1…`, 17,705,654 records, 36.3× mean depth |
| truth | GIAB HG001 v4.2.1 chr20 + its BED (56,000,154 confident bases) |
| images | clair3 `@sha256:b4648034…` (2.0.3), bcftools `@sha256:8171fe74…` |

Both cosign-verified. **The `ilmn` model was confirmed present by a $0.01 probe before this recipe
was designed** — which models a build ships is a property of the image, and discovering a missing
one on a 921 MB BAM is the expensive order.

### Run + verify

```sh
for s in $(make -s spec RECIPE=clair3); do spawn task run --spec "$s" --wait; done
aws s3 cp "s3://$(make -s print-bucket)/runs/clair3/r1/score.tsv" -
```

Fails on a BAM that differs from the fixture, a missing model, no `RefCall` rows, a denominator
that is not 69,210, or SNV metrics below their floors — but check the bucket regardless
([exit 0 isn't proof](../../practices/container-path.md)).

### Not covered

Whole-genome calling, long-read platforms (`ont`/`hifi` models are bundled and unexercised),
phasing with `--enable_phasing`, GVCF output, somatic calling (ClairS is a different tool), and
`hap.py`-style haplotype-aware comparison, which is what would let the indel numbers be asserted
rather than reported.

</details>
