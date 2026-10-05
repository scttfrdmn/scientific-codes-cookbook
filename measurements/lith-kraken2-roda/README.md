# A 1.2 TB mount costs nothing to build and can still be unusable

> **`lith index build` covered 1.206 TB of RODA in about a second, into a 728-byte index — and
> then kraken2 classified zero reads in eleven minutes.** The split is random-versus-sequential,
> measured on the same file: **146 MB/s** streaming, **141 ms per 4 KiB probe**. The mount looks
> equally healthy in both cases, which is the trap.

The RODA kraken2 RefSeq-Complete v205 database is **1.206 TB** in `us-west-2`; `hash.k2d` alone is
`1,189,091,671,800` bytes. Nothing can copy that, so this is the case where a mount is forced
rather than chosen — the adversarial test for [lith](https://github.com/scttfrdmn/lith), not a
flattering one. STAR's 28.6 GiB result was a *sequential* index load; `kraken2 --memory-mapping`
does *random* page faults over a TiB-scale hash table and touches a sliver of it.

## What the mount costs: nothing, and that holds at TB scale

| | |
|---|---|
| `lith index build` over 1.206 TB | **~1 s** |
| index size | **728 bytes** |
| files visible through the mount | 9 of 9 |
| `stat` on `hash.k2d` | `1,189,091,671,800` bytes |

The metadata-only property has no caveat at this size. That was the open question and it is answered.

## What the reads cost: it depends entirely on the pattern

lith 1.6.0, `r8g.xlarge` (Graviton4, 4 vCPU / 31 GiB), **instance and bucket both `us-west-2`**:

| rung | result |
|---|---|
| sequential, `taxo.k2d` (179 MB) | **107 MB/s** |
| sequential, `hash.k2d` (1.08 TiB), 256 MiB read | **146 MB/s** |
| `aws s3 cp` same file, same box, no mount | 170.9 MiB/s |
| **random 4 KiB `pread`, 200 probes** | **7.1/s — 141 ms each** |

**Sequential is healthy** — 62–86% of raw `s3 cp`, so there is no general mount overhead to speak
of. The entire problem is the random rung. 141 ms for 4 KiB is a full block fetch per miss
(8 MiB ÷ 0.141 s ≈ 56 MB/s), which is
[lith#232](https://github.com/scttfrdmn/lith/issues/232) — characterized there and closed as
"lith has no lever," which this reproduces at ~1000× the object size with a real tool instead of
a synthetic probe. kraken2 needs tens of thousands of probes for 1,000 reads, so the job is hours.
It never fails; it just never finishes.

**One rung came back invalid and is reported rather than quietly dropped.** The
"inside a container" sequential read returned 8.2 GB/s — a page-cache hit from the host `dd` that
preceded it. It measures nothing. Whether a bind-mounted FUSE mount loses kernel readahead is
still untested, and it matters beyond this page, because recipes always run in containers.

## The confound that nearly became a filed bug

The first two runs placed the instance in `us-west-1` against the `us-west-2` bucket. Nobody asked
for that and lith mounted it without comment. Cross-region, the same workload pulled **1.52 MiB/s**
steady — 983 MiB over 645 s, linear across 33 samples — against 107–146 MB/s in-region.

Two instances went into theorising about readahead before anyone ran
`get-bucket-location`. **A performance number from a cross-region read is not a performance
number**, and the control that settled it was one line of the probe: `aws s3 cp` of the same file
on the same box. Reach for the no-mount baseline first, not last. Filed as
[lith#362](https://github.com/scttfrdmn/lith/issues/362), together with the observability gap —
lith knows the bucket region, the instance region, and its own per-fault latency, and surfaced
none of the three.

## Two process notes that cost real money

**A probe must stream its result, not report it.** `spawn launch --command` does not stage logs to
S3 — the spawn#643 pre-stop flush is a `task run` feature — so the first design returned *nothing*
when the run was still going at TTL. Pushing a sampled curve to S3 as it goes turned a lost run
into the most useful artifact here. Relatedly, kraken2's `--output` was `/dev/null`, which threw
away the only incremental progress signal it has.

**Check the region before concluding a box died.** `InvalidInstanceID.NotFound` was read twice as
"the instance terminated" when it meant "you are querying `us-west-2` and spawn launched into
`us-west-1`." Nothing had died; kraken2 was still faulting. Two canaries also ran concurrently
against the same S3 result key, clobbering each other.

## What to take from it

For an immutable TB-scale reference, the question is never "is the mount affordable" — it always
is. It is **what shape your reads are.** Stream it or read it sparsely at block granularity and
lith is within spitting distance of raw S3; probe it randomly at 4 KiB and no amount of index
cleverness helps, because every miss is a round trip.
[data-movement](../../patterns/data-movement.md) is when to reach for a mount at all;
this page is the access pattern that voids it.

Raw curves in [`results/`](results/) — `canary-cross-region.txt` (the 33-sample fault curve) and
`readpath-in-region.txt` (the four rungs). Scripts are [`canary.sh`](canary.sh) and
[`readpath.sh`](readpath.sh).
