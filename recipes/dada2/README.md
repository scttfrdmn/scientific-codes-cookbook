---
tool: dada2
tool_version: "1.38.0"
image: quay.io/aarchbio/bioconductor-dada2@sha256:1dbe837ab143941ae1864da332632f2fca92b1c191a1c8f8644c6a2267148478
spawn_version: 0.111.1
last_verified: 2026-09-20
---
# DADA2 — exact sequence variants, recovered from reads whose truth we planted

DADA2 infers amplicon sequence variants from noisy reads on Graviton4, checked against the three templates the reads were generated from. The catalog's first 16S/amplicon recipe, for anyone doing microbial community profiling.

> **What this covers.** 8000 single-end 250 bp reads generated in-code from three templates with ~0.4% substitution error and realistic declining quality. Error learning, denoising and ASV inference. Not paired-end merging, chimera removal, or taxonomy assignment against a reference.

## Run it

```bash
spawn task run --spec "$(make -s spec RECIPE=dada2)" --wait
```

```r
filterAndTrim("reads.fastq", "filt.fastq", truncQ=2, maxN=0, maxEE=2)
err <- learnErrors("filt.fastq")               # fit the error model from the data
d   <- dada("filt.fastq", err=err)             # denoise to exact sequence variants
asv <- getSequences(makeSequenceTable(d))
```

One task: generate reads from known templates, denoise, then require the templates back.

## Make it yours

| In the recipe | Swap for | What to know |
|---|---|---|
| synthetic reads from 3 templates, seed 42 | your demultiplexed FASTQs | the planted templates are the whole point — with real reads you have no truth, only DADA2's internal consistency. |
| single-end | paired-end (`filterAndTrim(fwd, filt, rev, filtRev)` + `mergePairs`) | 16S V4 is usually paired; merging adds a step but does not change the shape of the check. |
| no chimera removal, no taxonomy | `removeBimeraDenovo`, `assignTaxonomy` + a reference | taxonomy needs a staged reference database (SILVA/GTDB) — a data-tier decision this recipe deliberately avoids. |

**Leave the fixture:** planted templates let the recipe assert *exact* recovery, which no real dataset can support. **Scale it** to your run — and note the fixture's one non-obvious requirement, below, because it will bite you if you ever synthesise reads yourself.

## Shape, size, cost

One task, `c8g.large` (2 vCPU / 4 GiB), TTL 15m, cap $0.05. Recorded window **1m48s**, most of it R startup and error-model fitting rather than denoising. **These timings are not compute cost.**

<details>
<summary>As shipped: four exact checks, the quality-variation requirement, pins, smoke check</summary>

### Four checks, all exact-or-wrong

No bands anywhere — every one of these either holds or DADA2 got the biology wrong:

| observable | assertion | observed |
|---|---|---|
| ASV count | `== 3` — no over-splitting into error variants, no collapsing of true ones | 3 |
| sequence identity | every inferred ASV is **string-identical** to a planted template | 3 of 3 |
| spurious ASVs | `== 0` — error-derived variants must collapse onto their parent | 0 |
| abundance | inferred counts `identical()` to the planted counts | **4000, 2500, 1500** = planted |

The abundance check is the strongest and the one worth reading twice: DADA2 assigned **every one of 8000 reads** to its correct template despite ~0.4% substitution error. That is not a tolerance being met, it is a partition being exactly right — so it is asserted with `identical()` rather than a correlation.

This is the [constructed-truth](../photutils/README.md) shape: because the reads are generated from known templates, the answer exists before the tool runs. Where a real dataset would force a band, a planted one gives equality.

### The fixture requirement that is easy to get wrong

The first attempt failed with `Error rates could not be estimated (this is usually because of very few reads)` — and the cause was the fixture, not the data volume. **Every base had the same quality score (Q35).** `learnErrors` models error rate *as a function of quality*, so a single quality value leaves it one bin and nothing to fit.

The fix is to make quality **vary** the way a real instrument does: sampled per base around a mean that declines along the read (here Q12–40, mean falling from ~36 to ~28). Anyone generating synthetic reads for DADA2 needs this; the read count was never the problem.

### Pins (data tier: synthetic / in-code)

| | |
|---|---|
| image | `quay.io/aarchbio/bioconductor-dada2@sha256:1dbe837a…` (DADA2 1.38.0, R 4.5.3) |
| input | none — templates, reads and quality strings are generated in-task from seed 42 |

**Determinism:** a fixed seed drives template generation, error placement and quality sampling, and DADA2's inference is deterministic given its input. The Graviton4 run reproduced the local arm64 run exactly, including the abundance partition.

Two image notes, the same pair as the other conda-image recipes: R needs `TMPDIR`/`HOME` writable or it dies with `cannot create 'R_TempDir'`, and spawn's task shell does not inherit the image's `PATH`, so `/opt/conda/bin` must be exported before `Rscript` is callable.

### Run + verify

```sh
make run RECIPE=dada2
make ls  RECIPE=dada2
```

The assertions are `stopifnot()` calls inside the task, so a failure fails the task ([exit 0 isn't proof](../../practices/container-path.md)). Expect `smoke-check.txt` with `exact_seq_matches 3 of 3` and matching abundance lines.

</details>
