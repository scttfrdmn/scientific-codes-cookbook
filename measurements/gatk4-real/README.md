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

## 3. Why the sweep uses 2 Mb when the recipe runs 64 Mb

Because **HaplotypeCaller's rate on chr20 varies about 50× with local complexity**, and three
attempts to size a run got that wrong in three different ways. This is the expensive lesson of
this measurement, so it is written down rather than smoothed over:

| attempt | reasoning | what happened |
|---|---|---|
| whole chr20, TTL 70m | 2 Mb canary ran at 1.56 Mb/min → 64 Mb ≈ 41 min | all four died at TTL. The canary sits on the p-arm and opens on a telomere; it is fast, not typical |
| whole chr20, TTL 150m | mid-run: 29.4 Mb at 51.3 min → linear → 112 min | died again. The linear extrapolation *over*-estimated the remaining work, because the slow centromere was already behind it — and still under-estimated the total |
| chr20:30-40 Mb, TTL 55m | 0.54 Mb/min measured over 31.0→48.7 Mb | advanced **1.07 Mb in 35.4 minutes** (168 regions/min vs ~9,480 on the p-arm). 30–31 Mb is pericentromeric heterochromatin: repeat-rich, so local assembly explodes |

The rule that falls out is sharper than "measure, don't guess", which was already being followed:
**an average over a heterogeneous region does not license picking a sub-window of it.** The
0.54 Mb/min figure was a real measurement and it still pointed at the worst available interval.
So the sweep runs the *only* interval with a completed end-to-end timing, and the whole-chromosome
number comes from a whole-chromosome run.

Cost of learning this: about **$1.65** of Graviton time across five abandoned runs.

## Run it

```sh
export AWS_PROFILE=aws COOKBOOK_BUCKET=<your bucket>

# once: reference index + GIAB truth slice, then build the 36x chr20 BAM on the box
bash ../../recipes/gatk4/stage-inputs.sh "$COOKBOOK_BUCKET"
spawn task run --spec ../../recipes/gatk4/00-prep-bam.task.json

# the threading question, settled in one task
spawn task run --spec canary.task.json --wait

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
