# Manta and delly on a whole HG002 genome against GIAB's SV benchmark: three chips

> **Manta calls a whole 30× genome in 3–5 minutes and finds 61% of the benchmark's deletions at 96%
> precision; delly takes 3.6–5× longer and finds 15%.** Both callers write byte-identical calls on
> Graviton4, Intel and AMD. AMD runs both fastest. Graviton4 is cheapest per genome, by under 1%.

Workload: **HG002 (NA24385), whole genome, 30×**. NHGRI's novoalign `HG002.hs37d5.60x.1.bam`
(1.33 billion reads, 2×148 bp, GRCh37 hs37d5) subsampled to 30× with a fixed seed. 30× is the
production depth: it is what GIAB's own downsampled HG002 set targets, to match 1000 Genomes and
most cohort WGS. Both callers read the same BAM bytes on every chip. Calls are scored against
**GIAB HG002 SV v0.6 Tier 1**: 9,641 deletions and insertions ≥50 bp inside its 2.66 Gbp of
confident regions.

**Data tier: RODA.** `s3://giab` (the BAM and the benchmark) and `s3://1000genomes` (hs37d5), copied
server-side into the bucket. The 30× BAM is derived (build tier) by `00-prep`. The sha256 of every
source object and every derived file is in [`pins.sha256`](pins.sha256), and each task checks its
inputs against it before running.

## Run it

```sh
export AWS_PROFILE=aws B=<your bucket>
bash measurements/structural-variants-real/stage-inputs.sh "$B"   # RODA -> bucket, then 00-prep on AWS
for a in arm64 x86_64 amd; do for t in 01-manta 02-delly; do
  spawn task run --wait --spec "$(python3 scripts/spec_arch.py structural-variants $a "$B" "$(date +%s)" \
    measurements/structural-variants-real/$t.task.json)"
done; done
spawn task run --wait --spec "$(python3 scripts/spec_arch.py structural-variants arm64 "$B" "$(date +%s)" \
  measurements/structural-variants-real/03-score.task.json)"           # truvari, all three chips
bash measurements/structural-variants-real/collect.sh "$B" arm64:manta:<nonce> arm64:delly:<nonce> ...
```

The tasks run the recipe's invocations at genome scale ([`manta.sh`](manta.sh), [`delly.sh`](delly.sh)):

```sh
configManta.py --bam HG002.hs37d5.30x.bam --referenceFasta hs37d5.fa --runDir mrun
mrun/runWorkflow.py -m local -j 32 -g 150
delly sr -h 32 -x human.hg19.excl.tsv -g hs37d5.fa -o delly.bcf HG002.hs37d5.30x.bam
```

`human.hg19.excl.tsv` is delly's own telomere/centromere list (`excludeTemplates/` at v2.6.0).
[`mkspec.py`](mkspec.py) writes the specs and pins the staged scripts by sha256.

## Results

32 threads on an `8xlarge` (32 vCPU, 256 GiB) of each chip. Recipe images: aarch.bio on Graviton4,
bioconda on both x86 chips. Same staged bytes on all three:

| chip | instance | caller | tool wall | billed | peak RSS | $/hr | compute $/result | billed $/result |
|---|---|---|---|---|---|---|---|---|
| Graviton4 | `r8g.8xlarge` | manta | 230 s | 690 s | 3.3 GiB | 1.8851 | 0.120 | 0.361 |
| | | delly | 1,140 s | 1,607 s | 1.1 GiB | | 0.597 | 0.842 |
| Xeon 6975P-C | `r8i.8xlarge` | manta | 320 s | 714 s | 3.3 GiB | 2.2227 | 0.198 | 0.441 |
| | | delly | 1,160 s | 1,552 s | 1.1 GiB | | 0.716 | 0.958 |
| **EPYC 9R45** | `r8a.8xlarge` | manta | **199 s** | 510 s | 3.3 GiB | 2.5562 | 0.141 | 0.362 |
| | | delly | **890 s** | 1,191 s | 1.1 GiB | | 0.632 | 0.846 |

