# BWA — align paired reads to a reference

Align paired-end reads to a reference genome and get back a sorted, indexed BAM. If you use
BWA you already know this — so here's the invocation and the two numbers that matter, not a
lecture.

## Run it

`bwa mem` aligns; `samtools sort` gives you the indexed BAM:

```bash
bwa index ref.fa
bwa mem -t 8 -R "@RG\tID:run1\tSM:mysample\tPL:ILLUMINA\tLB:lib1" \
  ref.fa reads_1.fq.gz reads_2.fq.gz > aln.sam

samtools sort -@ 2 -o mysample.bam aln.sam
samtools index mysample.bam
```

That's the whole thing. On spore.host it runs as two `spawn task run` tasks — bwa in one
image, samtools in the next, the SAM handed between them through S3. aarch.bio ships
[one tool per image](../../practices/container-path.md), so this is a chain, not a pipe to
reassemble — and each task reruns independently as a result. Exact shipped commands are in
[the details below](#as-shipped).

## Make it yours

The recipe aligns a fixed fixture so the output can be checked. **Three things to change for
real work** — and one that's fine to leave:

| In the recipe | Swap for | What to know |
|---|---|---|
| `chr20.fa` — GRCh38 **chr20 only** | your whole reference genome | **chr20 is not a genome.** With a chr20-only index, 29% of reads "map" (vs the ~2% that belong) at high MAPQ — reads from elsewhere have nowhere else to go. Fine for proving a BAM is real; wrong for real alignment. This is the fixture's one load-bearing limit. |
| the 400,000-read subsample | your reads | The subsample is there to make the demo fast and cheap, not because BWA wants small input. |
| `-R "@RG\t…SM:HG00096…"` | your sample's read group | **Real, not scaffolding** — set `SM`/`LB`/`ID` so downstream dedup and variant-calling can tell samples apart. |

`bwa mem -t 8` is **not** determinism scaffolding: scale `-t` to your instance's cores freely,
BWA's alignment doesn't depend on thread count. (Contrast an assembler, where `-t 1` *is*
scaffolding and *must* change for real runs — that distinction is [pin threads for stochastic
search](../../practices/pin-threads.md).)

**As the input grows:** a whole-genome BWA index is ~5.6 GB and won't fit the task path's 8 GiB
root disk alongside reads and output — so a real run builds the index on a larger disk or
attaches a prebuilt one. That's the single real constraint the chr20 fixture sidesteps.

## Shape, size, cost

- **Shape:** one alignment is one task. A whole cohort is the *same task, fanned out* →
  [Job arrays](../../patterns/job-arrays.md). Size one sample; run N.
- **Sized:** `c8g.2xlarge` (8 vCPU / 16 GiB) to align, `c8g.large` to sort. `bwa mem -t 8` ran
  32 s wall against 247 s CPU here — ~7.7× on 8 threads, so 8 cores is a sensible per-sample
  size. Find your own knee with an afternoon's sweep → [Sizing](../../patterns/sizing.md).
- **Cost & time:** first run **$0.024 total**, both boxes self-terminated. But **78 s of actual
  work sat inside 6m40s of billed time** — boot, Docker install, and image pull dominate a short
  run. Don't read $0.024 as "what BWA costs"; read it as "a short task is mostly overhead" —
  which is exactly the waste [job arrays](../../patterns/job-arrays.md) amortize across a cohort.

**Safe to try:** capped by TTL, self-terminating — a wrong guess costs cents.

## Proof it works

The recipe checks its own output, so the task fails if the BAM isn't real — that's what makes
it a *recipe* and not a snippet. It's proof, not the point of the page. Task 2 asserts, among
seven checks: exactly **800,000** primary records (400k pairs in → 800k out, a conservation
check, not a threshold), 233,036 mapped, 61,160 at MAPQ ≥ 30, > 50,000 properly paired — and
Graviton4 reproduced every number exactly.

<details id="as-shipped">
<summary>As shipped: exact commands, pins, the chr20 fixture, smoke-check table, container-path notes</summary>

### Run the shipped recipe

```bash
./stage-inputs.sh                                        # once; ~165 MB of range-gets from s3://1000genomes
spawn task run --spec 01-align.task.json --wait          # c8g.2xlarge, TTL 30m
spawn task run --spec 02-sort-and-check.task.json --wait # c8g.large,   TTL 30m
```

Both are `on_complete: terminate`. `--wait` blocks on the durable completion record and exits
with the task's code. Each task reads its inputs from S3 and writes outputs to S3, so a failed
task 2 reruns alone (task 1's `aln.sam` is already in the bucket). `spawn task run` exposes no
`--cost-limit`, so the **TTL is the cost cap** — 30 min × the on-demand rate; that bound is why
TTL is 30m, not 4h. Worst case if both hang to TTL: $0.20.

### Pins (data tier: RODA — every byte traces to `s3://1000genomes`)

| Thing | Pin |
|---|---|
| bwa image | `quay.io/aarchbio/bwa@sha256:19f0eceab8…` (`0.7.19--h0cbc5ad_1`) |
| samtools image | `quay.io/aarchbio/samtools@sha256:1191739637…` (`1.24--h391949c_0`) |
| reference | GRCh38 chr20, `sha256:61eba5b0…` — byte range of the 1000G GRCh38 analysis-set fasta |
| reads 1 / 2 | `sha256:4bd24cdf…` / `sha256:ebd1ad56…` — first 1.6M lines of HG00096 `SRR062634` |

Both images are cosign keyless-verified against `github.com/playgroundlogic/aarchbio`, and their
manifest lists contain **only** `linux/arm64` — no amd64 child to fall back to. `stage-inputs.sh`
materialises the three derived objects (input manifests stage whole S3 objects, so a subsample
must be pre-materialised) and the align task re-checks their sha256 on the box before running.

### Why chr20 (the mechanics behind the fixture caveat)

The task path gets an **8 GiB root disk** (`spawn task run` inherits the AMI default and
`TaskSpec` has no field to raise it), leaving ~6.1 GB free — no room for RODA's 5.63 GB
prebuilt whole-genome index plus reads plus output. chr20 is 62 MB and indexes on the box in
under a minute. Aligning whole-genome reads to a chr20-only index is why 29% map at high MAPQ:
with no competing loci, paralogous/repetitive reads from elsewhere land on chr20. Correct for a
smoke check ("is this BAM real and the right shape"); wrong for real alignment. The whole-genome
index on a bigger disk is a Round-Two job.

### Smoke check (inside task 2 — fails the task if the BAM isn't real)

| Check | Threshold | Observed | Catches |
|---|---|---|---|
| `samtools quickcheck -v` | clean | clean | truncated / corrupt BGZF |
| `@SQ` lines | exactly 1 | 1 | wrong or merged reference |
| `SN:chr20 LN:64444167` | present | present | not the pinned chromosome |
| primary records (`-F 0x900`) | exactly **800000** | 800000 | reads lost/duplicated (conservation) |
| mapped primary (`-F 0x904`) | 150000–350000 | 233036 | aligned nothing / everything |
| MAPQ ≥ 30 (`-q 30`) | 20000–150000 | 61160 | all alignments low-confidence noise |
| properly paired (`-f 0x2`) | > 50000 | 132080 | mates handled as singles |

`flagstat.txt`, `idxstats.txt`, `smoke-check.txt` are staged back for inspection after the box
is gone. Read the authoritative command with
`python3 -c 'import json;print(json.load(open("02-sort-and-check.task.json"))["command"][2])'`.

### Container-path behaviours

The three things this recipe relies on — flat `/tmp` staging, the exit code not proving the
output is real, and one-tool-per-image (why it's two tasks) — aren't BWA-specific; they're true
of every recipe. They live in **[The container path](../../practices/container-path.md)**.

### Outputs

Under `s3://scicookbook-942542972736-us-east-1/runs/bwa-samtools/r1/`: `HG00096.chr20.bam`(+`.bai`),
`flagstat.txt`, `idxstats.txt`, `smoke-check.txt`, and task 1's `aln.sam`; completion records under
`s3://spawn-results-…/tasks/<task_id>/`.

</details>
