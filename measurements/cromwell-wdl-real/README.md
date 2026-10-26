# Cromwell on Broad's published GVCF workflow, whole chr20: three chips

> **Cromwell reproduced every one of its 17 task outputs exactly against the same commands run
> without it, on every chip.** It added about a minute to a run, and its call cache turned a 1.4 h
> resubmission into 38 s. **AMD is the place to run this workflow:** 12.9× faster than Graviton4 and
> 7.6× cheaper per result. GATK's PairHMM is Java-only on Graviton, so the one pericentromeric shard
> takes 83 minutes there and holds up the other fifteen.

Workload: Broad's **published** `haplotypecaller-gvcf-gatk4.wdl`
([gatk4-germline-snps-indels 2.3.1](https://github.com/gatk-workflows/gatk4-germline-snps-indels/tree/2.3.1), unmodified)
over **the whole of chr20** of NA12878 at 36× (the NYGC `ERR3239334` BAM that
[gatk4-real](../gatk4-real/README.md) built). It scatters HaplotypeCaller over 16 interval files in
GVCF mode, then gathers with `MergeVcfs`. Calling a GVCF per sample is what this workflow is for, and
a chromosome at 36× is the unit GATK's own test data uses. It runs 17 real tasks with one heavy
shard, which is the problem a scatter-gather engine has to handle.

**Data tier: shared inputs plus durable public sources.** The BAM, reference and dictionary are
`recipes/gatk4`'s, reused and not copied (BAM sha256 `0ad228c1…`, checked in every run). The calling
regions are Broad's `wgs_calling_regions.hg38.interval_list` from `s3://broad-references` (RODA). They
are scattered 16 ways by GATK's `IntervalListTools` with warp's `ScatterIntervalList` settings
(balanced without subdivision, 1 Mb bands). The WDL comes from the release tag and the Docker CLI
from Docker's static 27.5.1 build. Every file is pinned in [`pins.sha256`](pins.sha256).

## Run it

```sh
export AWS_PROFILE=aws B=<your bucket>
bash measurements/cromwell-wdl-real/stage-inputs.sh "$B"      # 00-stage + 00-intervals, on AWS
for a in arm64 x86_64 amd; do
  spawn task run --wait --spec "$(python3 measurements/cromwell-wdl-real/mkspec.py $a "$B" r1 \
    measurements/cromwell-wdl-real/01-run.task.json)"
done
spawn task run --wait --spec "$(python3 measurements/cromwell-wdl-real/mkspec.py arm64 "$B" r1 \
  measurements/cromwell-wdl-real/02-compare.task.json)"       # the three GVCFs, compared in the bucket
bash measurements/cromwell-wdl-real/collect.sh "$B" r1         # results/<chip>/
```

[`run.sh`](run.sh) runs Cromwell 40 in the recipe's pinned image, through the published WDL:

```sh
java -Dconfig.file=cromwell.conf -jar cromwell.jar run haplotypecaller-gvcf-gatk4.wdl --inputs inputs.json
```

Here `cromwell.conf` selects the local backend with `concurrent-job-limit = 16`, call caching on a
file-backed HSQLDB, and a `submit-docker` that starts each task as a **sibling container** of the
pinned GATK image through the host's Docker socket. That is the single-box Docker shape the local
backend is built for. spawn's container path does not expose the socket, so `01-run` is a **host
task**: it installs Docker and runs Cromwell's container itself. For the same reason, `mkspec.py`
rather than `scripts/spec_arch.py` writes the x86 and AMD specs (spec_arch rewrites a spec's
`container`). It uses spec_arch's own family and cost tables. Inputs: `gatk_docker` = the
[gatk4 recipe](../../recipes/gatk4/README.md)'s pin for the chip, `gatk_path` = `gatk`.

## Results

16 concurrent jobs on a `4xlarge` (16 vCPU, 128 GiB) of each chip. Cromwell 40 and GATK 4.6.2.0:
aarch.bio's builds on Graviton4, bioconda's x86 builds on both x86 chips. Same staged bytes on all
three:

| chip | instance | workflow wall | slowest shard | spawn start → workflow end | peak RSS | $/hr | compute $/result | $/result to workflow end |
|---|---|---|---|---|---|---|---|---|
| Graviton4 | `r8g.4xlarge` | 5,089 s | 4,999 s | 5,184 s | 17.0 GiB | 0.9426 | 1.332 | 1.357 |
| Xeon 6975P-C | `r8i.4xlarge` | 507 s | 452 s | 617 s | 17.9 GiB | 1.1114 | 0.157 | 0.190 |
| **EPYC 9R45** | `r8a.4xlarge` | **396 s** | **314 s** | **503 s** | 18.0 GiB | 1.2781 | **0.141** | **0.179** |

A *result* is one sample's chr20 GVCF, made by the workflow. *Workflow wall* is Cromwell's first,
cold run. *Spawn start → workflow end* is spawn's task window from its wrapper start to the end of that
run, a sub-window of the billed task. The task then spent another 5,328 / 629 / 509 s on the checks
below (one of them the same 16 commands again without Cromwell); the full billed window, wrapper
start to stage-out done (`results/<chip>/completion.json`), was 10,512 / 1,246 / 1,012 s, costing
$2.752 / $0.385 / $0.359. *$/hr* is spawn's `cost_estimate` divided by its window.
*Peak RSS* is the `memory.peak` of the cgroup that holds every task container, page cache included,
while all 16 shards run. One HaplotypeCaller shard peaks at 1.29–1.71 GiB (`ru_maxrss`), and the
Cromwell JVM at 1.16–1.47 GiB. Threads per core: 1 on Graviton4 and AMD, 2 on Intel. GATK is Java
and links no OpenBLAS.

- **One shard is the whole wall time on Graviton4.** Shard 7 covers chr20:29–33 Mb, the
  pericentromere. It takes 4,999 s, while the other fifteen take 172–1,621 s. For the last 56
  minutes, 15 of 16 cores sit idle. On x86 the same shard takes 452 / 314 s against a median of about 260 / 212
  s. The reason is [gatk4-real](../gatk4-real/README.md)'s: Intel's GKL PairHMM is x86-only. On
  Graviton every shard runs Java's `LOGLESS_CACHING` on one thread, and repeat-rich active regions
  hit it hardest. Split that region more finely if you run this on Graviton.
- **AMD wins on speed and on price.** It is 1.28× faster than Intel, enough to offset a 15% dearer
  hour, so a result costs 6% less. Graviton4 is the cheapest hour and the most expensive result.
  The published WDL leaves `--native-pair-hmm-threads` at its default of 4, so on x86 the 16 shards
  ask for 64 PairHMM threads on 16 vCPUs. Both x86 chips ran it as published.
- **Cromwell's own cost is about a minute.** Run wall, minus the slowest shard, minus the merge,
  is 66 / 39 / 67 s. The same 16 commands run in parallel without Cromwell, then merged, took
  4,832 / 431 / 315 s against Cromwell's 5,089 / 507 / 396 s.
- **Size by the workflow's own request, then by measurement.** The WDL asks 7 GB per
  HaplotypeCaller shard (`-Xmx6G`): 16 × 7 = 112 GB, which only a 128 GiB `r8` honours. The measured
  aggregate is 17–18 GiB, so an `m8` (64 GiB) would hold this sample. Size from measurement for your
  own data.
- n = 1 per cell. HaplotypeCaller is deterministic within a build: Cromwell's run and the direct
  run, and the call-cache rerun, produce the same bytes on each chip. A repeat would re-measure only
  timing.

## The identities, at scale, on all three chips

| observable | assertion | observed (every chip) |
|---|---|---|
| the published WDL | `womtool validate` succeeds, sha256 `607eb8e2…` | Success! |
| workflow | Cromwell's metadata: `Succeeded`, 16 HaplotypeCaller calls + 1 merge, all `Done` | 16 + 1 |
| scatter order | shard *i* was handed interval file *i* | identical |
| **every shard** | **GVCF == the same command run directly, same image** (body; `##GATKCommandLine` excluded) | **16 of 16** |
| **merged GVCF** | **== `MergeVcfs` of the direct shards** | identical |
| other header lines | no difference besides `##GATKCommandLine` | 0 lines |
| **cache: identical resubmission** | **17 of 17 calls are cache hits**, output bytes == run 1 | **17 / 17** |
| **cache: one input edited** (`@CO` line in shard 0's interval file) | **exactly shard 0 and the merge re-run**, 15 hits; output body == run 1 | **15 hits, shard 0 + merge** |
| merge conservation | merged records == Σ shard records | 7,653,025 (arm64) / 7,653,033 (x86) |
| **GVCF coverage** | **every base of the 63,948,766 calling-region bp is in a record**, none outside | **63,948,766 in, 0 out** |
| records | sorted, sample `NA12878` (contigs: observed) | yes (`chr20` only) |
| scatter (00-intervals) | 16 shards conserve the 63,948,766 bp | 63,948,766 |

- **The direct run checks the engine, not just the tool.** Its commands are written by hand from
  the WDL's template, not copied from Cromwell's rendered scripts. So agreement on all 16 shards
  confirms what Cromwell substituted into each command: interval file, flags and memory, as well as
  its execution. Shard order is checked separately, because the merge would hide a permutation.
- **The cache tests run in both directions.** The identical resubmission must hit 17 of 17. The
  edited interval file has the same intervals in different bytes, so the cache must miss on exactly
  that call and on the merge downstream of it, and on nothing else. That edit re-ran shard 0
  (319 / 281 / 215 s) and the merge, and left the result's body unchanged.
- **The coverage identity is GVCF's contract.** Every base of every interval is either in a
  reference block or in a variant record. A dropped shard or a truncated gather would fail it by
  millions of bases, even where the record count looked plausible.

## Across chips

- **Intel and AMD: byte-identical.** Same x86 build, and both load GKL's AVX-512 PairHMM. The
  merged GVCF bodies match (`fd2c47e8…`), as do all 7,653,033 records.
- **Graviton4: the same calls, different likelihood arithmetic.** All 152,907 variant sites match
  the x86 set exactly (0 only on either side), and **all 152,907 genotypes agree**. 3 GQs differ.
  Of about 7.65M records, 7,652,193 are byte-identical. The other 832 (arm64) and 840 (x86) are
  where a reference block splits at a different GQ band. Graviton runs Java's `LOGLESS_CACHING`
  PairHMM in double precision, and x86 runs GKL's AVX-512 kernel with flush-to-zero. They are
  different implementations, so the likelihoods differ in their last digits. That makes this a
  build difference, not a path or a fixed point. It is reported, not asserted, because the build
  cannot be separated from the chip.

## Caveats

- One sample, one chromosome, one scatter width (16), one Cromwell (40, the newest bioconda ships;
  the WDL says it was tested on 53). The local backend only: no server mode, no cloud backend. To fan
  out across instances, use spawn's workflow adapters ([execution shapes](../../patterns/execution-shapes.md)).
- The checks (cache reruns, the direct rerun) double the task's bill. The *workflow* columns are
  what running the workflow costs.
- Spend for this write-up: **$3.57**. That covers staging ($0.002), a 3-shard probe ($0.07), the
  three runs ($2.75, $0.38, $0.36) and the compare ($0.004).

## Files

`fetch.sh` + `00-stage.task.json` (WDL, calling regions, Docker CLI to bucket), `intervals.sh` +
`00-intervals.task.json` (the scatter), `run.sh` + `rss.py` + `meta.py` + `gvcf.py` +
`01-run.task.json` (`01-probe.task.json`: the same on 2 Mb), `compare.py` + `02-compare.task.json`,
`mkspec.py`, `stage-inputs.sh`, `collect.sh`, `pins.sha256`. Per-chip evidence is in
`results/<chip>/`: `check.txt`, `timing.txt`, `host.txt`, Cromwell's three logs and per-call tables,
each shard's GATK log, and spawn's completion record. `results/compare/compare.txt` is the
cross-chip comparison, `results/probe/` the sizing probe.
