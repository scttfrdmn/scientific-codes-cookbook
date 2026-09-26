# One 28.6 GiB index, four data paths — and the local disk is the slowest

> **Reading the index straight from S3 was 8× faster than reading it from the local disk we
> copied it onto, and 6× faster than FSx for Lustre.** Same box, same index, same 15.8M reads,
> same answer every time. The reflex — stage it local, it'll be fast once it's there — is
> backwards at this size, and FSx costs $174/month minimum to be second-slowest.

STAR is the right workload to ask this on: the index is **28.6 GiB** and immutable, the
alignment is **under a minute of real work**, so the data path is most of the bill. Every route
below ran on one `m8g.8xlarge` (32 vCPU, 124 GiB) against the same published GRCh38 + Ensembl
116 index and the complete `ERR188026` run.

## The result

Genome load is the data path's share of the run; STAR logs it itself, so it's separable from
mapping. **First read** is what a fan-out actually pays — every new box is cold.

| route | setup | bytes moved up front | **first load** | effective | second load |
|---|---|---|---|---|---|
| **lith mount** (S3 in place) | **0 s** | **1,080 B** | **28 s** | **1046 MiB/s** | 15 s |
| EFS (hydrated from S3) | 86 s | 28.6 GiB | 57 s | 514 MiB/s | 57 s |
| FSx Lustre (S3-linked) | ~9 min to create | metadata only | **174 s** | 168 MiB/s | 57 s |
| `aws s3 cp` → local EBS | 118 s | 28.6 GiB | **231 s** | 127 MiB/s | 231 s |

Total wall for one alignment, setup included: **lith 89 s · EFS 203 s · FSx 233 s · copy 411 s.**
All four returned `input_reads 15800127` and `92.47%` uniquely mapped, so they did identical
work; mapping was 56–58 s everywhere, because mapping isn't what differs.

## Why the local copy loses

A copy replaces S3's bandwidth with **one volume's** bandwidth. The copy itself streamed at
248 MiB/s, but STAR's load of the copied index ran at **127 MiB/s** — an mmap'd index is not a
sequential read, and a single gp3 volume is where that shows. S3 answers the same access pattern
across many parallel connections at ~1 GiB/s. So the copy pays twice: 118 s to move the bytes,
then a *slower* read of them than if it had never moved them.

And it doesn't amortise. The second load was also 231 s, because these runs drop the page cache
between aligns — deliberately, since that models the real case (a cohort fans out onto fresh
boxes, and every one is cold). Where the cache does survive, see the warm pass below.

## Why FSx for Lustre loses, which is the surprising one

FSx's pitch is speed, and its metadata story is genuinely good: the data-repository association
imported all 16 entries so `ls -l` over the whole tree returned in **0 s** without moving a byte
— structurally the same trick as lith's index. Then the first read has to fault 28.6 GiB in from
S3, and that runs at the **throughput tier you bought**: 1200 GB × 125 MB/s/TiB ≈ 150 MB/s, and
we measured 168 MiB/s. Once hydrated it matched EFS exactly (57 s), so FSx's speed is real — it
is just *behind* the hydration, and the hydration is the part a fresh box pays.

The cost makes it worse. **1200 GB is the FSx Lustre minimum** and it is not negotiable, so
holding a 28.6 GiB index means renting 42× the capacity:

| holding this 28.6 GiB index for a month | |
|---|---|
| S3 Standard (what lith reads) | **$0.66** |
| EFS Standard | $8.58 **+ $0.03/GiB every read** |
| FSx Lustre 125 MB/s/TiB | **$174.00** — 1200 GB minimum |

Those are `aws pricing` rates for us-west-2, not estimates. FSx is **264× S3** for the same
bytes, to be 6× slower on the read that matters.

## EFS: fast enough, and the charge is the catch

EFS was the best of the three copy-ish routes — 86 s to hydrate at 340 MiB/s, then a steady
57 s load, cold and second-read alike. But Elastic Throughput bills **$0.06/GiB written and
$0.03/GiB read**, so:

- hydrating the index: 28.6 GiB × $0.06 = **$1.72**, once
- **every alignment that reads it: 28.6 GiB × $0.03 = $0.86**

That align costs **$0.047** of compute (117 s of an m8g.8xlarge). So the data charge is **18×
the compute cost of the work it serves**, on every sample, forever. Bursting mode avoids the
per-GiB charge but scales baseline throughput with *stored* bytes — ~1.4 MB/s for a 28.6 GiB
filesystem — so it is not an option here. There is no cheap EFS configuration for this shape.

