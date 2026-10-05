# kraken2's access pattern is a choice, and on S3 it is worth 4,000x

> **This is not a finding about lith, and not a storage bake-off.** On one unchanged S3 object,
> changing only *how the application asks* moves random-lookup throughput from **7.9/s to
> 32,128/s — ~4,067×**, with the storage, the latency and the bucket held constant. The mount is
> byte-efficient (23x) and runs at exactly the rate raw S3 serves one synchronous request (6.9/s
> vs the mount's 7.9/s). What fails is `mmap`: no queue depth, no batching, one fault at a time.
> **kraken2's performance on object storage is a property of a 2013 design constraint — "the
> database must fit in RAM" — not of the problem or of S3.**

## The answer, before the evidence

**kraken2 is 98.7% I/O wait.** Identical 100k-read work takes **139.9 s cold** and **1.77 s warm**
(32 threads, `drop_caches` between) — so its compute floor is ~1.8 s per 100k reads and everything
else is waiting for the database. The access pattern is not a detail of this workload; it *is* the
workload.

**Cross-region is an anti-pattern, which collapses the decision.** In-region, S3→EC2 transfer is
free and only requests are billed. So *buying byte-precision buys the wrong thing* — and that is
exactly what `mmap` does, which is why mounting the database fails rather than merely being slow.

Three ways to feed it, per 1M-read sample:

| | per-lookup 4 KiB (`mmap`, mount) | **stream big chunks** | **copy to local** |
|---|---|---|---|
| requests | 28.5M → **$11.40** | 70,875 → $0.028 | 17,719 → **$0.0074** |
| bytes moved (free in-region) | 117 GB | 1,189 GB | 1,189 GB |
| big box required | no | **no** | **yes** — ≥1.2 TB NVMe or RAM |
| barrier before any science | none | **none**, overlaps fetch | **26 min** |
| works with kraken2 today | yes — at **7.9 lookups/s**, i.e. never finishes | **no**, needs a rewrite | **yes** |

**So the optimum is one question: how many samples per box?**

- **Cohort (≳30 samples) → copy once to local NVMe.** The barrier and the $1.02 amortise to
  **$0.367/sample**, and this is **the best option available with kraken2 as it exists.** It needs
  the big box.
- **One or a few samples → streaming big chunks wins**, because at low N the barrier and the big
  instance dominate. kraken2 cannot do this, so today the honest choices are to eat the barrier or
  use a capped DB (Standard-8/16) on a small box.
- **Never: per-lookup random access in-region.** 1,600× the request cost to save bytes that are
  free.

**The amortisation caveat that matters:** 1.77 s is kraken2's *compute floor*, not what sample #2
costs. A different sample probes different table locations, and 247 GiB of page cache against a
1,107 GiB table holds **22%** — so every fresh sample is ~78% cold. **Caching does not reduce
per-sample cost; only the copy does.**

---

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
Nothing got faster.

**The plateau past 256 was my Python client, and the margin is embarrassing.** I originally
guessed that and asserted it without testing. Rewriting the identical sweep in **Go** (goroutines,
no GIL, plain `net/http` range GETs, same offsets and seed) **on the same instance type**:

| depth | Python/boto3 | **Go** |
|---|---|---|
| 1 | 6.9/s | 8.8/s |
| 128 | — | 1,237.7/s |
| 512 | — | 5,443.8/s |
| 2048 | — | 24,370.6/s |
| **8192** | — | **34,456.5/s** (141 MB/s, 0 errors) |
| best Python | 788.7/s | **44× higher** |

Two instance shapes were also compared to rule out the NIC: `c8g.2xlarge` (8 vCPU, *burstable*
"Up to 15 Gb") vs `r8gd.8xlarge` (32 vCPU, *sustained* 15 Gb). Under Python the plateau moved only
**788.7 → 905.1 (+15%)** — 4× the cores and a better NIC bought almost nothing, because the GIL
makes extra cores inert. So the ceiling was never bandwidth, PPS, or S3; **it was one process's
ability to keep requests in flight.**

**Against the lith mount's 7.9/s that is 4,362× — on identical storage, from an 8-vCPU box.** It
also **exceeds single-threaded local NVMe (5,412/s) by 6.4×**, which is the sentence that changes
the architecture question from *"is the copy expensive"* to *"is the copy necessary."*

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

Measured sequential S3 is **1125 MB/s** with depth, not the 146 MB/s a single stream gives (and the
Go probe shows even that is not the ceiling). So a fan-out that streams the index costs:

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

### Measured at last: what a kraken2 result costs

Human WGS reads (`SRR062634`) against the full 1.1 TiB RefSeq-Complete DB on local NVMe,
`r8gd.8xlarge`, 32 threads. kraken2's own reported rate, and a read-count ladder so a kill
costs the largest rung rather than all of them:

| reads | kraken2 wall | its rate | classified | compute $ |
|---|---|---|---|---|
| 10,000 | 21.9 s | 27.4 Kseq/min | 98.64% | $0.0268 |
| 100,000 | 70.4 s | 85.3 Kseq/min | 99.29% | $0.0529 |
| **1,000,000** | **483.2 s** | **124.2 Kseq/min** | **99.30%** | **$0.3331** |
| 100,000 (repeat) | 71.2 s | 84.2 Kseq/min | 99.29% | $0.0529 |

Fitting the extremes: **≈17 s fixed + 466 s per million reads**, i.e. a marginal rate of
**~129,000 reads/min**. The rate quadruples from 10k to 1M purely because that fixed 17 s of
mmap and taxonomy setup amortises — the same reason [recipe timings are not compute
cost](../../patterns/layout-and-effective-cost.md).

98.6–99.3% classified is the sanity check that matters: these are human reads and human is in
RefSeq Complete, so near-total classification is what correct looks like.

**$/sample, with the copy amortised:**

| samples on one box | copy | compute | **each** |
|---|---|---|---|
| 1 | $1.0209 | $0.3331 | **$1.354** |
| 10 | $0.1021 | $0.3331 | $0.435 |
| 100 | $0.0102 | $0.3331 | **$0.343** |

So the 1107 GiB copy stops dominating at roughly **30 samples**, and the floor is **~$0.33 per
million reads**. Whole ladder, copy included: **$1.5643** for 2.11M reads classified.

**A free cross-run identity:** the two 100k rungs returned *exactly* 99,291 classified and 709
unclassified. kraken2 is deterministic given the same input and thread count, so each rung
checks the other.

**And the rung I designed badly, stated plainly: it did not test what it was for.** The repeat
was meant to separate "what the first sample costs" from "what the next one costs" via page
cache. But `cache_gib_before` was **243, 244, 242, 241 GiB** — the 1107 GiB copy leaves the cache
already full of the database, so **there was never a cold rung to compare against**, and 70.4 s
vs 71.2 s measures run-to-run stability rather than cache warmth. Testing it properly needs
`drop_caches` before the first rung. What the identical times *do* rule out is any large
remaining cache win on this box.

### The term I had not counted: S3 request charges decide the design

Throughput is not the binding constraint once the client is competent — **money is**, and only in
a bucket you own. At $0.0004/1000 GETs, for one 1M-read sample at ~30 lookups/read:

| design | throughput | requests | **GET $** | bytes moved | barrier |
|---|---|---|---|---|---|
| `mmap` over a mount (what kraken2 does) | 7.9 lookups/s | — | — | 2.7 GB | none, but unusable |
| per-lookup range GETs, deep queue | **34,456/s** | 30,000,000 | **$12.00** | 123 GB | none |
| sorted + coalesced scan, 16 MiB | **1125 MB/s** | 74,319 | $0.0297 | 1,189 GB | none |
| **`aws s3 cp`, 64 MB chunks** | 0.76 GB/s | **18,580** | **$0.0074** | 1,189 GB | **26 min** |

**The copy is the most coalesced reader of all** — biggest chunks, fewest calls, cheapest in
requests. The scan makes 4× *more* requests than the copy, and at realistic density it touches
essentially every chunk anyway, so it moves roughly the same bytes.

So the honest split is narrower than "the scan wins on economics": **concurrency is what makes a
no-copy design possible at all, and the only thing the scan buys over the copy is that it overlaps
fetch with compute instead of being a 26-minute barrier**
([data-movement](../../patterns/data-movement.md) already says exactly this). What coalescing
rules *out* is the per-lookup design — byte-efficient, 1,600× the copy's request count, and the
only uneconomic option on the list.

**And a trap worth naming, which is bigger than requests.** This RODA bucket reports
`Payer: BucketOwner`, so every GET above is **free to the requester** — the Open Data sponsor
pays. For a 1,189 GB database the terms RODA is absorbing are:

| | if you host it yourself |
|---|---|
| storage | **$27.35/month**, standing, before one read is classified |
| GET charges | $0.0074 per copy, or $12.00 per per-lookup sample |
| in-region transfer S3→EC2 | free — **and $23.78 cross-region for a single copy** |

So the per-lookup pattern looks costless exactly while you prototype against public data, and the
whole cost structure changes the moment the database lives in a bucket you own. **A recipe that
only works because someone else is paying is not a recipe** — check `Payer` and cost it as though
you owned the bucket. (That transfer line also reprices the cross-region mistake earlier on this
page: at full scale it is $23.78, not merely slow.)

### Eliminating requests costs 9.7× the data movement — it is a frontier, not a win

Chunk size is the dial, and the two ends are genuinely opposed: per-lookup 4 KiB GETs move only
the **117 GB** you actually need but make 28.5M requests; big chunks make almost none but move the
whole **1,189 GB**.

| chunk | requests | GB moved | amplification | GET $ | **in-region total** | **cross-region total** |
|---|---|---|---|---|---|---|
| 4 KiB | 28,501,953 | 116.7 | 1.0× | $11.40 | $11.40 | **$13.74** ← best |
| 64 KiB | 14,671,459 | 961.5 | 7.8× | $5.87 | $5.87 | $25.10 |
| 256 KiB | 4,529,938 | 1,187.5 | 9.7× | $1.81 | $1.81 | $25.56 |
| 16 MiB | 70,875 | 1,189.1 | 9.7× | $0.0284 | $0.0284 | $23.81 |
| 256 MiB | 4,430 | 1,189.1 | 9.7× | $0.0018 | **$0.0018** ← best | $23.78 |

**Amplification saturates at 9.7× by 256 KiB** — past that you are already touching every chunk,
so you move the entire table regardless and are *only* buying request reduction. Which is why,
in-region, there is no reason to stop short of the largest chunk you can buffer.

**And the optimum inverts with transfer pricing.** In-region S3→EC2 transfer is free, so you buy
fewer requests with unlimited amplification and the copy wins by **1,615×**. Cross-region at
$0.02/GB, bytes dominate, so you buy precision and eat the request count — and **per-lookup wins
by 1.6×**. Same workload, opposite architecture.

> **So the sharpest version of the finding: kraken2's `mmap` pattern minimises *bytes read*. That
> was the correct objective on a local disk with finite bandwidth. In-region S3 charges for
> *requests* and gives bytes away free — so the design is optimal for a cost model that no longer
> applies.** Not wrong; obsolete. And it is the reason the naive fix ("just mount it") fails: a
> mount faithfully preserves the byte-minimising access pattern, which is exactly the thing that
> is no longer worth minimising.

### Scaling out does not help, and the method has slack at any scale

**$0.3044 per million reads is invariant under scale-out.** The compute is 14,912 **core-seconds**
per million reads; splitting it across 10 or 100 nodes buys wall-clock and changes the bill not at
all, because it is the same core-seconds either way. So the only thing that moves $/result is
reducing core-seconds — which means the *method*, not the deployment.

And there is a lot to reduce. 124.2 Kseq/min on 32 cores is **64.7 reads/s/core**; at 10–30
minimizer lookups per 100 bp read that is:

| lookups/read assumed | lookups/s/core | **cycles per lookup** | NVMe utilisation |
|---|---|---|---|
| 10 | 647 | **4,638,000** | 12% |
| 20 | 1,294 | **2,319,000** | 24% |
| 30 | 1,940 | **1,546,000** | 36% |

**A DRAM miss is ~200–300 cycles and a 0.185 ms NVMe read is ~550,000.** So every bracket of that
range costs several NVMe round-trips' worth of time per lookup, while the device sits **12–36%
utilised**. Neither the CPU nor the storage is saturated — which is the signature of being
**latency-bound with insufficient concurrency in flight.**

That is the same root cause as the S3 result above, on different hardware: **kraken2 ties its I/O
queue depth to its thread count.** 32 threads means 32 outstanding faults, because threads are
doing double duty as CPU parallelism *and* as I/O concurrency. The
[queue-depth sweep](#concurrency-buys-back-the-latency-without-the-latency-changing) showed S3
scaling linearly to depth 64+; NVMe wants 32–128+ for the same reason. A design with explicit
async I/O would get depth 256 from a handful of threads.

**So scale-out multiplies the slack rather than removing it.** At ~36% device utilisation you are
renting ~2.8× the hardware the work needs ([effective
cost](../../patterns/layout-and-effective-cost.md)); a 100-node fan-out rents 100× that slack.

This also corrects what this page said a moment ago. I wrote that the run was "not obviously
I/O-bound" on the strength of it using only 36% of the NVMe ceiling — but the cycles-per-lookup
figure makes that reasoning too weak: 1.5M cycles is far beyond any plausible CPU cost for a hash
probe, so the time is going into stalls. **Not CPU-bound, not device-saturated, concurrency-starved.**

**That test has now run, and my rung design broke again in the same way — no `drop_caches`
*between* rungs**, so the first rung warmed the cache and the rest rode it:

| threads | kraken2 secs | rate | what it actually measured |
|---|---|---|---|
| 4 | 124.284 | 48.3 Kseq/m | **cold** — paid all the faults |
| 8 | 4.653 | 1,289.6 Kseq/m | warm |
| 16 | 1.724 | 3,480.2 Kseq/m | warm |
| 32 | 1.771 | 3,387.9 Kseq/m | warm, saturated at 16 |
| **32, `drop_caches`** | **139.909** | 42.9 Kseq/m | **cold** |

4→8 threads "improving" 27× is impossible as thread scaling; the cold rung landing back at 139.9 s
is the proof. **The accident is more useful than the design was:** cold/warm on identical work is
**79×**, so kraken2's compute floor is ~1.8 s per 100k reads and **98.7% of a cold run is I/O
wait.** That answers the CPU-vs-I/O question definitively — it is I/O — and it means the earlier
124.2 Kseq/min figure was partially warm, not cold.

**The largest slack is algorithmic and invisible at every scale.** 99.30% of these reads classify,
overwhelmingly to one organism — the run performs tens of millions of full-table probes to
rediscover "human" a million times over. A pre-filter (Bloom/xor over the minimizer set, ~1–2
bytes/key) or any use of sample-level structure would eliminate most lookups outright. kraken2
treats every read as independent and novel, which is correct and maximally wasteful for the
commonest real workload.

So, revising the earlier framing: a scan-shaped redesign saves the **$1.02 copy and the big box**;
the concurrency and pre-filter changes are what would move the **$0.33**. Scale-out moves neither.

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

**Two bugs, one invisible cause.** kraken2 loaded the 1.1 TiB database and then died with
`Unable to open file: /w/out-10k.kraken, reason: Permission denied` — `chown`-ing the NVMe mount
to the *instance* user is not enough, because the container runs as the **image's** user. Same
ownership trap this project documents for staged *inputs*
([container-path](../../practices/container-path.md)), via an output path; fix is `chmod 1777`.
But the reason three such failures were *undiagnosable* is that
**`spawn launch --command` runs under `bash -e`** (`$-` == `ehB` before any user `set`), and
`set -uo pipefail` does **not** clear it — so each script exited *before* the line that would
have reported why. Filed [spawn#707](https://github.com/spore-host/spawn/issues/707) asking for
the effective shell options to be echoed into `command.log`, not for `-e` to be removed. Use
`set +e` explicitly and check statuses by hand. **Two correct local tests actively misled me
here**: `zcat | sed …q` and `$(( $(failing) / 4 ))` both *survive* `set -uo pipefail` alone, so
local was right about the construct and silent about the environment.

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

## What a refactor would buy, from measured floors

The forward question: not which existing option is least bad, but what is reachable if the tool
is fixed. Every input below is measured on this page.

**The ceiling is kraken2's own compute floor, 27× away.** Warm, it does 100k reads in 1.77 s, so
1M reads is **17.7 s of compute** against the **483.2 s** actually observed — $0.0116 vs $0.3331,
**29× cheaper**.

**The obvious refactor does not get there, which is the useful negative result.** Replacing `mmap`
with async range GETs needs **565k–1.69M lookups/s** to feed that floor; Go at depth 8192 delivers
**34,456/s** on 8 vCPU (~138k scaled to 32). **Still 4–49× short.** Concurrency is necessary and
nowhere near sufficient.

**The scan gets there, and its cost is per-*batch* rather than per-sample** — one pass answers an
arbitrarily large batch, which is the property neither the copy nor per-lookup has. On a `c8gn.16xlarge` (64 vCPU, 200 Gb, no local disk, $3.792/hr) at the **measured 18.48 GB/s**:

| samples batched | scan | compute | total | **$/sample** |
|---|---|---|---|---|
| 1 | 64 s | 18 s | $0.0939 | **$0.0939** |
| 4 | 64 s | 71 s | $0.1498 | $0.0375 |
| **100** | 64 s | 1771 s | $1.9407 | **$0.0194** |
| 1000 | 64 s | 17710 s | $18.73 | $0.0187 |

Compute overtakes the scan at just **3.6 samples**, so above that you are paying to classify
rather than to read — the right place to be.

**$0.0200/sample against today's best of $0.3672 — 18× cheaper**, with no local disk, no 26-minute
barrier and no 1.2 TB instance requirement.

### Measured, not assumed: the scan rate, and whether prefix sharding matters

The batched projection hinged on an assumed 10 GB/s. It is now measured, and **10 GB/s was
conservative.** Large sequential range GETs on the real `hash.k2d`, bytes discarded (pure network
read, no disk in the path):

| chunk | conc | `c8gn.4xlarge` (16c) | `c8gn.16xlarge` (64c, 200 Gb) |
|---|---|---|---|
| 8 MiB | 32 | 1.44 GB/s | 1.42 GB/s |
| 8 MiB | 128 | 5.12 GB/s | 5.40 GB/s |
| **8 MiB** | **512** | **5.64 GB/s** | **18.48 GB/s — 147.8 Gbit, 74% of NIC** |
| 32 MiB | 512 | 4.73 GB/s | 17.95 GB/s |
| 256 MiB | 512 | 5.77 GB/s | 13.97 GB/s |

Zero errors anywhere; 533 GB pulled per run. **Concurrency is worth 13×, chunk size only ~1.3× —
and 8 MiB beats 256 MiB at depth 512**, because 512 × 256 MiB means 128 GB in flight, well past
useful.

> **The whole 1,189 GB table streams in 64.3 s. Copying it took 1,563 s — 24× slower for the same
> bytes**, because the copy is bottlenecked on NVMe *write* (0.76 GB/s) while the stream is
> bottlenecked on the NIC (18.48 GB/s). The copy exists to make re-reads fast; if you read once
> per batch, the write is pure overhead.

**Prefix sharding: not the binding constraint at these rates.** S3 documents ~5,500 GET/s per
prefix, and the earlier 34,456/s was all against one key in one prefix — so this needed checking.
1,024 objects staged two ways in our own bucket, 4 KiB GETs:

| layout | conc 256 | conc 1024 | conc 2048 |
|---|---|---|---|
| flat (1 prefix) | 8,079/s | 31,659/s | **44,790/s** |
| sharded (64 prefixes) | 8,774/s | 23,806/s | **47,388/s** |

**A single prefix sustained 44,790 GET/s — 8× the documented figure — with zero errors**, and
sharding across 64 prefixes gave +5.8%, inside the run-to-run noise (note sharded@1024 came in
*below* flat@1024). Two caveats: S3's prefix partitioning is **adaptive**, so fresh prefixes may
not be partitioned yet and a null result here is weak evidence rather than proof sharding never
helps; and "flat" here means 1,024 distinct keys under one prefix, not a single key.

**The first attempt at this rung was invalid and the cause was mine**: the SDK logged a DEBUG line
per request through `tee`, producing 20 MB of output and a serialization point that made
81,920-request rungs collapse to ~500/s with zero errors. Fixed with `logging.Nop{}` and
`WithClientLogMode(0)`.

### Which box — and the rate card gets it backwards twice

| | $/hr | scan rate | $ per scan | $/sample at N=100 |
|---|---|---|---|---|
| `c8gn.4xlarge` (16c) | 0.9480 | 5.88 GB/s | **$0.0533** | **$0.0099** |
| `c8gn.16xlarge` (64c) | 3.7920 | 18.48 GB/s | $0.0678 | $0.0380 |

4× the rate card buys **3.1×** the bandwidth, so the fat box is **1.3× dearer per byte scanned** —
and once the compute leg is included the small box is **~4× cheaper per sample**, because kraken2
appears to saturate near 16 threads and the 64-core box leaves ~48 cores idle
([effective cost](../../patterns/layout-and-effective-cost.md): you rent a bundle and use a
fraction). *Caveat: that saturation came from a 1.7 s warm run, too short to be a reliable scaling
measurement — it is the weakest input in this table.*

So the fat NIC buys **latency** (64 s vs 202 s to sweep the table), not $/result. Which one is
right depends on whether you need the answer in one minute or four.

### The asymmetry that makes the shape obvious

| | bytes |
|---|---|
| 100 samples of queries (3B minimizers × ~16 B) | **48 GB** |
| the table | **1,189 GB** |

> **kraken2 streams the reads and holds the table. The right shape streams the table and holds the
> reads.** The query side is 25× smaller, so buffering queries and sweeping the table past them is
> the natural structure — feasible in RAM to ~100 samples before an external sort is needed.

### Three caveats, because this is a projection and not a measurement

- **The 17.7 s floor derives from a 1.77 s warm run** — short enough that thread startup may
  dominate, so the true floor could be lower. `t16` (1.724 s) ≈ `t32` (1.771 s) hints at
  saturation by 16 threads but is not a reliable scaling measurement at that duration.
- ~~10 GB/s on a 200 Gb NIC is assumed.~~ **Now measured at 18.48 GB/s** (74% of NIC, zero
  errors), so the assumption was conservative and the batched figures improved.
- **The refactor is a database format change, not only code.** A merge join needs the table as a
  sorted, chunked run by minimizer hash — a one-time offline rebuild, which `kraken2-build`
  already is, but a format change is an adoption cost on top of an engineering one.

## What the excursion cost

Eleven launches, roughly **$8.60**. The kraken2 figure alone took five, of which four died to
three bugs tangled together — a container-uid output permission failure, a SIGPIPE'd pipeline
under `pipefail`, and `spawn --command` running under an inherited `bash -e` that made all three
exit before reporting why. Three wrong diagnoses, one of which reached this page and was
withdrawn.

Worth stating because it is the honest shape of this kind of work: **the measurements were cheap
and the scaffolding was expensive.** Every number above cost cents of compute; the $8.60 was
mostly paid to learn that a probe must stream its output, that a cheap rung must come first, and
that `$-` is one line worth printing.

Raw output in [`results/`](results/); scripts are [`canary.sh`](canary.sh),
[`readpath.sh`](readpath.sh), [`nvme.sh`](nvme.sh),
[`concurrency.sh`](concurrency.sh) and [`density.sh`](density.sh). [data-movement](../../patterns/data-movement.md) is when to
reach for a mount at all; this page is the access pattern that voids it.
