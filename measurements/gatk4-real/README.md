# GATK4 HaplotypeCaller on Graviton: cores do nothing, generation does everything

> **`--native-pair-hmm-threads` is inert on Graviton.** GATK ships its PairHMM and
> SmithWaterman accelerators as x86-64 native libraries, so on AArch64 both fall back to Java
> and run single-threaded whatever you pass. Measured: **77 s at 1 thread, 78 s at 8**, identical
> 4,093 variants. There is no core-count knee to find — so the only axis worth sweeping is the
> chip, and the only way to parallelise is to scatter intervals across tasks.

Workload: **NA12878 at 36× on GRCh38 chr20**, from the published NYGC 30× CRAM
(`ERR3239334`). Checked against the **GIAB HG001 v4.2.1** benchmark, which is why this recipe
can claim accuracy rather than plausibility.

## 1. Threading does nothing — the measurement that reframes the rest

```text
chr20:1,000,000-3,000,000 at ~33x, c8g.2xlarge
  --native-pair-hmm-threads 1    77 s    4093 variants
  --native-pair-hmm-threads 8    78 s    4093 variants
```

GATK's own log says why, and it is worth quoting because the message names the wrong culprit
first — it reads like a missing file:

```text
Unable to load libgkl_compression.so from native/libgkl_compression.so
  (/tmp/libgkl_compression…so: cannot open shared object file: No such file or directory
   (Possible cause: can't load AMD 64 .so on a AARCH64 platform))
IntelInflaterFactory - IntelInflater is not supported, using Java.util.zip.Inflater
IntelSmithWaterman - Intel GKL Utils not loaded
SmithWatermanAligner - AVX accelerated SmithWaterman implementation is not supported,
  falling back to the Java implementation
```

Correctness is unaffected — the Java PairHMM computes the same likelihoods, and all four
generations below returned byte-identical variant counts. What is affected is every sizing
instinct: **do not buy cores for HaplotypeCaller on Graviton.** Buy the cheapest instance that
holds the heap, and get parallelism from GATK's own scatter-gather (one interval per task, a
[job array](../../patterns/job-arrays.md)), which is the production pattern anyway.

## 2. Generations — newer is faster *and* cheaper, again

`chr20:1,000,000-3,000,000`, 4 vCPU / 8 GiB on each generation, one thread, same container
digest, same staged bytes. `wall` is timed around HaplotypeCaller alone.

| generation | instance | wall | $/hr | **compute $/result** | billed window | billed $/result | overhead |
|---|---|---|---|---|---|---|---|
| Graviton2 | `c6g.xlarge` | 121 s | 0.1360 | 0.004572 | 225 s | 0.008500 | 104 s |
| Graviton3 | `c7g.xlarge` | 95 s | 0.1450 | 0.003826 | 175 s | 0.007049 | 80 s |
| Graviton4 | `c8g.xlarge` | 79 s | 0.1595 | 0.003501 | 151 s | 0.006691 | 72 s |
| **Graviton5** | `c9g.xlarge` | **62 s** | 0.1739 | **0.002994** | 124 s | **0.005989** | **62 s** |

**Graviton5 is 1.95× faster than Graviton2 and 34.5% cheaper per result** (29.5% billed), even
though its rate card is 27.9% higher. Every step of the ladder is both faster and cheaper —
the same monotonic result [bwa](../bwa-real/README.md) and [salmon](../../recipes/salmon/README.md)
found, now on a code whose hot loop is pure Java rather than hand-tuned SIMD.

**All four returned exactly 4,093 variants and 3,361 SNVs.** That invariance is what licenses
reading the wall column as a chip comparison: the answer is generation-independent, only the
time differs.

Overhead falls with generation too (104 s → 62 s), because most of it is staging a 921 MB BAM
and newer instances have more network — the same secondary effect salmon showed.

## 3. The whole chromosome, and the accuracy it buys

One `c8g.xlarge`, whole chr20, one thread:

```text
HaplotypeCaller   7929 s (2h 12m)    131,730 variants   109,467 SNVs
billed window     7982 s             overhead 53 s  ->  99.3% compute, $0.354
```