A *result* is one whole genome called and checked by one caller. *Billed* is spawn's task
window, wrapper start to stage-out done, at spawn's printed price. *Peak RSS* is the largest single process (`ru_maxrss` over
manta's pyflow tasks; delly's `VmHWM`); the container's cgroup `memory.peak` is 3.4 and 1.1 GiB.
Neither caller links a BLAS. Threads per core: 1 on Graviton4 and AMD, 2 on Intel (16 cores).

- **The box is sized by the BAM, not the callers.** Both callers need under 3.5 GiB. The 59.6 GiB
  BAM plus the 3.2 GB reference has to fit in the half-RAM `/tmp`, and that needs a 256 GiB box.
  Manta's pyflow sizes its task pool from `MemTotal`, which counts the tmpfs as free, so `-g 150`
  tells it what is left. Without the tmpfs, through lith on a 64 GiB `c8g.8xlarge`, delly is
  cheapest ($0.50) and manta 7.6× slower. A tuned copy stages the 67 GB in 40 s instead of 229 s
  ([lith-vs-copy-real](../lith-vs-copy-real/README.md)).
- **AMD is fastest, and Graviton4 is cheapest by a hair.** Per genome (both callers), billed:
  Graviton4 $1.20, AMD $1.21, Intel $1.40. AMD runs manta 1.16× and delly 1.28× faster than
  Graviton4, which almost exactly cancels its 36% dearer hour. Intel's 16 SMT cores run manta
  1.39× slower than Graviton4 and delly at the same speed.
- **Boot and staging are most of manta's bill.** Its 230 s run sits in a 690 s window: boot, image
  pull, 67 GB of stage-in and a 50–190 s sha256 of the inputs. delly is compute-dominated.
- **Where delly's 19 minutes go** (its own log, Graviton4): 4.7 min scanning the BAM, 4.6 min
  split-read assembly, 9.7 min SV annotation. No thread sweep was run, so what its 32 threads buy
  is not measured.
- n = 1 per cell. Both tools are deterministic: a second Graviton4 manta run took 230 s again and
  wrote identical records.

## The checks, at scale, on all three chips

| observable | assertion | observed (every chip) |
|---|---|---|
| 30× subsample | kept fraction **0.5 ± 0.005** (hash-based, ~1e9 reads: 250 sd) | 0.500008; mean depth 29.6 |
| reference | names and lengths **==** 1000 Genomes' published `.fai`; BAM `@SQ` **==** both | 86 contigs; our `.fai` is byte-identical to theirs |
| benchmark | truvari's base count, per type, **==** an independent count of Tier 1 PASS calls 50 bp–50 kb inside one BED interval | 4,199 DEL + 5,442 INS = 9,641 |
| manta completion | pyflow's `successfully completed` line; BGZF VCF; sample == the read group's | yes |
| manta convention | every explicit record: `END == POS+len(REF)−1`; every DEL: `−SVLEN == END−POS`; every explicit INS: `SVLEN == len(ALT)−1` | 0 exceptions in 13,862 records |
| delly convention | every DEL: `\|SVLEN\| ==` deleted length **+ 1** (the recipe's 1001 for 1000) | 0 exceptions in 6,965 |
| delly evidence | `MAD > 0`, discordant pairs > 0, BCF magic | MAD 90, 776,849 pairs |
| precision vs Tier 1 | **≥ 0.8** for both callers: PASS calls in confident regions are mostly real | manta 0.957, delly 0.892 |
| manta ↔ delly | precise deletion pairs mostly claim the same deleted interval | 7 of 8 |
| across chips | every record and every score identical | manta `711477cd…`, delly `9992b21f…` on all three |

Scoring uses GIAB's own published matching rules (`README_SV_v0.6`: `--passonly --includebed`,
`-r 2000`, `-p 0`), the same for both callers, on their DEL and INS calls. BND and INV calls can only
be false positives against a DEL/INS benchmark, so they are counted and set aside (manta 5,920 BND;
delly 4,534 BND and 1,837 INV). Recall is reported, never asserted. The gap between the callers is
the finding, and any shared floor would be tuned to one of them. Like
[callers-real](../callers-real/README.md), this is each tool's documented command, not its ceiling.

