# BWA → samtools

Align 400,000 Illumina read pairs from a 1000 Genomes sample against GRCh38
chr20, sort to BAM, index it, and prove the BAM is real. Two `spawn task run`
tasks on Graviton4, both self-terminating.

**Catalog row:** BWA / samtools / bcftools · **Shape:** D · **Round:** One

> **Read this before copying the recipe for real work:** the index is chr20 only,
> so **29% of reads map** rather than the ~2% that genuinely belong there, and
> they map with high MAPQ. The BAM is real; the alignments are not correct in the
> way whole-genome alignments are. See [Why chr20](#why-chr20-and-why-derived-copies-exist).

---

## Pins

| Thing | Pin |
|---|---|
| Aligner image | `quay.io/aarchbio/bwa@sha256:19f0eceab80740b821be7ada082d4434acf778912aac658dd1b4c6692dd2e9ba` (tag `0.7.19--h0cbc5ad_1`) |
| Sort/index image | `quay.io/aarchbio/samtools@sha256:1191739637fb6f46ef97c02b28f693b25ca3ca61f90e1337f349b7b7cc0be4f7` (tag `1.24--h391949c_0`) |
| Reference | GRCh38 chr20, `sha256:61eba5b0…35ff3` — bytes 2751788762–2817153559 of `s3://1000genomes/technical/reference/GRCh38_reference_genome/GRCh38_full_analysis_set_plus_decoy_hla.fa` |
| Reads, mate 1 | `sha256:4bd24cdf…536a00` — first 1,600,000 lines of `s3://1000genomes/phase3/data/HG00096/sequence_read/SRR062634_1.filt.fastq.gz` |
| Reads, mate 2 | `sha256:ebd1ad56…d82a04` — first 1,600,000 lines of `…/SRR062634_2.filt.fastq.gz` |

Both images are cosign keyless-verified against
`github.com/playgroundlogic/aarchbio` and their manifest lists contain **only**
`linux/arm64` — there is no amd64 child to accidentally fall back to.

**Data tier: RODA.** Every byte traces to `s3://1000genomes`. `stage-inputs.sh`
materialises the three derived objects and prints the sha256 sums above; the
align task re-checks them on the box before it will run, so a silently changed
input fails the task rather than producing a quiet wrong answer.

---

## Why chr20, and why derived copies exist

Two constraints, both discovered by reading the tooling rather than guessing:

1. **The task path gets an 8 GiB root disk, not 20.** `spawn task run` never sets
   a root volume size, so the instance inherits the AL2023 AMI default of 8 GiB
   (`spawn launch --volume-size` documents "0 = use AMI default"), and `TaskSpec`
   has no field to raise it. RODA ships a *prebuilt* whole-genome BWA index, which
   would have skipped the index step — but it is 5.63 GB, and with ~6.1 GB free
   after the OS there is no room for it plus reads plus output. chr20 is 62 MB and
   indexes on the box in well under a minute.
2. **Input manifests stage whole S3 objects.** There is no byte-range or
   subsample option, so a 400,000-read slice of a 1.9 GB fastq has to be
   materialised into our own bucket first. That is what `stage-inputs.sh` does,
   from fixed offsets, so the derived objects are reproducible rather than
   arbitrary.

Aligning whole-genome reads to a chr20-only reference has a consequence worth
stating rather than hiding: **29% of reads map, not the ~2% chr20's share of the
genome would suggest.** With no competing loci in the index, reads from
paralogous and repetitive regions elsewhere in the genome find a home on chr20 —
and they get high MAPQ too, because MAPQ is computed against what is in the
index. So 7.6% of reads clear MAPQ 30 even though only ~2% genuinely belong here.
That is fine for a smoke check, which asks *is this BAM real and the right
shape*, and it would be wrong for anything that needed correct alignments. Round
Two's job, on a bigger disk, is the whole-genome index.

---

## Run it

```bash
./stage-inputs.sh                                    # once; ~165 MB of range-gets
spawn task run --spec 01-align.task.json --wait      # c8g.2xlarge, TTL 30m
spawn task run --spec 02-sort-and-check.task.json --wait   # c8g.large, TTL 30m
```

`--wait` blocks on the durable completion record and exits with the task's exit
code. Without it, poll `spawn task status cookbook-bwa-align-r1 --region us-east-1`.

Both tasks are `on_complete: terminate`. Nothing is left running.

---

## Sizing and ceiling

| Task | Instance | Rate | Expected | TTL cap |
|---|---|---|---|---|
| `01-align` | c8g.2xlarge (8 vCPU / 16 GiB) | $0.3190/hr | ~4 min ≈ $0.021 | $0.16 |
| `02-sort-and-check` | c8g.large (2 vCPU / 4 GiB) | $0.0798/hr | ~3 min ≈ $0.004 | $0.04 |

**Actual, first run:** task 1 billed 3m51s = $0.0205 (`bwa index` 46.6s, `bwa mem
-t 8` 32.0s wall / 247.0s CPU); task 2 billed 2m49s = $0.0037. **$0.024 total**,
against an expected $0.03. Both boxes self-terminated.

**These timings are not compute cost.** 78 seconds of actual work sits inside 6m40s
of billed time — boot, Docker install and image pull are the majority of both
tasks, and that ratio gets worse with every task a recipe adds. Fine here, and
irrelevant to Round One, which builds working examples rather than benchmarks. But
don't quote these numbers as what BWA costs.

**Rerunning one task.** Each task reads all of its inputs from S3 and writes its
outputs to S3, so if task 2 fails you rerun task 2 alone — task 1's `aln.sam` is
already in the bucket and task 2 reads it from there. Nothing in either task
depends on the other's local disk. One caveat: `task_id` is fixed in the spec, so a
rerun writes over the previous `completion.json` and `command.log` under
`s3://spawn-results-…/tasks/<task_id>/`. Bump the `task_id` suffix if you want to
keep both records.

**Expected ≈ $0.03; absolute worst case $0.20** if both tasks hang until TTL.
`spawn task run` exposes no `--cost-limit`, so the TTL *is* the cost cap here —
30 minutes × the on-demand rate. That bound is why the TTL is 30m and not 4h.

Peak disk, task 1: 62 MB reference + 231 MB index + 61 MB reads + ~200 MB SAM
≈ 0.6 GB, against ~6.1 GB free. Comfortable.

---

## Smoke check

Runs inside task 2, so the task itself fails if the BAM is not real. Seven
assertions, all in `02-sort-and-check.task.json`:

| Check | Threshold | Observed | What it catches |
|---|---|---|---|
| `samtools quickcheck -v` | clean | clean | truncated / corrupt BGZF |
| `@SQ` lines in header | exactly 1 | 1 | wrong or merged reference |
| `SN:chr20 LN:64444167` | present | present | reference is not the chromosome we pinned |
| primary records (`-F 0x900`) | exactly **800000** | 800000 | reads lost or duplicated — 400,000 pairs in, 800,000 primary records out |
| mapped primary (`-F 0x904`) | 150000–350000 | 233036 | aligner ran but aligned nothing / mapped everything |
| MAPQ ≥ 30 primary (`-q 30`) | 20000–150000 | 61160 | all alignments are low-confidence noise |
| properly paired (`-f 0x2`) | > 50000 | 132080 | mates handled as singles |

The `Observed` column comes from running both commands in the pinned images on
arm64 before any launch, and the run on Graviton4 reproduced **every one of those
numbers exactly** — 808,505 records, 800,000 primary, 233,036 primary mapped
(29.13%), 61,160 at MAPQ ≥ 30, 132,080 properly paired. The bands are set wide enough around those values to
survive aligner nondeterminism and tight enough that an empty, unmapped,
truncated or wrong-reference BAM fails. The exact `800000` is the strongest of
them: it is a conservation check, not a threshold.

Plus `flagstat.txt`, `idxstats.txt` and `smoke-check.txt` are staged back, so the
numbers are inspectable after the box is gone.

The command is stored escaped inside the JSON, which is where it is authoritative.
To read it:

```bash
python3 -c 'import json,sys;print(json.load(open(sys.argv[1]))["command"][2])' 02-sort-and-check.task.json
```

---

## Three things about the container path, worth knowing before the next recipe

All three are `spawn task run` behaviours. Filed as spore-host/spawn#555 (the
container can't write staged dirs, confirmed), spore-host/spawn#561 (a lost output
is still reported as success), and spore-host/spawn#556 (the 8 GiB disk that forced
chr20, plus other silent narrowings). The third bullet is not a bug at all.

