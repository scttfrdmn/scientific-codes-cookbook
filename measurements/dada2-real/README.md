# DADA2 on a real study: the whole Kozich 2013 MiSeq run, Graviton4, Intel and AMD

> **DADA2 reproduces its own published tutorial on every chip, to every printed number. On the
> whole 362-sample study it was cut from, all three chips give a byte-identical ASV table.**
> One study costs **$0.09 billed on Graviton4**, $0.11 on AMD and $0.12 on Intel. AMD has the
> shortest tool wall.

Workload: the **complete** "stability" study of Kozich et al. 2013 (*AEM* 79:5112). That is
2×250 V4 MiSeq data for 360 mouse-gut samples (12 mice, time series) and 2 HMP mock-community
samples. In total 362 samples and 3,651,784 read pairs, as the Schloss lab distributes it. The
DADA2 tutorial's 20 samples are a subset of it. Two parts run in one task:

- **A** runs the [DADA2 tutorial](https://benjjneb.github.io/dada2/tutorial.html) verbatim on
  its own 20 samples, and asserts the numbers the tutorial prints.
- **B** runs the same calls on all 362 samples: `filterAndTrim(truncLen=c(240,160), maxEE=c(2,2))`,
  `learnErrors`, `dada`, `mergePairs`, `removeBimeraDenovo(method="consensus")`.

**Why this size is the real one:** a 16S study is one sequencing run of a few hundred samples,
denoised together. This is a whole run of a published study, not a subset. Samples are the axis
DADA2 parallelises over, and `learnErrors` learns from the first 1e8 bases (42 forward and 62
reverse samples here) whatever the study size. One difference from current data: 2013 MiSeq
depth is low, with a median of 7,989 read pairs per sample. A modern run at 30–50k per sample
would make the `dada` and `mergePairs` phases longer, but would not change what they compute.

## Run it

```sh
export AWS_PROFILE=aws
bash measurements/dada2-real/stage-inputs.sh "$COOKBOOK_BUCKET"   # S3 -> S3 from s3://mothur
for a in arm64 x86_64 amd; do
  spawn task run --spec "$(python3 scripts/spec_arch.py dada2 $a "$COOKBOOK_BUCKET" "$(date +%s)" \
    measurements/dada2-real/study.task.json)" --wait
done
```

The task checks `pins.sha256` against its own hash and every staged byte against the pins.
Then [`run.sh`](run.sh) records the box and runs [`dada2-real.R`](dada2-real.R) under a footprint
sampler. [`mkspec.py`](mkspec.py) regenerates the pins and the spec after a script edit. The x86
legs use the recipe's `images_x86_64` pin, bioconda's linux-64 build of the same DADA2 1.38.0.

## Results

16 vCPU / 32 GiB, one `4xlarge` per chip, 16 threads, same input bytes:

| chip | instance | tool wall | billed | peak RSS | $/hr | compute $/result | **billed $/result** |
|---|---|---|---|---|---|---|---|
| Graviton4 | `c8g.4xlarge` | 354 s | 526 s | 18.1 GiB | 0.6381 | 0.0628 | **0.0932** |
| Xeon 6975P-C | `c8i.4xlarge` | 443 s | 586 s | 11.3 GiB | 0.7497 | 0.0922 | **0.1220** |
| EPYC 9R45 | `c8a.4xlarge` | 311 s | 461 s | 11.9 GiB | 0.8622 | 0.0746 | **0.1104** |

*Tool wall* is all of `dada2-real.R`: R start-up, unpacking, part A (32–46 s) and part B
(273–390 s). *Billed* runs from launch to terminate. *Peak RSS* is the container's peak anonymous
memory.

- **Graviton4 is cheapest per study, and AMD is fastest.** AMD runs 12% faster than Graviton4
  and costs 18% more billed, because its hour costs 35% more. Intel is 25% slower than Graviton4
  and costs 31% more. Its 16 vCPU are 8 cores with SMT (`threads_per_core 2`); Graviton4 and AMD
  have 16 full cores each.
- **Most of the box is idle.** On average 5.5 of 16 cores are busy (7.5 on Intel). `mergePairs`
  is single-threaded and takes 70–79 s of part B on every chip. `learnErrors` and `dada` thread
  within each sample. A smaller box would cost less if it had the memory (next point).
- **The memory peak is `filterAndTrim`, not denoising.** It forks one R worker per thread. That
  peaked at 18.1 GiB on the arm64 image and 11.3–11.9 GiB on the x86 image, against about 1.5 GiB
  for the rest of the run. So the peak scales with `multithread=`, and that is what fixes the box.
- **Overhead is 143–172 s per run**: boot, Docker install, the image pull, staging 0.9 GB, and
  shutdown. That is 24–33% of the bill.
- n = 1 per cell for timing. A second, identical Graviton4 run (the sizing run) produced
  byte-identical outputs and its tool wall was within 7%.

## What held at scale, on all three chips

Every row is a `stopifnot`-style assertion inside the task. A failure fails the task.

| claim | kind | observed (all three chips) |
|---|---|---|
| tutorial `filterAndTrim` head (6 samples, in/out) | published reference | **identical**, 7793/7113 … 4827/4312 |
| tutorial `learnErrors` bases, forward and reverse | published reference | **33514080 and 22342720** bases in 139642 reads, as printed |
| tutorial sample 1: variants inferred | published reference | **128** |
| tutorial `dim(seqtab)`, length table 251–255 | published reference | **20 × 293**; 1 / 88 / 196 / 6 / 2 |
| tutorial `dim(seqtab.nochim)`, non-chimeric fraction | published reference | **20 × 232**, **0.9640374** |
| tutorial `track` head (6 samples × 6 stages) | published reference | **36 of 36** counts identical |
| tutorial Mock: 20 ASVs, all exact reference matches | planted truth | **20 / 20**, 0 non-reference reads |
| whole study, Mock: still 20 / 20 under the study-wide error model | planted truth | **20 / 20**, abundances identical to part A |
| the 20 tutorial samples inside the study are the same reads, and filter to the same counts | invariance | **20 of 20** byte-identical, counts equal |
| a sample denoised alone and single-threaded == the same sample in the 16-thread study batch | invariance (`pool=FALSE`) | identical |
| filtered ≤ input; denoised ≤ filtered; merged ≤ min(denoisedF, denoisedR) | conservation | all 362 samples |
| `rowSums(seqtab)` == merged reads per sample; chimera removal only drops columns | conservation | exact |

The tutorial was built with DADA2 1.37.0, and this image is 1.38.0. Every printed number still
reproduced. **Reported, not asserted:** the second mock (`Mock2`) gives 23
ASVs. 22 are exact reference matches, and the 23rd has 19 reads (0.17%), 11 mismatches from the
nearest reference. That is too far to be an error variant DADA2 failed to collapse, so it is a
contaminant or an undetected chimera. The study gives 752 ASVs before chimera removal and 420
after (non-chimeric fraction 0.9617226), from 3,351,305 filtered pairs.

## What differed across chips: the error model in its last bits, and nothing downstream

| output | Intel vs Graviton4 | AMD vs Graviton4 | AMD vs Intel |
|---|---|---|---|
| ASV table (362 × 420), per-sample track, ASV totals | byte-identical | byte-identical | byte-identical |
| fitted error matrices `errF`/`errR` (656 cells each) | 455 / 480 cells differ, max relative **9.2e-15** | the same | **bit-identical** |

The error model is a loess fit inside an alternating fixed-point loop. It differs in the last few
ULPs between the arm64 and x86 builds (R 4.5.3 / OpenBLAS 0.3.34 vs R 4.5.2 / OpenBLAS 0.3.30),
and is bit-identical between Intel and AMD, which run the same image. So the difference comes
from the **build, not the vendor**. DADA2's partition decisions compare p-values computed from those
rates against `OMEGA_A = 1e-40`. A 1e-14 relative change cannot move them, and none did: every ASV, every
abundance and every read count is the same on all three chips. The full-precision matrices are
reported and never asserted. The ASV table is the fixed point, and it is what a study keeps.

## Pins

| | |
|---|---|
| data | `s3://mothur/data/MiSeqDevelopmentData/StabilityNoMetaG.tar`, sha256 `bbbe46ce…5777ba`; `s3://mothur/wiki/miseqsopdata.zip` (tutorial samples + `HMP_MOCK.v35.fasta`), sha256 `728f7544…a35d4`. Tier: stable public source, the authors' own distribution ([stage-inputs.sh](stage-inputs.sh), [pins.sha256](pins.sha256)) |
| published reference | DADA2 tutorial (benjjneb.github.io/dada2/tutorial.html, built with 1.37.0); Kozich et al. 2013, doi:10.1128/AEM.01043-13 |
| arm64 image | `quay.io/aarchbio/bioconductor-dada2@sha256:1dbe837a…` (1.38.0, R 4.5.3) |
| x86 image (Intel, AMD) | `quay.io/biocontainers/bioconductor-dada2@sha256:a51eeb35…` (1.38.0, R 4.5.2) |
| raw reports | [results/](results/): `results.tsv`, `footprint.txt`, `box.txt`, tracks and output sha256s, per chip |