| caller | TP | FP | FN | precision | recall | F1 | DEL recall | INS recall |
|---|---|---|---|---|---|---|---|---|
| **manta** | 3,485 | 155 | 6,156 | **0.9574** | **0.3615** | **0.5248** | **0.6101** | 0.1696 |
| manta, DUP as INS | 3,501 | 165 | 6,140 | 0.9550 | 0.3631 | 0.5262 | 0.6101 | 0.1725 |
| delly | 687 | 83 | 8,954 | 0.8922 | 0.0713 | 0.1320 | 0.1500 | 0.0105 |
| delly, DUP as INS | 718 | 147 | 8,923 | 0.8301 | 0.0745 | 0.1367 | 0.1500 | 0.0162 |

- **Insertions are where short reads stop.** They are 56% of the benchmark, and manta recovers 17%
  of them. Read-length-limited insertion recall is expected of any short-read caller. Reading
  manta's DUPs as insertions recovers only 16 more.
- **delly's low recall is in what it emits, not in the scoring.** Of its 5,510 PASS deletions,
  3,655 are under 50 bp, and only 719 are ≥50 bp inside the Tier 1 regions. Manta has 2,678. Its
  ≥50 bp deletions are all written `IMPRECISE`, so only 8 pair precisely with manta's.
- **The recipe's convention table needs a qualifier at scale.** On the fixture, delly wrote its precise
  1 kb deletion as symbolic `<DEL>`, with `END` one past manta's. On this genome, delly writes every
  precise deletion with explicit alleles, and then its `END` is the last deleted base, the same as
  manta's: offset 0 on all 7 same-interval pairs. The +1 belongs to delly's symbolic records, not
  to delly. The `|SVLEN|` = length + 1 rule holds on both forms.

## Across chips

**Identical, all three.** The same manta records (`711477cd…`, 13,862) and the same delly records
(`9992b21f…`, 18,322) on Graviton4, Intel and AMD, from different builds of the same versions.
Every truvari count and score is identical too. These are fixed points of the input bytes: neither
caller samples, and neither links a BLAS.

## Caveats

- One genome, one depth, one benchmark (GRCh37 Tier 1 v0.6, which covers DEL and INS only), and one
  thread count. Neither tool was swept.
- The manta and delly pins are the arm64 entries of signed indexes. aarch.bio signs the index, so
  cosign on the per-arch digest says `no signatures found`. The parents verify: manta
  `7020a19f…` (sign-existing.yml) and delly `b6eac55c…` (publish.yml), both from
  `playgroundlogic/aarchbio` on `main`.
- Prices are on-demand us-west-2: spawn's `cost_estimate` divided by the billed window.
- Spend for this write-up: **$5.09**. That covers prep ($0.64), the six counted runs ($3.81), the
  scoring ($0.02), a prep that stopped at gzip's exit 2 on the RAZF reference's trailing index
  ($0.26), and a first Graviton4 manta run ($0.37) whose insertion check guessed `END == POS`. Manta
  writes `END` as the last replaced base on every explicit record, which the check now asserts. The
  re-run's records were identical.

## Files

`stage-inputs.sh` (RODA to bucket, then `00-prep`), `prep.sh` + `00-prep.task.json`, `manta.sh` +
`01-manta.task.json`, `delly.sh` + `02-delly.task.json`, `score.py` + `03-score.task.json`
(`03-score-arm64.task.json` scores Graviton4 alone), `mkspec.py`, `collect.sh`, `pins.sha256`,
`human.hg19.excl.tsv`. Evidence: `results/prep/`, `results/<chip>/{manta,delly}/` (checks,
timing, host, the tools' own logs, spawn's completion record), `results/score/` (`score.txt`,
truvari's summaries and logs), `results/runs.txt`.
