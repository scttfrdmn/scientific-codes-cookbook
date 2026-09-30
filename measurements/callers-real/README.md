# Three variant callers, one 36× genome, one published truth set

> **bcftools matches GATK on SNVs in 1/35th of the time; GATK earns its cost on indels; freebayes
> needs filtering or its precision is 0.31.** Same BAM, same reference, same GIAB benchmark, same
> scoring code — so for once the three numbers are comparable.

NA12878 (GIAB **HG001**) at 36× across the whole of GRCh38 chr20, from the published NYGC 30×
CRAM. Scored against **GIAB HG001 v4.2.1** inside its 56,000,154 high-confidence chr20 bases.
Every caller ran on `c8g.xlarge`, 4 vCPU / 8 GiB.

## Speed and cost

| caller | model | wall | variants called | billed | **$/result** |
|---|---|---|---|---|---|
| **bcftools** `mpileup`+`call` | pileup | **228 s** | 127,616 | 285 s | **0.0126** |
| freebayes | haplotype | 644 s | 385,371 | ~700 s | 0.0310 |
| GATK4 HaplotypeCaller | local re-assembly | **7,929 s** | 131,730 | 7,982 s | **0.3537** |

**GATK is 34.8× slower and 28× more expensive per result than bcftools on identical bytes.**
Note also that GATK is the only one of the three whose cores do nothing — its AVX PairHMM is
x86-64 only, so it runs single-threaded on Graviton
([measured](../gatk4-real/README.md)).

## Accuracy — and why there are two columns, not one

**QUAL is not calibrated across callers**, so a single shared threshold cannot rank them. `q0` is
each tool's full output, which is like-with-like by construction; `q30` is `QUAL>=30` applied to
each. SNVs:

| caller | | TP | FP | FN | precision | recall | **F1** |
|---|---|---|---|---|---|---|---|
| GATK4 | q0 = q30 | 68,895 | 696 | 315 | 0.99000 | **0.99545** | 0.99272 |
| bcftools | q0 | 68,858 | 580 | 352 | 0.99165 | 0.99491 | 0.99328 |
| bcftools | q30 | 68,701 | 328 | 509 | 0.99525 | 0.99265 | **0.99395** |
| freebayes | q0 | 66,409 | **146,290** | 2,801 | **0.31222** | 0.95953 | 0.47114 |
| freebayes | q30 | 66,119 | 202 | 3,091 | **0.99695** | 0.95534 | 0.97570 |

Indels:

| caller | | precision | recall | F1 |
|---|---|---|---|---|
| **GATK4** | q0 = q30 | 0.99419 | **0.99372** | **0.99395** |
| bcftools | q0 | 0.99171 | 0.97963 | 0.98563 |
| bcftools | q30 | 0.99415 | 0.97077 | 0.98232 |
| freebayes | q0 | 0.96606 | 0.94573 | 0.95579 |
| freebayes | q30 | 0.99292 | 0.92146 | 0.95586 |

### Four things in that table worth acting on

- **On SNVs, bcftools is not a downgrade.** Its best F1 (0.99395) slightly *exceeds* GATK's
  (0.99272), trading a little recall for better precision, at 1/35th the compute. If your
  analysis is SNV-driven, GATK's cost buys very little here.
- **On indels, GATK is clearly better and this is where its model earns the money** — 0.99372
  recall against bcftools' 0.97963 and freebayes' 0.94573. Local re-assembly is supposed to win
  on indels, and it does. That is the honest reason to pay 28×.
- **GATK's q0 and q30 rows are identical**, which is not a scoring artifact: HaplotypeCaller's
  default `--standard-min-confidence-threshold-for-calling` is already 30, so it filters
  internally. Its "unfiltered" output is not unfiltered.
- **freebayes unfiltered is unusable and filtered is excellent.** Its QUAL≈0 tail is
  **146,290 false positives** — precision 0.312 — and one `QUAL>=30` removes 146,088 of them to
  leave the best precision of the three (0.99695). Nobody should ship raw freebayes output; that
  is a property of its defaults, not a defect.

### The metric mistake this measurement made first

The first scoring pass applied `QUAL>=30` to all three and asserted recall ≥ 0.99. freebayes
failed at 0.95534, and the conclusion drawn was "the shared threshold is cutting genuine
freebayes calls" — a tidy story about QUAL calibration. **The unfiltered run disproved it:**
freebayes' recall is 0.95953 even with no filter at all, so the threshold costs it 0.4 points,
not 4. Its recall deficit is real, and belongs to its default settings (tuned for pooled and
population calling), not to the metric.

Worth recording because the wrong explanation was the more sophisticated one. The fix was to run
the comparison both ways rather than to reason about which was fairer.

### What is asserted, and what is only reported

Two claims every working caller must satisfy, neither shaved to the observed values:

- **unfiltered SNV recall ≥ 0.95** — the caller detects the truth variants at all, independent of
  how it scales QUAL. Observed: 0.9595 / 0.9949 / 0.9955.
- **`QUAL>=30` SNV precision ≥ 0.98** — the caller *can* be filtered to high precision.
  Observed: 0.9900 / 0.9953 / 0.9970.

A shared recall floor high enough to separate the three is deliberately *not* asserted: tuned to
admit freebayes it would be a fudge, tuned to exclude it it would fail a correctly-working tool.
The gap is the finding, so it is reported. Indels are reported and never asserted, because
`POS:REF:ALT` equality after left-alignment counts two correct spellings of one indel as FP *and*
FN — which `hap.py`'s haplotype comparison would credit.

## Run it

```sh
export AWS_PROFILE=aws COOKBOOK_BUCKET=<your bucket>
make stage RECIPE=gatk4                              # reference, GIAB truth, the 36x BAM
make run   RECIPE=bcftools                            # call + score, ~5 min
make run   RECIPE=freebayes                           # call then score
make run   RECIPE=gatk4                               # ~2h 12m, single-threaded on Graviton
spawn task run --spec measurements/callers-real/three-way.task.json --wait
```

The two canaries (`bcftools-canary.task.json`, `freebayes-canary.task.json`) clock each caller on
`chr20:1,000,000-3,000,000` — 6 s and 14 s against HaplotypeCaller's 77 s — which is how the
whole-chromosome TTLs were sized rather than guessed.

## Caveats

n = 1 per cell. All three callers read one BAM, one reference and one truth set, and are scored by
one block of code, so the comparison is clean; the wall times share an instance type and size.
Rates are us-west-2 on-demand at time of measurement.

**All three are unfiltered beyond QUAL.** No VQSR, no hard filters, no `bcftools filter`
expressions — so these are each tool's out-of-the-box behaviour, not its ceiling. GATK in
particular is normally run with VQSR or hard filtering, which would raise its precision above the
0.990 shown. Read the table as "what you get if you run the documented command", which is what a
cookbook owes, not as a published benchmark of best achievable accuracy.

Sizing note: freebayes' 644 s is ~1.4× its 2 Mb canary's linear extrapolation (451 s), and
bcftools' 228 s is ~1.2× its own (193 s) — both far better behaved than
[GATK's 3.2×](../gatk4-real/README.md), because a pileup's cost tracks depth × bases rather than
local assembly complexity.
