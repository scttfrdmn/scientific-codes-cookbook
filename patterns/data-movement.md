# Copy, mount, or share? — getting reference data to the compute

> **Pay for the bytes you touch, not the bytes you own.** The reflex is to `aws s3 cp` the reference data onto the box before you run. Sometimes that's right. Usually, for read-only reference data, it isn't — and this page is when.

Your job needs a reference — a genome index, a taxonomy DB, a model — sitting in S3. You have a box, a bill, and a deadline. The instinct from a cluster is to stage everything to local disk first, because there the filesystem was already there and S3 was far away. Here S3 is a fast LAN away and the box costs money by the second, so staging is a choice, not a given. Make it on purpose.

## The one question under the reflex

Collapse the decision to a single cost model:

```text
cost = instance $/hr × wall-clock + staging storage + S3 requests
```

Whichever path **finishes sooner usually also costs less** — the instance-hour term dominates, so the question isn't "is copying wasteful" in the abstract, it's "does copying make this run finish sooner." That turns entirely on **how you touch the data**: all of it or a fraction, once or many times, from one node or many at once.

## The access shapes

- **You touch a fraction** (an index seeks; a BAM slices a region). **Mount, don't copy** — copying the whole object to read 5% of it is paying for bytes you never open.
- **You stream all of it, once** (a single sequential pass). Copy and mount are close; the win is overlapping the fetch with compute rather than a stage-then-run barrier.
- **You re-read all of it, many times, on one box.** Now copying amortizes — but copy into **tmpfs**, not EBS. Measured on a 28.6 GiB index: tmpfs staged it in 58 s and read it in 9 s, EBS took 118 s to stage and **231 s** to read, because an mmap'd index is random access and one gp3 volume is where that shows. "Many times" is **three**, and the [crossover is measured](../measurements/star-real/README.md). The precondition is RAM for copy *plus* working set — undersize it and the kernel kills your tool, not your copy.
- **You share one read-only reference across N nodes** — the fan-out. This is the interesting case, and it has four mechanisms with genuinely different economics.

## The fan-out case: a shared read-only index across N tasks

The cookbook's worked example is [bwa](../recipes/bwa-samtools/README.md): a whole-genome index (~8.9 GB) that `bwa mem` **mmaps** (so it needs a POSIX path, not `s3://`), read by a cohort of N samples. Four ways to put that index in front of N tasks:

| mechanism | up-front | **amortizes across N, or repeats per task?** | per-task read throughput | left to clean up |
|---|---|---|---|---|
| **copy-per-task** | none | **repeats** — every task copies the full 8.9 GB into `/tmp` (a tmpfs ≈ ½ RAM → needs `r8g`) | local after the copy | nothing |
| **EFS-share** | hydrate the index once (moves 8.9 GB into EFS) | **amortizes** — one hydration, then N warm readers share it | **329 MB/s, cold ≈ warm** (index > RAM, no page cache); a shared FS is aggregate-capped, so per-task plausibly drops under 6× | delete mount targets → filesystem, and *verify* it's gone (bills until then) |
| **snapshot-share** | build an EBS snapshot — **can't; still blocked (below)** | **repeats** — each task attaches a *fresh* volume that faults its blocks in from S3 again | *unbuildable* — spawn#579 perms still 403 | per-task volume |
| **lith mount** | build an index — **metadata only** (0.016 s here; ~26 MB for ~280k keys; scales with key *count*, not data volume) | **~nothing to repeat** — ~2 s index fetch per task, then bytes on demand | **single 445 cold / 684 warm; 6 concurrent ~583/task (445–741), no contention collapse** — separate instances, separate NICs (a floor) | **nothing** (no bulk data moved, no persistent resource) |

The **amortizes-vs-repeats** column is the one that surprises, and it inverts the intuition:

- **snapshot-share looks like the natural fan-out choice and is likely the worst.** "Build one snapshot, every task attaches a volume from it" *sounds* like it amortizes — but a snapshot-restored volume faults blocks in from S3 **on first touch, one round trip per block, with no useful prefetch** for a random-access pattern like an mmap'd index. Each task gets a *fresh* volume, so that slow hydration **repeats per task** rather than being shared. Attractive-looking, structurally slow across a cohort — and on this platform it can't even be built: **[spore-host/spawn#579](https://github.com/spore-host/spawn/issues/579) is closed, but a spawn-launched instance's role still returns 403 on *both* `s3:ListBucket` and `ebs:StartSnapshot` (re-verified), so the fix isn't live on this path.** Blocked *and* structurally worst — the option to skip.
- **FSx for Lustre is the same mechanism, and now measured rather than reasoned by analogy.** On a 28.6 GiB STAR index its S3 link gives free metadata (`ls -l` the whole tree in **0 s**, no bytes moved — the same trick as lith's index) and then faults the bytes in at **the throughput tier you bought**: 1200 GB × 125 MB/s/TiB ≈ 150 MB/s, measured **174 s** to load what lith streamed in **28 s**. Once hydrated it equals EFS (57 s). So the hydration is in front of the speed, every fresh box pays it, and the **1200 GB minimum costs $174/month to hold 28.6 GiB** — 264× S3, to be 6× slower on the read that matters. [Full five-route comparison](../measurements/star-real/README.md).
- **EFS is the only one that truly amortizes the bulk data** — hydrate once, N tasks read the one warm filesystem. That's its whole reason to exist, and why it fits a *mutating* dataset too (below).
- **[lith](https://github.com/scttfrdmn/lith)** (a read-only POSIX filesystem over S3 in native layout) sidesteps the question: it moves only **metadata** up front, so there is no bulk hydration to either amortize or repeat. For immutable reference data the crossover isn't about N at all.

## The categorical difference, and the honest limit

For an **immutable** reference — GRCh38, 1000 Genomes, a pinned kraken2 DB, our entire use case — the choice is not "how big is N before mounting wins." lith moves nothing bulk ever: build the small index once, share it, and every task reads only the bytes its mmap touches. There is no 8.9 GB hydration and no filesystem billing quietly after the run.

The limit that scopes it: lith's index is a **point-in-time snapshot, ETag-checked** — if an object mutates out from under it, reads return `EIO` (a *loud* failure, not silent corruption — which is the right failure). So lith is build-once-share-N for data that doesn't change; a **mutating** bucket wants EFS's live filesystem instead. Reference data doesn't mutate, which is exactly why this fits.

And it fits *where reference data actually lives*: the measured runs read the GRCh38 index straight from the **public 1000 Genomes bucket** (RODA), not a private copy — the natural home for shared reference data is a public/open-data bucket, which lith reads directly with no per-instance credential plumbing. (Reading a *private* bucket from a container has the separate IMDS-auth wrinkle in [the container path](../practices/container-path.md); public reference data sidesteps it entirely.)

> **Measured — lith holds under concurrency, and wins.** Six tasks mmap'ing the same index at once averaged **~583 MB/s each** (445–741 range) with **no contention collapse**, because each instance reads S3 over its *own* NIC — unlike EFS's single shared filesystem, whose aggregate capacity is split across readers. Per task, lith (445–741) already beats EFS's 329 MB/s in isolation; under six-way concurrency the gap widens. Setup is ≈0 and it leaves nothing behind. So for immutable reference data across a fan-out, **lith is the pick**: faster per task, no hydration, no cleanup. (The 445–741 MB/s is a *floor* — lith fell back to a conservative readahead with no NIC probe available in the guest.)

## The other axis: which bytes, not where they are

This page picks a *transport*. It says nothing about whether the bytes that arrive are the ones
the recipe was verified against — a separate question, with a separate answer, and one the
TB-scale mount case cannot even ask (you cannot hash what you declined to download). That seam
is [proving which bytes you ran on](../practices/input-provenance.md).

## Which resource are you sizing on?

This is the seam between this page and [sizing](sizing.md). A **compute-bound** run sizes on cores — where adding them stops paying (the knee). A **data-movement-bound** run sizes on **NIC and RAM**: cold reads are your NIC (bytes stream from S3), warm re-reads are your RAM (the cache), metadata is free. If your job spends its time waiting on bytes rather than computing on them, size the box by bandwidth and working-set, not core count — and see [lith's node-sizing guidance](https://scttfrdmn.github.io/lith/sizing/) for the depth, since it's the tool that surfaced the distinction.

## The rule

Pay for the bytes you touch, not the bytes you own. Copy only when one box will re-read the whole thing at least ~3 times, and then into **tmpfs**, never EBS. For a shared read-only reference across a fan-out, **mount** — lith for immutable data (build-once-share-N, nothing left behind), EFS if the data mutates. Don't buy FSx Lustre for this: its floor is 1200 GB and its first read is slower than S3's. lith is a tool with its own docs; this page is *when you'd reach for it*, not how it works — that's at [scttfrdmn.github.io/lith](https://scttfrdmn.github.io/lith).
