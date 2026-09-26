# One 28.6 GiB index, five data paths — and the local disk is the slowest

> **Reading the index straight from S3 was 8× faster than reading it from the local EBS disk we
> copied it onto, and 6× faster than FSx for Lustre.** Same box, same index, same 15.8M reads,
> same answer every time. The reflex — stage it local, it'll be fast once it's there — is
> backwards for the *first* read, and FSx costs $174/month minimum to come second-last.
>
> It flips once you reuse the box: copy into **tmpfs** and the third sample onward is the
> cheapest path there is. The crossover is three samples, and it is measured below.

STAR is the right workload to ask this on: the index is **28.6 GiB** and immutable, the
alignment is **under a minute of real work**, so the data path is most of the bill. Every route
ran on one `m8g.8xlarge` (32 vCPU, 124 GiB) against the same published GRCh38 + Ensembl 116
index and the complete `ERR188026` run. All of them returned `input_reads 15800127` and
**92.47%** uniquely mapped, and mapping took 56–58 s in every route — that invariance is the
internal control, and it means the whole spread below is data path, not science.

## First read — what a fan-out actually pays

Every box in a cohort is cold, so this is the row that matters for N samples on N boxes.
STAR logs its own genome-load boundary, so the data path's share is separable.

| route | setup | bytes moved up front | **load** | effective | setup + align |
|---|---|---|---|---|---|
| **lith mount** (S3 in place) | **0 s** | **1,080 B** | **28 s** | **1046 MiB/s** | **89 s** |
| copy → tmpfs (RAM) | 58 s | 28.6 GiB | 9 s | — | 126 s |
| EFS (hydrated from S3) | 86 s | 28.6 GiB | 57 s | 514 MiB/s | 203 s |
| FSx Lustre (S3-linked) | ~9 min to create | metadata only | **174 s** | 168 MiB/s | 233 s |
| copy → local EBS | 118 s | 28.6 GiB | **231 s** | 127 MiB/s | **409 s** |

## Steady state — the same box aligning another sample

| route | load | align | notes |
|---|---|---|---|
| copy → local EBS, page-cache warm | **7 s** | **67 s** | fastest, *if* the cache survives |
| copy → tmpfs | 9 s | **68 s** | cannot be evicted — tmpfs *is* the page cache |
| lith mount | 36 s | 98 s | under cache pressure from the other routes |
| EFS | 57 s | 118 s | unchanged cold vs warm |
| FSx Lustre, hydrated | 58 s | 117 s | identical to EFS once the bytes are local |

So there are exactly two regimes, and which one you're in decides everything:

- **One sample per box** (a cohort fanned out): **mount.** 89 s against 126–409 s, no setup,
  nothing to clean up.
- **Many samples on one box:** **copy into tmpfs.** 58 s once, then 68 s each. Against lith's
  ~90 s per sample the break-even is **N = 3** (`58 + 68N` vs `90N`).

Copying to **EBS is dominated on every axis** — 2× slower to stage than tmpfs (118 s vs 58 s)
and 4.3× slower on the first align (291 s vs 68 s), with no compensating advantage. If you are
going to copy, copy into RAM.

## Why the local EBS copy loses

A copy replaces S3's bandwidth with **one volume's** bandwidth. The copy itself streamed at
248 MiB/s, but STAR's load of the copied index ran at **127 MiB/s** — an mmap'd index is random
access, and a single gp3 volume is where that shows. S3 answers the same pattern across many
parallel connections at ~1 GiB/s. So the copy pays twice: 118 s to move the bytes, then a
*slower* read of them than if it had never moved them.

It does amortise, but only while the page cache holds: a second align with caches intact loaded
in 7 s. Drop the cache and it is 231 s again, every time — and a fresh box has no cache at all.

## Why FSx for Lustre loses, which is the surprising one

FSx's pitch is speed, and its metadata story is genuinely good: the data-repository association
imported all 16 entries so `ls -l` over the whole tree returned in **0 s** without moving a byte
— structurally the same trick as lith's 1,080-byte index. Then the first read has to fault
28.6 GiB in from S3, and that runs at the **throughput tier you bought**: 1200 GB ×
125 MB/s/TiB ≈ 150 MB/s, measured at 168 MiB/s. Once hydrated it matched EFS exactly (57–58 s).
FSx's speed is real — it is just *behind* the hydration, and a fresh box always pays that.

The cost makes it worse. **1200 GB is the FSx Lustre minimum** and not negotiable, so holding a
28.6 GiB index means renting 42× the capacity:

| holding this 28.6 GiB index for a month | |
|---|---|
| S3 Standard (what lith reads) | **$0.66** |
| EFS Standard | $8.58 **+ $0.03/GiB every read** |
| FSx Lustre @ 125 MB/s/TiB | **$174.00** — 1200 GB minimum |

Those are `aws pricing` rates for us-west-2, not estimates. FSx is **264× S3** for the same
bytes, to be 6× slower on the read that matters.

## EFS: fast enough, and the charge is the catch

EFS was the best of the copy-ish routes for setup — 86 s to hydrate at 340 MiB/s — then a
stubborn 57 s load, identical cold, warm, and under cache pressure. But Elastic Throughput bills
**$0.06/GiB written and $0.03/GiB read**:

- hydrating the index: 28.6 GiB × $0.06 = **$1.72**, once
- **every alignment that reads it: 28.6 GiB × $0.03 = $0.86**

That align costs **$0.047** of compute (117 s of an m8g.8xlarge). The data charge is **18× the
compute cost of the work it serves**, on every sample, forever. Bursting mode avoids the per-GiB
charge but scales baseline throughput with *stored* bytes — ~1.4 MB/s for a 28.6 GiB filesystem
— so it isn't an option here. There is no cheap EFS configuration for this shape.

## The tmpfs copy is a pro move, with one precondition

Copying into `/tmp` is the fastest copy available: **58 s at 505 MiB/s** versus 118 s to EBS, and
no read after it can be slow because the "disk" is memory (load: 9 s). It also cannot be evicted
by cache pressure — tmpfs *is* the page cache — so unlike the EBS copy its 68 s is dependable
rather than conditional.

The precondition is the whole story: the copy occupies RAM the tool also needs. On the 124 GiB
box, 29 GiB of tmpfs plus STAR's 30 GiB peak sat comfortably in 124 and logged **0 OOM events**.
On a `c8g.8xlarge` (62 GiB) the same copy held 29 of the 31 GiB tmpfs and the kernel killed STAR
reaching for its own 32 GiB:

```text
Out of memory: Killed process 34623 (STAR) total-vm:36833440kB, anon-rss:33656832kB
```

So size for **copy + working set** — about 61 GiB for this index — and remember that on the
[task path](../../practices/container-path.md) staging `/tmp` is tmpfs at *half* of RAM, so that
means ~124 GiB of instance. Size for both or don't take the shortcut.

## What to do

- **Fanning out one sample per box: mount.** Fastest first read by 2–8×, no setup, no cleanup.
- **Reusing one box for 3+ samples: copy into tmpfs**, sized for copy + working set.
- **Don't copy to EBS.** tmpfs beats it on both staging and reading whenever the RAM exists.
- **Don't buy FSx Lustre to make a shared index fast.** Slower than S3 on first read, identical
  to EFS after, and its 1200 GB floor costs more than everything else here combined.
- **EFS when the data mutates** — that's what it's for. Price the per-GiB read charge against
  how many samples will read it.

Same conclusion as [bwa at 8.9 GiB](../lith-vs-copy/README.md), where copy and mount tied on
wall time. At 28.6 GiB they do not tie, and the first-read gap runs opposite to the reflex.

## Run it

```sh
export AWS_PROFILE=aws
B=$COOKBOOK_BUCKET
aws s3 cp four-data-paths.sh "s3://$B/scripts/four-data-paths.sh"

# EFS has to exist first — spawn mounts by ID, it does not create one
FS=$(aws efs create-file-system --region us-west-2 --throughput-mode elastic \
       --encrypted --query FileSystemId --output text)
# ... then one mount target per AZ, in a security group allowing tcp/2049 ...

spawn launch dpaths --region us-west-2 --instance-type m8g.8xlarge --volume-size 160 \
  --az us-west-2b --efs-id "$FS" \
  --fsx-create --fsx-s3-bucket "$B" --fsx-import-path "s3://$B/inputs/star-index-GRCh38-116" \
  --fsx-lifecycle ephemeral --iam-policy s3:ReadOnly,s3:WriteOnly \
  --ttl 90m --cost-limit 2.50
```

Then `warm-leg.sh` (the same aligns with no `drop_caches`) and `tmpfs-leg.sh` (the RAM copy).
`build-publish.sh` publishes the index itself, once.

**Clean up, and verify it.** EFS and FSx both bill until deleted, and `--fsx-lifecycle ephemeral`
has been seen not to reap ([spawn#613](https://github.com/spore-host/spawn/issues/613)):

```sh
spawn fsx list --region us-west-2                       # must be empty
aws efs delete-mount-target --mount-target-id fsmt-…    # every one, then
aws efs delete-file-system --file-system-id "$FS"
aws efs describe-file-systems --file-system-id "$FS"     # want FileSystemNotFound
```

## Caveats, and the hour FSx cost

n = 1 per cell; one box, one image digest, 32 threads throughout, so routes are comparable to
each other. Mapping held at 56–58 s across all of them, which is what licenses reading the load
column as the data path. Rates are us-west-2 on-demand from `aws pricing` on 2026-09-26. The
first-read table drops the page cache before each align; the steady-state table does not, and
says which.

`--fsx-import-path` **created no S3 link at all** — the filesystem mounted empty because spored's
`CreateDataRepositoryAssociation` fails on a missing `iam:CreateServiceLinkedRole`, logged only
on the box while the CLI printed `Instance Ready`
([spawn#622](https://github.com/spore-host/spawn/issues/622)). The association here was created
by hand, so route C measures FSx, not spawn's FSx wiring.

Two smaller ones, both shapes this repo has hit before: the container runs as `mambauser`
(57439) and cannot write a host directory owned by the login user, so STAR's output dir needs
`chmod 777` — that failure produces *no* log, because the redirect creating the log is itself
what's denied. And under `set -u` an SSM-driven script has no `$HOME`, so `W=$HOME/work` dies on
line 3.
