# BWA → samtools

Align 400,000 Illumina read pairs from a 1000 Genomes sample against GRCh38
chr20, sort to BAM, index it, and prove the BAM is real. Two `spawn task run`
tasks on Graviton4, both self-terminating.

**Catalog row:** BWA / samtools / bcftools · **Shape:** D · **Round:** One

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
arm64 before any launch. The bands are set wide enough around those values to
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

## Two things about the container path that this recipe works around

Both are `spawn task run` behaviours worth knowing before writing the next
container recipe. The first is filed as spore-host/spawn#555; the disk limit
that forced chr20 is spore-host/spawn#556.

- **Every path is directly in `/tmp`, deliberately.** The generated wrapper
  bind-mounts the parent directory of each staged path and creates it as the
  instance user (uid 1000), but `docker run` is issued with no `--user`, so an
  aarchbio image runs as `mambauser` — **uid 57439**. A 0755 directory owned by
  uid 1000 is not writable by uid 57439, so a command that writes its output into
  a staged directory gets `EACCES`. Host `/tmp` is 1777, so it is the one location
  that works regardless of the image's user. This affects every bioconda-derived
  image, not just these two.
- **One image per task, so this is two tasks.** `spec.container` takes a single
  image and aarchbio ships no combined bwa+samtools image, so the canonical
  `bwa mem | samtools sort` pipe is not available. The SAM travels between tasks
  through S3 instead. That is honest but it is two boots for what should be one;
  a mulled bwa+samtools arm64 image would collapse this recipe to a single task.

---

## Outputs

Under `s3://scicookbook-942542972736-us-east-1/runs/bwa-samtools/r1/`:
`HG00096.chr20.bam`, `HG00096.chr20.bam.bai`, `flagstat.txt`, `idxstats.txt`,
`smoke-check.txt`, plus `aln.sam` from task 1 and each task's `completion.json`
and `command.log` under `s3://spawn-results-942542972736-us-east-1/tasks/<task_id>/`.