Scored against GIAB HG001 v4.2.1 inside its 56,000,154 high-confidence bases, SNVs at QUAL>=30:

| | TP | FP | FN | precision | recall | F1 |
|---|---|---|---|---|---|---|
| **SNVs** (asserted) | 68,895 | 696 | 315 | **0.99000** | **0.99545** | **0.99272** |
| indels (observation) | — | — | — | 0.99419 | 0.99372 | 0.99395 |

Two things about the floors, both about not shipping a check that teaches people to ignore
failures. The asserted floor on precision is **0.98**, looser than the 0.990 observed, because
this recipe applies no variant filtering and precision is therefore a property of the recipe
rather than of GATK — a 0.99 floor would sit 0.001 away and fail on noise. And **indels are
reported, not asserted**: `POS:REF:ALT` equality after left-alignment counts two correct
spellings of one indel as FP *and* FN, which `hap.py` would credit, so asserting it would assert
a representation difference.

`TP + FN = 69,210` is the truth-side SNV count inside the BED, matching what a local identity
test (the truth set scored against itself, which returns exactly 1.00000 with zero FP and zero
FN) reports — so both sides use the same denominator. That identity test was run locally before
any of this cost a task, alongside a deliberately deficient query that scored 0.99027 precision
and 0.23676 recall, which is how the metric was shown to respond to both error classes.

Note the contrast with the sweep: at 2 Mb the billed window is ~50% fixed overhead, while at
whole-chromosome scale it is 0.7%. Same tool, same box — the workload decides whether the data
path matters at all.

## 4. Why the sweep uses 2 Mb when the recipe runs 64 Mb

**HaplotypeCaller's rate along chr20 varies about 50× with local complexity**, so an interval is
not interchangeable with a chromosome and a per-Mb rate is not a constant:

| region | rate |
|---|---|
| p-arm (chr20:1–20 Mb) | ~9,480 regions/min |
| pericentromeric (chr20:30–31 Mb) | **168 regions/min** — 1.07 Mb in 35.4 min |

Repeat-rich sequence makes local re-assembly produce huge active regions with many candidate
haplotypes, and the centromere is the worst of it. Two practical consequences:

- **Size a whole-chromosome TTL from a whole-chromosome run**, not from a slice, and not by
  extrapolating a partial run linearly — an average over a heterogeneous region does not
  transfer to a sub-window of it, in either direction.
- **Scatter on intervals of similar complexity** if you are splitting work, or one shard lands on
  the centromere and becomes the critical path.

The sweep therefore runs `chr20:1,000,000-3,000,000`, clocked end to end at 77 s on c8g, and the
whole-chromosome number comes from the whole-chromosome run in §3.

## Run it

```sh
export AWS_PROFILE=aws COOKBOOK_BUCKET=<your bucket>

# once: reference index + GIAB truth slice, then build the 36x chr20 BAM on the box
make stage RECIPE=gatk4     # index + truth slice + the 36x chr20 BAM

# the threading question, settled in one task
spawn task run --spec measurements/gatk4-real/canary.task.json --wait

# the generation sweep (four tasks, ~4 min each, each self-terminating)
bash sweep.sh
```

## Caveats

n = 1 per cell. All four sweep rows share one image digest, one interval, one thread count and
one instance size, so they are comparable to each other. Rates are us-west-2 on-demand from
`aws pricing` on 2026-09-26; billed windows are the task's own start/end from spawn's completion
record, so they include image pull, staging and stage-out.

The sweep interval is 2 Mb, which makes billed cost ~50% fixed overhead. For the real
whole-chromosome workload the overhead is ~2.5 min against ~2 h, so the **compute-only** column
is the one that transfers to production; the billed column is shown because it is what an invoice
for *this* run would say.

`--native-pair-hmm-threads` being inert is specific to builds without a native GKL for the
platform. On x86-64 with the AVX libraries loaded, the flag does real work and the sizing advice
inverts — which is the point of stating the mechanism rather than just the timing.
