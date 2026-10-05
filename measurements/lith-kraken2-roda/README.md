# kraken2's access pattern is a choice, and on S3 it is worth 4,000x

> **This is not a finding about lith, and not a storage bake-off.** On one unchanged S3 object,
> changing only *how the application asks* moves random-lookup throughput from **7.9/s to
> 32,128/s — ~4,067×**, with the storage, the latency and the bucket held constant. The mount is
> byte-efficient (23x) and runs at exactly the rate raw S3 serves one synchronous request (6.9/s
> vs the mount's 7.9/s). What fails is `mmap`: no queue depth, no batching, one fault at a time.
> **kraken2's performance on object storage is a property of a 2013 design constraint — "the
> database must fit in RAM" — not of the problem or of S3.**

The RODA kraken2 RefSeq-Complete v205 database is **1.206 TB** in `us-west-2`; `hash.k2d` alone is
`1,189,091,671,800` bytes. Nothing can copy that casually, so it is the case where a mount is
forced rather than chosen — the adversarial test for [lith](https://github.com/scttfrdmn/lith),
not a flattering one. STAR's 28.6 GiB result was a *sequential* index load; `kraken2
--memory-mapping` does *random* page faults over a TiB-scale hash table.

## The mount is free, and that holds at TB scale

| | |
|---|---|
| `lith index build` over 1.206 TB | **~1 s** |
| index size | **728 bytes** |
| files visible / `stat` on `hash.k2d` | 9 of 9 / `1,189,091,671,800` bytes |

No caveat at this size. That was the open question, and **it is also what makes the trap work** —
the mount looks equally healthy whether or not it can serve your reads.

## The one table that settles the architecture

All in-region. The first three rows are the *same bucket, same object, same random offsets, same
seed* — only the access method changes. NVMe is the same `r8gd.8xlarge` that did the copy.

| access path | random 4 KiB | latency | vs the mount |
|---|---|---|---|
| **lith mount** (`mmap`, 1 fault at a time) | 7.9/s | 126 ms | 1× |
| raw S3, **queue depth 1** | 6.9/s | 145 ms | 0.9× |
| raw S3, **queue depth 64** | **465.9/s** | **137 ms** | **59×** |
| raw S3, queue depth 1024 | 788.7/s | 1298 ms | 100× |
| **local NVMe**, single-threaded | **5,412/s** | **0.185 ms** | **685×** |

**Read the first two rows together: the mount is as fast as a serial request can be.** lith adds
no measurable overhead — the naive conclusion "the mount is slow" is wrong, and the correct one is
"one synchronous fault at a time is slow." That decomposes the 685× NVMe advantage into **~100×
recoverable by concurrency on identical storage**, and only ~6.9× genuine storage advantage.

Sequential reads were never the problem: **146 MB/s** on `hash.k2d` and 107 MB/s on `taxo.k2d`
against **170.9 MiB/s** for a plain `aws s3 cp` — 62–86% of raw S3.

## Concurrency buys back the latency without the latency changing

The assumption worth naming, because nobody checks it: *"random access over network storage is
hopeless"* is treated as physics when it is a statement about **queue depth**.

Scaling is linear to depth 64 — **68× more work at flat per-request latency (145 → 137 ms)**.
Nothing got faster. The plateau past 256 is **this probe's client, not S3**: latency inflates
137 → 1298 ms while throughput barely moves (466 → 789), which is in-process queuing, and
789 × 4 KiB = 3.2 MB/s is nowhere near the NIC or S3's ~5,500 GET/s per prefix. **So 100× is a
floor.**

This *supports* [lith#232](https://github.com/scttfrdmn/lith/issues/232)'s "lith has no lever"
rather than undermining it: the lever needs several future offsets at once, and a page fault
exposes one. The lever belongs to the application, and the number says it is worth ~68×.

## And coalescing at realistic density is worth another 42×

Depth alone is half of it. The other half is *request shape*, and it only shows up at the density
a real sample implies. A 1M-read sample is ~30M minimizer lookups over 1.189 TB — a mean spacing
of **39.6 KB**, which a probe can reproduce by confining 10,000 lookups to a 378 MiB region
rather than issuing 30M requests.

Same object, same depth 256, same 10,000 probes; only the coalescing window changes:

| window | requests | MiB | wall | lookups/s |
|---|---|---|---|---|
| individual (kraken2's shape) | 10,000 | 39.1 | 13.11 s | 763 |
| 64 KiB | 3,781 | 134.9 | 5.20 s | 1,922 |
| 256 KiB | 1,316 | 284.3 | 2.18 s | 4,583 |
| 1 MiB | 365 | 352.4 | 0.91 s | 10,978 |
| 4 MiB | 94 | 371.9 | 0.46 s | 21,892 |
| **16 MiB** | **24** | 375.9 | **0.31 s** | **32,128** |
| *plain sequential scan* | *48* | *377.9* | *0.35 s* | *28,389 — **1125 MB/s*** |

**There is no knee: the sweep converges on a scan.** At 16 MiB the "coalescing" fetches 375.9 of
378 MiB and matches the explicit scan rung within noise, so **at real density the optimal strategy
is to stream the index, not probe it.** That is the merge-join argument, measured.

The control is what makes it a result rather than a story: at **sparse** density (119 MB apart)
the identical sweep gives **1.0×** — 773.8 → 779.9 lookups/s. Density was the variable.

**The cost model agrees with the performance model, which is not usual.** Requests fell **417×**
(10,000 → 24) while bytes rose only 9.6×, and in-region transfer is free while GETs are billed. So
the coalesced shape is simultaneously ~42× faster and ~400× cheaper per lookup. kraken2's access
pattern fights both at once.

Combined with depth, **7.9/s → 32,128/s is ~4,067× on storage that never changed** — and 5.9×
above this page's single-threaded local-NVMe probe (not concurrency-matched, so not a claim that
S3 beats NVMe; it *is* a demonstration that the $1.02 copy bought less than changing the request
shape would have, for free).

### What it does to the layout question

Measured sequential S3 is **1125 MB/s** with depth, not the 146 MB/s a single stream gives. So a
fan-out that streams the index costs:

| | wall | cost |
|---|---|---|
| **10 × `c8g.2xlarge`, 110 GiB each** | **~98 s** | **~$0.19** |
| copy 1107 GiB to one `r8gd.8xlarge` NVMe | 1561 s | $1.02 |

~5.4× cheaper, ~16× faster, no big-memory or big-NVMe box in the design. Boot dominates, which is
why ten larger workers beat a hundred small ones. And a scan has the property a point lookup
cannot: **it amortizes across samples** — batch 20 samples into one pass and per-sample cost falls
~20×, where 20 samples through a resident hash table costs 20× the lookups.

## Three of my own claims the measurements killed

**Byte amplification was a red herring.** I argued a scattered 4 KiB fault pulls an 8 MiB block
(2048×), reasoning backwards from a latency. Measured by counting NIC bytes across exactly 200
probes: **17 MiB total = 91 KiB per probe = 23×.** lith's random path is byte-efficient. The
crossover I quoted (4,000–15,000 reads) was wrong too — at 91 KiB/probe, cumulative fetch reaches
1107 GiB at ~12.8M probes ≈ **425,000 reads**, so below a few hundred thousand reads the mount
moves *fewer* bytes than copying. It still loses, on latency alone.

**My first "sort the queries" test measured nothing, and the test was the fault.** 10k probes over
1.189 TB sit **~119 MB apart**, so a 1 MiB window merged 97 of 10,000 and doubled bytes for no
gain — undersized by ~100× to detect its own mechanism. I reported it as a null result at the
time; re-run at realistic density it is worth **42×** (above). The lesson is not about sorting: a
null result from a probe that cannot resolve the effect is not evidence, and I should have
computed the mean spacing before believing it.

## Why caching cannot rescue it either

For uniform-random access the steady-state hit rate is just `cache ÷ table`:

| cache | hit rate |
|---|---|
| ~16 GiB (the `r8g.xlarge` canary) | **1.4%** |
| ~230 GiB (`r8gd.8xlarge`) | 20.8% |
| **90%** | **997 GiB** |

So the favourable regime needs ~1 TiB of RAM — the configuration that makes the mount pointless.
A hash table destroys spatial locality by construction, which is what makes the uniform model
apt. *(Related: lith's RSS tracking bytes-read ~1:1 on the canary is **correct** behaviour, not
unbounded growth — default `--mem-cache` is 25% of RAM = 7.75 GiB on that box, and the run pulled
983 MiB, so it fit with 7× headroom.)*

## What a result costs

Copying the runtime set (`hash.k2d` + `taxo.k2d` + `opts.k2d`, **1107.6 GiB**) to local NVMe,
measured twice on `r8gd.8xlarge` ($2.3514/hr):

| concurrency | wall | rate | cost |
|---|---|---|---|
| `max_concurrent_requests 64` | 1561 s | 0.76 GB/s | $1.0196 |
| `max_concurrent_requests 128` | 1686 s | 0.71 GB/s | $1.1012 |

**Doubling client concurrency made it slightly slower**, which settles the open question from the
first run: at 41% of a 15 Gbps NIC it is bounded by **NVMe write throughput**, not by the network
or the client. So ~$1.02–1.10 is the real cost of the copy on this instance shape, and a bigger
NIC would not move it — more NVMe devices to stripe across would.

It amortizes over every sample in the instance's lifetime: **~$1.02 for one sample, ~$0.01 each
for a hundred.** And NVMe, not RAM, is the destination — `x8g.24xlarge` (1536 GiB) is $9.3792/hr
against $2.3514, and `--memory-mapping` exists precisely so the table need not be resident.

**Unmeasured, and the one number still missing: kraken2's reads/min and $/sample.** Two attempts
failed, and the second found the cause of both — **not** anything to do with mmap or storage:

```
Loading database information... done.
Unable to open file: /w/out-10k.kraken, reason: Permission denied
```

`chown`-ing the NVMe mount to the *instance* user is not enough, because the container runs as the
**image's** user. It is the same ownership trap this project documents for staged *inputs*
([container-path](../../practices/container-path.md)), arriving through an **output** path, and the
fix is `chmod 1777` on the output directory — what host `/tmp` uses, for exactly this reason.

An earlier version of this page speculated that ~9 minutes failing to finish 1M reads meant
"either `mmap` faults do not parallelise or startup over a 1.1 TiB mapping is expensive."
**That is withdrawn** — the first attempt had the identical permission setup, so the speculation
had a much duller explanation available and should not have been offered. A one-second container
write test before the 28-minute copy would have caught it twice over, and now runs.

## Three process notes that cost real money

**Check the region before concluding anything.** Two runs were placed in `us-west-1` against the
`us-west-2` bucket — unasked-for, and silent. Cross-region the same work pulled **1.52 MiB/s**
against 107–146 MB/s. `InvalidInstanceID.NotFound` then means *wrong region*, not a dead box; I
read it twice as "the shell died" while kraken2 was still faulting. The control that settled it
was one line of the probe: `aws s3 cp` on the same box. **Reach for the no-tool baseline first.**
Filed as [lith#362](https://github.com/scttfrdmn/lith/issues/362); the warning shipped same-day in
[#365](https://github.com/scttfrdmn/lith/issues/365).

**A probe must stream its result.** `spawn launch --command` does not stage logs out (spawn#643's
pre-stop flush is a `task run` feature). Learned in the canary, then re-learned one phase later
when P3 was lost anyway.

**A heredoc binds to the last command in a pipeline.** `python3 - 2>&1 | tee <<'PY'` gives Python
an empty stdin and makes `tee` write the script's own source into the results file. One wasted
launch.

## A note on the order things happened

The hypothesis — *kraken2's access pattern is a choice, not a property of the problem* — was
stated **before** the density probe was written, as was the objection that fixed the amplification
error ("why assume a block read serves only the 4 KiB?") and the one that moved the destination
from RAM to NVMe. That order matters: it makes the 42× a confirmed prediction rather than a
narrative fitted to a number afterwards. Three of this page's own earlier claims died in the
process, which is the honest cost of having had them.

Raw output in [`results/`](results/); scripts are [`canary.sh`](canary.sh),
[`readpath.sh`](readpath.sh), [`nvme.sh`](nvme.sh),
[`concurrency.sh`](concurrency.sh) and [`density.sh`](density.sh). [data-movement](../../patterns/data-movement.md) is when to
reach for a mount at all; this page is the access pattern that voids it.