## The tmpfs variant — a legitimate pro move with a precondition

Copying into `/tmp` (RAM-backed tmpfs) instead of onto EBS is the fastest copy available: the
same 28.6 GiB landed in **69 s** versus 118 s to disk, and no read after it can be slow because
the "disk" is memory. It also can't be evicted by dropping caches — tmpfs *is* the page cache,
so there is no cold/warm distinction at all.

The precondition is the whole story: the copy occupies RAM the tool also needs. On a
`c8g.8xlarge` (62 GiB) this exact copy held 29 of the 31 GiB tmpfs and the kernel then killed
STAR reaching for its own 32 GiB —

```text
Out of memory: Killed process 34623 (STAR) total-vm:36833440kB, anon-rss:33656832kB
```

— so the pro move needs `copy + working set` of headroom, about 61 GiB for this index, which on
the [task path](../../practices/container-path.md) means ~2× that in RAM because staging `/tmp`
is tmpfs at half of memory. Size for both or don't take the shortcut.

## What to do

- **Immutable reference data larger than a few GiB: mount it.** No setup, nothing to hydrate,
  nothing to clean up, and it was the fastest read here by 2–8×.
- **Don't buy FSx Lustre to make a shared index fast.** For this access pattern it is slower
  than S3 on first read, identical to EFS afterwards, and the 1200 GB floor costs more than
  everything else on this page combined.
- **EFS if the data mutates** — that's what it's for. Price the per-GiB read charge against how
  many samples will read it.
- **Copy to tmpfs, not EBS,** if you are going to copy, and size the box for copy + working set.

Same conclusion as [bwa at 8.9 GiB](../lith-vs-copy/README.md), where copy and mount tied on
wall time. At 28.6 GiB they do not tie, and the gap runs the opposite way from the reflex.

## Run it

```sh
export AWS_PROFILE=aws
B=$COOKBOOK_BUCKET
aws s3 cp four-data-paths.sh "s3://$B/scripts/four-data-paths.sh"

# EFS has to exist first (spawn mounts by ID, it does not create one)
FS=$(aws efs create-file-system --region us-west-2 --throughput-mode elastic \
       --encrypted --query FileSystemId --output text)
# ... one mount target per AZ, security group allowing tcp/2049 ...

spawn launch dpaths --region us-west-2 --instance-type m8g.8xlarge --volume-size 160 \
  --az us-west-2b --efs-id "$FS" \
  --fsx-create --fsx-s3-bucket "$B" --fsx-import-path "s3://$B/inputs/star-index-GRCh38-116" \
  --fsx-lifecycle ephemeral --iam-policy s3:ReadOnly,s3:WriteOnly \
  --ttl 90m --cost-limit 2.50
```

Then `warm-leg.sh` (the same aligns without `drop_caches`) and `tmpfs-leg.sh` (the RAM copy).
The index itself is published once by `build-publish.sh`.

**Clean up, and verify it:** EFS and FSx both bill until deleted, and `--fsx-lifecycle ephemeral`
has been observed not to reap ([spawn#613](https://github.com/spore-host/spawn/issues/613)).

```sh
spawn fsx list --region us-west-2          # must be empty
aws efs delete-mount-target --mount-target-id fsmt-…   # each, then
aws efs delete-file-system --file-system-id "$FS"
```

## Caveats, and one thing that cost an hour

n = 1 per cell; one box, one image digest, one thread count (32), so the routes are comparable
to each other. Mapping was 56–58 s in every route, which is the internal control — the spread is
entirely in the load. Rates are us-west-2 on-demand from `aws pricing` on 2026-09-26.

`--fsx-import-path` **created no S3 link at all**: the filesystem mounted empty because spored's
`CreateDataRepositoryAssociation` fails on a missing `iam:CreateServiceLinkedRole`, logged only
on the box while the CLI printed `Instance Ready`
([spawn#622](https://github.com/spore-host/spawn/issues/622)). The association here was created
by hand. Worth knowing before trusting a `/fsx` mount contains anything.

Two smaller ones, both the same shape as bugs this repo has hit before: the container runs as
`mambauser` (57439) and cannot write a host directory owned by the login user, so STAR's output
dir needs `chmod 777` — and under `set -u`, an SSM-driven script has no `$HOME`, so `W=$HOME/work`
dies on line 3.