- **Every path is directly in `/tmp`, deliberately.** The generated wrapper
  bind-mounts the parent directory of each staged path and creates it as the
  instance user (uid 1000), but `docker run` is issued with no `--user`, so an
  aarchbio image runs as `mambauser` — **uid 57439**. A 0755 directory owned by
  uid 1000 is not writable by uid 57439, so a command that writes its output into
  a staged directory should get `EACCES`. Host `/tmp` is 1777, so it is the one
  location that works regardless of the image's user. This affects every
  bioconda-derived image, not just these two.

  **Status: confirmed by a deliberate probe task, not inferred.** `uid=57439`,
  a staged `/tmp/pw/in` owned `1000:1000` mode `0755` → `Permission denied`,
  `/tmp` (mode `1777`) → OK, in the same run. The flat-`/tmp` layout is
  load-bearing. Two extra wrinkles the probe found: parents of *output* sources are
  never created by the wrapper, so Docker creates them as **root** — even less
  writable — and any destination outside `/tmp` fails at `mkdir` before the uid
  matters at all, because stage-in runs unprivileged and cannot create a directory
  at the filesystem root. The `/data` + `/work` layout in spawn's own
  `examples/task-spec.json` cannot work on this path.

- **spawn's exit code does not prove the outputs exist.** A task whose declared
  output fails to stage is still recorded `state: completed, exit_code: 0`
  (spore-host/spawn#561 — the wrapper computes the stage-out result and never reads
  it). This is why the smoke check runs *inside* task 2 rather than after it: there
  it can fail the task. Confirm the five objects are in the bucket regardless.
- **One image per task, so this is two tasks — by design, not by accident.**
  `spec.container` takes a single image, and aarch.* ships one tool per image
  deliberately: every image traces to a single signed conda recipe, and mulled
  multi-tool images would mean resolving a joint environment, which breaks that
  provenance. So the canonical `bwa mem | samtools sort` pipe is not available
  here and will not become available. The SAM round-trips through S3 instead, and
  the second boot is the price of a pinned, single-recipe image. Don't read this
  as a gap waiting to be filled.

---

## Outputs

Under `s3://scicookbook-942542972736-us-east-1/runs/bwa-samtools/r1/`:
`HG00096.chr20.bam`, `HG00096.chr20.bam.bai`, `flagstat.txt`, `idxstats.txt`,
`smoke-check.txt`, plus `aln.sam` from task 1 and each task's `completion.json`
and `command.log` under `s3://spawn-results-942542972736-us-east-1/tasks/<task_id>/`.
