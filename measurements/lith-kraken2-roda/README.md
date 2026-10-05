# A 1.2 TB mount costs nothing to build, and the thing that kills it is queue depth

> **`lith index build` covered 1.206 TB of RODA in about a second into a 728-byte index — then
> kraken2 classified zero reads in eleven minutes.** The mount is not slow and not wasteful: it
> moves only **23×** the bytes asked for, and at **7.9 random 4 KiB probes/s** it is running at
> exactly the rate raw S3 serves *one synchronous request at a time* (6.9/s). The whole failure is
> that `mmap` has no queue depth — and **~100× of the gap is recoverable on the same storage.**

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

## Two of my own arguments that the measurements killed

**Byte amplification was a red herring.** I argued a scattered 4 KiB fault pulls an 8 MiB block
(2048×), reasoning backwards from a latency. Measured by counting NIC bytes across exactly 200
probes: **17 MiB total = 91 KiB per probe = 23×.** lith's random path is byte-efficient. The
crossover I quoted (4,000–15,000 reads) was wrong too — at 91 KiB/probe, cumulative fetch reaches
1107 GiB at ~12.8M probes ≈ **425,000 reads**, so below a few hundred thousand reads the mount
moves *fewer* bytes than copying. It still loses, on latency alone.

**"Sort the queries" did not survive its own test.** Sorted 10k probes: 779 lookups/s against 784
random — nothing. Coalescing into 1 MiB spans *hurt* (767/s) and doubled bytes moved (39.1 → 84.7
MiB) for 97 merges out of 10,000. The arithmetic I should have done first: 10k probes over 1.189
TB sit **~119 MB apart**, so a 1 MiB window catches almost nothing. **The test was undersized by
~100×** — density needs ~1.1M probes (≈38,000 reads), and a real 1M-read sample would have ~40 KB
mean spacing. So sorting is *unmeasured at the scale where it would matter*, not disproven.

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

Copying the runtime set (`hash.k2d` + `taxo.k2d` + `opts.k2d`, **1107.6 GiB**) to local NVMe:
**1561 s at 0.76 GB/s for $1.0196** on `r8gd.8xlarge` ($2.3514/hr). That used only **41% of the
15 Gbps NIC**, so it is bounded by NVMe write throughput or the CLI's 64-way concurrency — the
$1.02 is a ceiling, not a floor.

It amortizes over every sample in the instance's lifetime: **~$1.02 for one sample, ~$0.01 each
for a hundred.** And NVMe, not RAM, is the destination — `x8g.24xlarge` (1536 GiB) is $9.3792/hr
against $2.3514, and `--memory-mapping` exists precisely so the table need not be resident.

**Unmeasured, and the one number still missing: kraken2's reads/min and $/sample.** The TTL killed
it mid-run on 1M reads. That was a design error, not bad luck — the progress sampler is stopped
after the copy, so unlike the copy phase this one left nothing partial behind. Instance store is
ephemeral, so recovering it means re-copying. Worth noting ~9 minutes did not finish 1M reads even
though 5,412 probes/s × 32 threads implies ~3 min; whether `mmap` faults fail to parallelise or
kraken2's startup over a 1.1 TiB mapping is expensive is **not established**.

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

Raw output in [`results/`](results/); scripts are [`canary.sh`](canary.sh),
[`readpath.sh`](readpath.sh), [`nvme.sh`](nvme.sh) and
[`concurrency.sh`](concurrency.sh). [data-movement](../../patterns/data-movement.md) is when to
reach for a mount at all; this page is the access pattern that voids it.
