# MACS2 on a real workload — four Graviton generations, and the cleanest per-core number in the catalog

> **A single-threaded Python/Cython peak caller gains 2.40× from Graviton2 to Graviton5 — essentially
> tying GROMACS, the most vectorised FP code measured here, and well ahead of SIESTA's dense linear
> algebra at 1.86×.** Every generation is faster *and* cheaper per result, and the answer is
> byte-identical on all four.

MACS2 2.2.9.1 calling peaks genome-wide on a full ENCODE CTCF ChIP-seq experiment — treatment
`ENCFF933NSJ` (1.92 GiB) against its matched input control `ENCFF768XTH` (3.13 GiB), HCT116, GRCh38 —
`-f BAM -g hs -q 0.05`, 4 vCPU and 32 GiB on every rung, same image digest, same input objects.

| generation | instance | CPU part | macs2 | billed | $/hr | **$/run** | compute-only $ |
|---|---|---|---|---|---|---|---|
| Graviton2 | `r6g.xlarge` | `0xd0c` | 741 s | 865 s | 0.2016 | **0.0484** | 0.0415 |
| Graviton3 | `r7g.xlarge` | `0xd40` | 514 s | 626 s | 0.2142 | **0.0372** | 0.0306 |
| Graviton4 | `r8g.xlarge` | `0xd4f` | 389 s | 487 s | 0.2357 | **0.0319** | 0.0255 |
| **Graviton5** | `r9g.xlarge` | `0xd84` | **309 s** | 395 s | 0.2569 | **0.0282** | **0.0221** |

Per-step speedup **1.44×, 1.32×, 1.26×** — decelerating but never flat. Cost falls monotonically on both
the billed window and the compute-only basis, so the conclusion does not depend on how boot is
attributed. **Gv2 to Gv5: 2.40× faster and 41.8% cheaper per result.**

## Why this measurement is worth more than one more row

**It is the catalog's cleanest *per-core* generation number, because MACS2 cannot use more than one
core.** Measured `avg_cores` is **1.01, 1.01, 1.01, 1.00** across the four rungs — the tool is
single-threaded by construction, so nothing about thread scaling or parallel efficiency contaminates
the comparison. Most of the catalog's generation evidence comes from multi-threaded runs where
per-core IPC and scaling behaviour are entangled; here they cannot be.

That makes the size of the gain the interesting part. Against the existing evidence:

| code | kind of work | Gv2 → Gv5 |
|---|---|---|
| GROMACS | heavily vectorised FP MD | 2.43× |
| **MACS2** | **single-threaded Python/Cython, sort + pileup + Poisson** | **2.40×** |
| GPAW | plane-wave DFT | 2.33× |
| LAMMPS | classical MD | 2.24× |
| picard MarkDuplicates | JVM sort/hash, integer + IO | 1.93× |
| SIESTA | dense linear algebra, modest matrices | 1.86× |

**The tidy story — newer Graviton adds vector throughput, so FP-heavy codes gain most — is now dead
twice over.** [picard](../picard-real/README.md) already put a JVM integer/IO tool (1.93×) above
SIESTA's dense FP (1.86×). MACS2 goes further: a single-threaded interpreted-language peak caller
lands *at the top of the range*, tying the most aggressively vectorised code in the catalog. Whatever
the generations are improving, it is not reducible to instruction mix.

It also says the gains are substantially **per-core** rather than an artifact of scaling better across
more cores — which a reader choosing an instance can use directly: on a single-threaded tool, the
newer generation is the only lever available, and it is worth 2.4×.

This motivates the working-set hypothesis rather than establishing it. Attributing it needs a
bandwidth and cache measurement, not another wall clock.

## The identity across chips

**`real_peaks.narrowPeak` is byte-identical on all four generations** — sha256
`687b97a9bc39fed53555d7e566dd2d44f49cd926d1b91ebaea3ecc7c1638c54d`, 50,276 peaks, 50,276 summits,
zero peaks below the `-q 0.05` cutoff everywhere. The `r8g` rung also reproduces the `m8g.2xlarge`
clocking run exactly (389 s, same hash), which is the internal control: same Graviton4 silicon, a
different instance family, no difference in the answer.

