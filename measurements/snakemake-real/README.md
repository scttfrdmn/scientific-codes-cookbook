# Snakemake + the spawn executor on a real cohort: three chips

> **The executor ran the recipe's workflow over eleven whole FASTQ files (446.0M reads) on twelve
> instances per chip. Every count was exact against its published number, and every output was
> byte-identical to the same commands run without Snakemake and across all three chips. Every
> incremental re-run ran exactly the jobs its change implied.** A cohort costs about $0.05 billed.
> The wall time is the catch: 1–1.6 h for 4–6 minutes of seqkit. The executor dispatches one
> job at a time, and each dispatch waited 2–13 minutes in spawn before EC2 was called.

Workload: [recipes/snakemake](../../recipes/snakemake/README.md)'s workflow, unchanged in shape.
One wildcard job per input file runs seqkit stats and checks its own file against a published
number, then one join checks the fan-out against its own sum. It runs over **eleven complete FASTQ
files from seven public runs and four assays**: 22.9 GB compressed, 446.0M reads, 32.2 Gbp.
[qc-aggregate-real](../qc-aggregate-real/README.md) assembled this cohort as the size of a
sequencing core's mixed project directory, and a per-file QC fan-out is what Snakemake is used for
on one. No file is subsampled.

**Data tier: shared inputs, nothing new fetched.** The FASTQs are read where their owning
measurements staged them: bwa-real (RODA `s3://1000genomes`), salmon-real and mageck-real (ENA),
and metaphlan-real (RODA SRA). Each job checks its file's sha256 against
[qc-aggregate-real's pins](../qc-aggregate-real/inputs.sha256) and its counts against the
published read and base counts in [its cohort table](../qc-aggregate-real/cohort.tsv) (ENA
`read_count`/`base_count`; the 1000 Genomes `.filt` count for `SRR062634`).
`stage-inputs.sh` uploads those values as one small truth file per FASTQ.

## Run it

```sh
export AWS_PROFILE=aws B=<your bucket>
bash measurements/snakemake-real/stage-inputs.sh "$B"          # truth files + direct.sh, a few KB
for chip in arm64 x86_64 amd; do
  for phase in r1 noop touch1 touch2 direct; do
    bash measurements/snakemake-real/run.sh $chip $phase "$B"
  done
done
python3 measurements/snakemake-real/check.py                    # results/check.txt
python3 measurements/snakemake-real/collect.py r1               # timings and cost, per job
```

[`run.sh`](run.sh) is the recipe's invocation, run from the submitting machine. The chip is the
`--spawn-instance-type` family, and `--jobs 1` is used because of
[#19](https://github.com/spore-host/snakemake-executor-plugin-spawn/issues/19):

```sh
snakemake -s Snakefile --config bucket="$B" --executor spawn --shared-fs-usage none \
  --spawn-region us-west-2 --spawn-ttl 60m --spawn-cost-limit 0.05 --spawn-instance-type c8g.large \
  --default-storage-provider s3 --default-storage-prefix "s3://$B/runs/snakemake-real/r1" --jobs 1
```

The rules are [`stats.sh`](stats.sh) (`seqkit stats -T -a -j 2` in the recipe's pinned image,
picked by `uname -m`) and [`cohort.py`](cohort.py), both as Snakemake `script:`s, so the executor
deploys them with the Snakefile. `direct` is one spawn task, [`direct.sh`](direct.sh). It runs the
same seqkit command on the same eleven files in the same image on the same instance size, written
by hand from the rule, then runs `cohort.py`, and compares every output with the workflow's.

## Results

The same eleven files, the recipe's seqkit pins, and two threads on a `large` of each chip for every
`stats` job. The cold run (`r1`) is twelve instances:

| chip | instances | workflow wall | seqkit (Σ 11 jobs) | billed (Σ 12 windows) | peak RSS | $/hr (`stats`) | compute $/result | **billed $/result** |
|---|---|---|---|---|---|---|---|---|
| **Graviton4** | 11 × `c8g.large` + `c8g.medium` | 5,881 s | 326 s | 2,134 s | 25 MiB | 0.0798 | **0.0072** | **0.0460** |
| Xeon 6975P-C | 12 × `c8i.large` | 3,828 s | 362 s | 2,216 s | 25 MiB | 0.0937 | 0.0094 | 0.0577 |
| EPYC 9R45 | 11 × `c8a.large` + `c8a.medium` | 5,009 s | **239 s** | **1,654 s** | 25 MiB | 0.1078 | **0.0072** | 0.0478 |

A *result* is the cohort table: eleven files checked and joined. *seqkit* is the `docker run`
around seqkit, summed over the eleven jobs. *Billed* sums each instance's window from CloudTrail,
from its `RunInstances` to its own `TerminateInstances`. *$/hr* is spawn's price for the instance,
the `spawn:price-per-hour` tag it set at launch. The join ran on a `medium` where the family has one
(0.0399 / 0.0539 $/hr), and on `c8i.large` otherwise. *Peak RSS* is seqkit's `VmHWM`, the kernel's
own high-water mark, read every 0.2 s until it exits. Threads per core: 1 on Graviton4 and AMD, 2
on Intel. seqkit is Go and links no OpenBLAS. Per-job lines are in
[`results/summary.txt`](results/summary.txt).

- **AMD runs seqkit fastest.** It is 1.36× faster than Graviton4, and Intel is 0.90×. At AMD's 35%
  dearer hour, compute per result comes out equal to Graviton4. Graviton4 is the cheapest billed:
  4% under AMD and 20% under Intel.
- **seqkit is 14–16% of the bill.** A `stats` job's window was 101–232 s, and its fixed part looked
  the same on every chip. Medians on Graviton4: 4 s to the kernel; 31 s to the executor's Python
  3.11 venv (`dnf`); 29 s for `pip install snakemake` plus stage-in of the FASTQ; 27 s to install
  Docker; 3 s to pull. After the script came 56 s of output upload, the completion record and
  the wait to terminate. Windows fall in about 60 s steps (101, 161, 167, 227 s), which fits a
  periodic terminate check. AMD's fixed part is the shortest: 21 s Docker, 22 s venv.
- **The wall is dispatch, not compute.** It took 3,828–5,881 s for 4–6 minutes of seqkit. The
  plugin calls `spawn task run` inside `run_job`, so Snakemake submits one job at a time. Each call
  spent 142–763 s before EC2's `RunInstances`, while spawn sized and priced the launch against the
  AWS Price List. That adds up to 2,227 / 3,749 / 4,257 s across the cohort (Intel / AMD /
  Graviton4), 58–75% of the wall. The time is unbilled, and it grew with other launches on the
  account at the same time. A dry run alone took 65–118 s.
- **One box looping is 3.1–3.6× cheaper at this job size.** `direct.sh` did the same eleven
  files on a single `large` in 574 / 636 / 507 s (Graviton4 / Intel / AMD), for $0.0127 / $0.0166
  / $0.0152. One file per instance pays only when a job's compute is large next to its roughly
  2.5-minute fixed window, and seqkit stats takes 10–55 s per file. Here the workflow earns its
  place through its DAG, not its throughput.
- **Size by the platform, not the tool.** seqkit peaked at 22–25 MiB, and the job directory used
  3.4–6.2 GiB of the 20 GiB root (the storage plugin stages each input to `/var/tmp`). The `large`
  is for seqkit's two threads, not its memory. Keep `mem_mb` set on a rule that installs Docker:
  a `threads: 1` rule without it was sized onto a 0.5 GiB `t4g.nano`, which wedged in the install
  ([#18](https://github.com/spore-host/snakemake-executor-plugin-spawn/issues/18)). Sizing came from
  a one-file probe of the largest file (`results/arm64/probe.*`).
- n = 1 per cell. seqkit is deterministic here: the re-runs, the direct run and all three chips give
  the same bytes, so a repeat would re-measure only the timings.

## The identities, at scale, on all three chips

| claim | why it must hold | observed (every chip) |
|---|---|---|
| each staged FASTQ's sha256 == its pin | the job reads the cohort's bytes, not a re-download | 11 of 11 |
| **each file's `num_seqs` / `sum_len` == the published reads / bases** | ENA `read_count`/`base_count`; the 1000 Genomes `.filt` count | **11 of 11 exact** |
| each pair's mates hold the same reads | paired runs | 4 of 4 |
| **fan-out total == published total** | a lost, duplicated or mis-joined shard cannot reproduce it | **445,999,501 reads / 32,158,518,768 bases** |
| the cold run ran exactly 11 `stats` + 1 `cohort` | job count from the wildcard | 12 jobs, exit 0 |
| **every output == the same commands without Snakemake** | `direct.sh`, written from the rule | **11 of 11 stats rows + the cohort table, byte-identical** |
| **re-run, nothing changed** | nothing to do | **0 jobs, 0 instances, 0 objects rewritten**, 4–5 s |
| **one file's truth re-uploaded** (same bytes, new S3 mtime) | exactly that file's `stats` and the join | **2 jobs; exactly their 4 objects rewritten, the other 10 files' untouched** |
| **the cohort truth re-uploaded** | the join only | **1 job, 2 objects** |
| every re-created output == the cold run's | the re-runs recompute, not just re-stamp | 12 of 12, byte-identical |

`check.py` reads which jobs ran from Snakemake's own log. It reads which objects changed from the
S3 listings taken before and after each phase (key, mtime, ETag), so "exactly the dependents" is
checked in the bucket, not taken from the plan. [`results/check.txt`](results/check.txt): 0 fails.

Observed, not asserted. The first two are Snakemake's own semantics and the third is the
plugin's. Each can catch out a reader:
- **A rule script's mtime is a rerun trigger.** A fresh copy of `stats.sh`, newer than the outputs,
  plans all eleven `stats` jobs again ("Code has changed since last execution",
  `results/arm64/recopied.plan.txt`). A `git checkout` does the same. `run.sh` copies with `cp -p`.
- **Interrupting Snakemake deletes the running job's outputs from S3, and a plain re-run does not
  bring them back.** With the join's output still up to date, the next run reported "Nothing to be
  done" while one `stats/*.tsv` was missing. Naming the file as a target re-created it (1 job). The
  next run then re-ran exactly the join, and both outputs were byte-identical to the cold run's
  (`results/arm64/recover.*`, `again.*`). The interrupted job's instance was terminated by
  Snakemake's cancel 104 s after launch, not left to its TTL.
- **`--jobs 4`, once, on Graviton4** (`results/arm64/jobs4.*`): 2,595 s against 5,881 s for the
  same billed $0.046, and all 12 outputs were byte-identical to the cold run's. Completions, though, were seen in batches, up to
  701 s after a job's script had ended. Every job of `stats` polls the one `smk-stats-1` record,
  and each new launch clears it, so a job is seen as done only once the most recently launched one
  writes the record. Here launch order matched finish order. A short job launched after a long one
  would report the long one done while it still ran. That is #19's failure, and this run did not
  exercise it.

## Across chips

All twelve science outputs of the cold run are **byte-identical** on Graviton4, Intel and AMD:
every column of seqkit's `-a` table, including the float `AvgQual`, `Q20(%)`, `Q30(%)` and `GC(%)`,
and the cohort table. The arm64 image is aarch.bio's build and the x86 one bioconda's, both seqkit
2.13.0. The integer counts are a fixed point, and the float columns, printed to two decimals, agree
too. Nothing here is a path.

## Caveats

- One cohort, one tool per job, one engine (Snakemake 9.27.0 with plugin v0.4.0). The DAG is
  shallow: the change-propagation checks cover one fan-out level and one join.
- `--jobs 1` serialises the workflow. Concurrent jobs of one rule share a spawn `task_id`
  ([#19](https://github.com/spore-host/snakemake-executor-plugin-spawn/issues/19)). Even with that
  fixed, the plugin's synchronous `spawn task run` would still submit one job at a time.
- The dispatch latency was measured while other spawn launches ran on the same account, so it is
  reported separately from the billed window and is not a property of the chip.
- A billed window is CloudTrail's API-call times, not an EC2 billing record.
- Spend for this write-up: **$0.29**, as billed windows at spawn's prices. That covers the probe
  ($0.005), the Graviton4 runs ($0.057, plus $0.002 for the interrupted job), the Intel runs
  ($0.068), the AMD runs ($0.056), four direct runs ($0.057; Graviton4's twice) and the `--jobs 4`
  observation ($0.046).

## Files

`Snakefile`, `stats.sh`, `cohort.py` (the workflow); `stage-inputs.sh`; `run.sh` (one phase on one
chip); `direct.sh` + `direct.task.json` + `mkspec.py` (the run without Snakemake; `mkspec.py`
resolves it for x86 and AMD from `spec_arch`'s tables, since it is a host task with no
`container`); `collect.py` (timings and cost from the job logs and CloudTrail); `check.py`.
Per chip, `results/<chip>/` has each phase's plan, Snakemake log, S3 listing, wall time and copied
outputs (job logs included), the CloudTrail windows, and `direct/`.
