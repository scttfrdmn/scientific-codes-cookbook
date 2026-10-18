---
tool: dada2
tool_version: "1.38.0"
image: quay.io/aarchbio/bioconductor-dada2@sha256:1dbe837ab143941ae1864da332632f2fca92b1c191a1c8f8644c6a2267148478
last_verified_x86_64: 2026-10-13
images_x86_64:
  - quay.io/biocontainers/bioconductor-dada2@sha256:a51eeb35b252786cabe0653c3ef88ff6075fa7115f5442a7153c039e79d54d32
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

## Which box — [measured](../../measurements/dada2-real/README.md) (whole Kozich 2013 study, 362 samples, 16 threads)

| chip | instance | tool wall | peak RSS | $/hr | compute $ | **billed $/result** |
|---|---|---|---|---|---|---|
| Graviton4 | `c8g.4xlarge` | 354 s | 18.1 GiB | 0.6381 | 0.0628 | **0.0932** |
| Intel | `c8i.4xlarge` | 443 s | 11.3 GiB | 0.7497 | 0.0922 | 0.1220 |
| AMD | `c8a.4xlarge` | 311 s | 11.9 GiB | 0.8622 | 0.0746 | 0.1104 |

**Graviton4 is cheapest per study and AMD is fastest.** All three give a byte-identical 362 × 420 ASV table and reproduce the DADA2 tutorial's printed numbers exactly. Only ~5.5 of 16 cores are busy, because `mergePairs` is single-threaded. The memory peak is `filterAndTrim`'s one fork per thread, so `multithread=` decides the box.

## Make it yours

| In the recipe | Swap for | What to know |
|---|---|---|
| synthetic reads from 3 templates, seed 42 | your demultiplexed FASTQs | real reads carry no truth unless the run has a mock community: sequence one, as the [measured study](../../measurements/dada2-real/README.md) did (20 of 20 strains exact). |
| single-end | paired-end (`filterAndTrim(fwd, filt, rev, filtRev)` + `mergePairs`) | 16S V4 is usually paired; merging adds a step but does not change the shape of the check. |
| no chimera removal, no taxonomy | `removeBimeraDenovo`, `assignTaxonomy` + a reference | taxonomy needs a staged reference database (SILVA/GTDB) — a data-tier decision this recipe deliberately avoids. |

**Leave the fixture:** planted templates let a 2-minute run assert *exact* recovery of every read; the real-study run asserts the same of a mock, at study scale. **Scale it** to your run — and note the fixture's one non-obvious requirement, below, because it will bite you if you ever synthesise reads yourself.

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

### On x86_64

The same task on same-generation Intel (`c8i.large`), from bioconda's own x86 build of the same
version — `quay.io/biocontainers/bioconductor-dada2@sha256:a51eeb35…` (1.38.0, `linux/amd64`; not
cosign-signed, so the digest is the pin):

| | arm64 | x86_64 |
|---|---|---|
| ASVs inferred / exact matches / spurious | 3 / 3 of 3 / 0 | 3 / 3 of 3 / 0 |
| abundances | 4000,2500,1500 | 4000,2500,1500 |
| `smoke-check.txt` sha256 | `3c7e2c69…` | `3c7e2c69…` — **byte-identical** |

The reads are synthesised in-task by R's own `set.seed(42)` generator, which is the same algorithm
in both images — so unlike an awk `rand()` fixture, x86 infers from exactly the reads arm64 did.

### Run + verify

```sh
make run RECIPE=dada2                 # Graviton4
make run RECIPE=dada2 ARCH=x86_64     # same spec, x86 pin, writes runs-x86_64/
make ls  RECIPE=dada2
```

The assertions are `stopifnot()` calls inside the task, so a failure fails the task ([exit 0 isn't proof](../../practices/container-path.md)). Expect `smoke-check.txt` with `exact_seq_matches 3 of 3` and matching abundance lines.

</details>