That the hash matches matters more than the peak count, because the file carries q-values to five
decimals. **It is also a clean negative result for the kernel-dispatch risk**: conda's OpenBLAS is
`DYNAMIC_ARCH` and re-selects kernels from the host CPU, and the four rungs here span four distinct
cores — yet nothing moved. MACS2's numpy work is pileups and Poisson tails, not large blocked GEMM, so
the dispatcher never reaches kernel-specific code. That is the third condition from the project's own
OpenBLAS finding, confirmed on a fourth code.

`tmpfs_peak_mib` is **11,409 on every rung**, identical to the byte — the staging footprint is set by
the data, not by the box.

## Sizing: the box is bought for /tmp, not for compute

Measured on the `m8g.2xlarge` clocking run before any sweep: macs2 389 s, peak RSS 7.6 GiB,
`avg_cores` 1.01, **tmpfs high-water 11.1 GiB** against staged inputs of only 5.05 GiB.

Since `/tmp` is a tmpfs at half the instance RAM, 11.1 GiB of staging demands **≥ 22.3 GiB of RAM** —
so the 16 GiB box this recipe would otherwise have been sized onto (4 vCPU is ample for a
single-threaded tool) **would have run out of staging space**. The over-provisioned instrument is what
caught it; a guess would not have.

Hence `r*.xlarge`: 4 vCPU because 1.01 cores are used, 32 GiB because `/tmp` needs it. That is also
26% cheaper than the `m8g.2xlarge` the clocking run used, for identical work — the instrument is not
the recommendation.

**I cannot yet account for the gap between 5.05 GiB of staged input and the 11.1 GiB high-water mark.**
It reproduces to the byte on all four generations, so it is deterministic rather than noise, but the
cause is unestablished and is recorded here as an open question rather than explained away.

## Run it

```sh
export AWS_PROFILE=aws COOKBOOK_BUCKET=<your bucket>
bash stage-inputs.sh "$COOKBOOK_BUCKET"          # server-side copy from encode-public, same region

for f in r6g r7g r8g r9g; do
  sed "s|\${COOKBOOK_BUCKET}|$COOKBOOK_BUCKET|g" gen-$f.task.json > /tmp/g.json
  spawn task run --spec /tmp/g.json --region us-west-2 --wait
done
```

Launch the rungs **serially**. truffle's static price table has no Graviton coverage
([truffle#175](https://github.com/spore-host/truffle/issues/175)), so concurrent launches carrying a
`cost_limit` can be refused.

`spawn task run` does not substitute `${COOKBOOK_BUCKET}` — pass a spec with the bucket resolved, or
stage-in fails on a literal `${COOKBOOK_BUCKET}` bucket name.

## Pins

| | |
|---|---|
| treatment | `ENCFF933NSJ.bam`, 2,057,454,374 B, ENCODE md5 `48f06f46ac59b93e6ae3110de9730a3e` |
| control | `ENCFF768XTH.bam`, 3,365,735,271 B, ENCODE md5 `ef4683318e7e0fede279fc5717116ccb` |
| image | `quay.io/aarchbio/macs2@sha256:ca577fd2…` |

ENCODE publishes an md5 per file in its metadata API, so these are checkable **at source** rather than
only against ourselves — the same sourcing tier as [metaphlan](../../recipes/metaphlan/README.md)'s
database. The md5 gate runs inside the task, next to the bytes it is gating.

`encode-public` is in **us-west-2**, so caching into our bucket is a same-region server-side copy: no
local transfer, no cross-region hop to justify.

## Caveats

**n = 1 per generation.** Defensible only because the result is byte-identical on all four rungs and
the per-step speedups fall smoothly (1.44 / 1.32 / 1.26); a rung landing off that trend would need
repeating before it could be reported.

**One instance size, one input.** MACS2 is single-threaded, so there is no core knee to find — the
generation axis is the whole question here, which is exactly why it isolates per-core improvement so
cleanly. A deeper ChIP or a broader-mark experiment (H3K27me3 rather than CTCF) would change both the
peak count and the runtime, and could move the ratios.

**The chr20 comparison is an observation, not a check.** This whole-genome run reports **1,397** peaks
on chr20 where the shipped [chr20-only recipe](../../recipes/macs2/README.md) reports **1,390**. They
are not expected to match — the background lambda and the effective genome size both differ when the
whole genome is in the model — so the 0.5% agreement is interesting but asserted nowhere.
