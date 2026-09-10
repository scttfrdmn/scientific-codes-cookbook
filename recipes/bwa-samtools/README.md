---
tool: bwa
tool_version: 0.7.19
image: quay.io/aarchbio/bwa@sha256:19f0eceab80740b821be7ada082d4434acf778912aac658dd1b4c6692dd2e9ba
spawn_version: 0.104.0
---
# BWA — align paired reads to a reference

Align paired-end reads to a reference genome and get back a sorted, indexed BAM.

## Run it

```bash
bwa index ref.fa
bwa mem -t 8 -R "@RG\tID:run1\tSM:mysample\tPL:ILLUMINA\tLB:lib1" \
  ref.fa reads_1.fq.gz reads_2.fq.gz > aln.sam
samtools sort -@ 2 -o mysample.bam aln.sam
samtools index mysample.bam
```

Two tasks — `bwa` in one image, `samtools` in the next, the SAM handed between them through S3 — because aarch.bio ships [one tool per image](../../practices/container-path.md), so the pipe becomes a chain and each task reruns independently.

## Make it yours

| In the recipe | Swap for | What to know |
|---|---|---|
| `chr20.fa` — GRCh38 **chr20 only** | your whole reference genome | **chr20 is not a genome.** Against a chr20-only index 29% of reads "map" at high MAPQ (vs ~2% that belong) — reads from elsewhere have nowhere else to go. Fine to prove a BAM is real; wrong for real alignment. The fixture's one load-bearing limit. |
| the 400,000-read subsample | your reads | subsampled for a fast demo, not because BWA wants small input. |
| `-R "@RG\t…SM:HG00096…"` | your sample's read group | **real, not scaffolding** — set `SM`/`LB`/`ID` so downstream dedup and variant-calling can tell samples apart. |

`bwa mem -t 8` is **not** determinism scaffolding — BWA's alignment doesn't depend on thread count, so scale `-t` to your cores freely. (Contrast an [assembler](../flye/README.md), where `-t 1` *is* scaffolding and must change.)

**Scale it:** chr20's 29% is the one number that misrepresents the tool — the whole GRCh38 index maps **99.76%** (measured, below). But the 8.9 GB index makes *staging* the constraint, so size by RAM on an `r8g` and share it across a cohort ([copy, mount, or share?](../../patterns/data-movement.md)).

## Shape, size, cost

One alignment is one task; a cohort is the same task [fanned out](../../patterns/job-arrays.md). `c8g.2xlarge` to align (`-t 8` ran 32 s wall / 247 s CPU, ~7.7×), `c8g.large` to sort. First run **$0.024**, both boxes self-terminated — but 78 s of work sat inside 6m40s billed, so read that as "a short task is mostly overhead," [not what BWA costs](../../practices/what-this-does-not-cover.md).

<details id="as-shipped">
<summary>As shipped: exact commands, pins, why chr20, the whole-genome measurement, smoke check</summary>

### Run the shipped recipe
```bash
./stage-inputs.sh                                        # once; ~165 MB of range-gets from s3://1000genomes
spawn task run --spec 01-align.task.json --wait          # c8g.2xlarge, TTL 30m
spawn task run --spec 02-sort-and-check.task.json --wait # c8g.large,   TTL 30m
```
Both `on_complete: terminate`. Each task reads inputs from and writes outputs to S3, so a failed task 2 reruns alone. `spawn task run` exposes no `--cost-limit`, so **TTL is the cost cap** (30m × on-demand ≈ $0.20 worst case).

### Pins (data tier: RODA — every byte traces to `s3://1000genomes`)
| thing | pin |
|---|---|
| bwa / samtools images | `bwa@sha256:19f0eceab8…` (`0.7.19`) / `samtools@sha256:1191739637…` (`1.24`) |
| reference | GRCh38 chr20, `sha256:61eba5b0…` (byte range of the 1000G analysis-set fasta) |
| reads 1 / 2 | `sha256:4bd24cdf…` / `ebd1ad56…` — first 1.6M lines of HG00096 `SRR062634` |

Both images are cosign-verified and `linux/arm64`-only. `stage-inputs.sh` pre-materialises the three derived objects and the align task re-checks their sha256 on the box.

### Why chr20, and the whole-genome number it stands in for
The task path gets an 8 GiB root disk (~6.1 GB free) — no room for RODA's 5.63 GB whole-genome index. chr20 (62 MB) indexes in under a minute; against it, paralogous/repetitive reads from elsewhere land on chr20, which is why 29% map. The honest rate: 10M HG00096 pairs against the whole GRCh38 index map **99.76%**, `bwa mem` dominating ~8.4:1 compute-to-overhead. Two constraints the fixture hides — staging is a **tmpfs ≈ ½ RAM, not `disk_gib`** (the 8.9 GB index overran a 16 GiB box's `/tmp`; an `r8g.2xlarge` held it), and a cohort should **share one read-only index** rather than re-stage 8.9 GB per sample ([data movement](../../patterns/data-movement.md)).

### Smoke check (inside task 2 — fails the task if the BAM isn't real)
| check | threshold | observed | catches |
|---|---|---|---|
| `samtools quickcheck -v` | clean | clean | truncated / corrupt BGZF |
| `@SQ` lines / `SN:chr20` | exactly 1 / present | 1 / present | wrong or merged reference |
| primary records (`-F 0x900`) | exactly **800000** | 800000 | reads lost/duplicated (conservation) |
| mapped primary (`-F 0x904`) | 150000–350000 | 233036 | aligned nothing / everything |
| MAPQ ≥ 30 (`-q 30`) | 20000–150000 | 61160 | all low-confidence noise |
| properly paired (`-f 0x2`) | > 50000 | 132080 | mates handled as singles |

`flagstat.txt`, `idxstats.txt`, `smoke-check.txt` stage back for inspection. The three container-path behaviours this leans on (flat `/tmp`, exit-code-isn't-proof, one-tool-per-image) live in [the container path](../../practices/container-path.md). Outputs under `runs/bwa-samtools/r1/`; re-running bumps the `-r1` suffix.

</details>
