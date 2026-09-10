---
tool: flye
tool_version: 2.9.6
image: quay.io/aarchbio/flye@sha256:d87ccd4e29f2995e6bbcea9f72e90f575897a5489b472111695320bd8528dc12
spawn_version: 0.104.0
---
# Flye — long-read de novo assembly

Assemble long reads into contigs — the catalog's first long-read recipe, run deterministically so its contig count can be asserted exactly.

## Run it

```bash
flye --nano-hq reads.fastq.gz --out-dir out -t 1     # → 3 contigs, largest 420,910 bp
```

One task. The recipe assembles Flye's own toy dataset (945 long reads over a ~420 kb E. coli region), pinned to the tag matching the image.

## Make it yours

| In the recipe | Swap for | What to know |
|---|---|---|
| Flye's toy `ecoli_500kb_reads` (pinned to tag 2.9.6) | your long reads | staged from Flye's own test data at the image tag, so the run reproduces the tool's own fixture — long reads are an input class the catalog lacked (reusable for medaka, racon). |
| **`--nano-hq`** | `--nano-raw`, `--pacbio-hifi`, … for your read type | must match your reads. **The raw modes OOM at 7.75 GiB here** (their error-correction stage is memory-hungry); `--nano-hq` fits. |
| **`-t 1`** | more threads for a real run | **determinism scaffolding.** Thread count changes the assembly (contig count *wanders* — [sizing](../../patterns/sizing.md)), so `-t 1` is byte-identical across runs and lets the check assert an exact count. On more threads, assert a band, not an exact number. |
| `m8g.large` (8 GiB) | keep ≥ 8 GiB | Flye's consensus stage runs `samtools sort -@4 -m1G` — a 4 GB reservation, so `c8g.large`'s 4 GiB fails on Graviton (below). |

**Leave the fixture:** it reproduces Flye's version-matched toy data deterministically and opens the long-read input class; a full genome is a longer run, not a more legible one. Leave-it.

## Shape, size, cost

One task, **`m8g.large`** (2 vCPU / 8 GiB — the catalog's first `m8g`; the 8 GiB is for Flye's internal samtools reservation, not cores). TTL 5m, cap $0.02. Assembly is ~40–60 s. Recorded command window 147s. **These timings are not compute cost** — boot and image pull dominate ([why](../../practices/what-this-does-not-cover.md)).

<details>
<summary>As shipped: the deterministic identity, the 4 GB reservation trap, the thread-wander sizing, pins, smoke check, run + verify</summary>

**Deterministic assembly that recovers the reference.** At `-t 1` the toy data gives exactly **3 contigs, 466,356 bp total**, and the largest (**420,910 bp**) recovers the 419,860 bp reference to within 1,050 bp (asserted "within 25 kb"). **This is not `test_toy.py`'s number:** that test uses the *HiFi* reads with `--pacbio-corr` (expects ~1 contig); this recipe uses the *standard* toy reads with `--nano-hq`, a different path, so 3 is the correct deterministic answer for this input+mode — what's reproduced is Flye's version-matched toy data assembled deterministically, not the test's contig count.

| observable | assertion | observed |
|---|---|---|
| contigs | exactly 3 (`--nano-hq -t 1`, deterministic) | 3 |
| total bp | exactly 466356 | 466356 |
| **largest contig vs reference** | within 25 kb of 419860 | 420910 (Δ 1050) |

**The 4 GB reservation local Docker can't show.** The recipe first ran `c8g.large` (4 GiB) and *failed on Graviton* (`samtools sort: couldn't allocate memory`). Flye hardcodes `samtools sort -@4 -m1G` — a 4 GB up-front reservation, independent of `-t 1` or the tiny data. It ran fine on local Docker at 4/3/2.5 GiB because Docker allows memory *overcommit* (the reservation is lazy); the Graviton box accounts strictly and refuses it. The sharpest case yet of "local Docker can't prove the real box" ([container path](../../practices/container-path.md)) — sized `m8g.large`, passed first try.

**Threads change the answer — the sizing dial.** Swept on a real E. coli ONT run (DRR242223) across `-t 1/2/4/8`, the contig count wandered — **10 / 12 / 11 / 14** on identical reads (non-monotonic, so you can't reason about direction, only that thread count moves the result); speed is sublinear (3.2× at 4, 4.8× at 8), knee ~4. Flye is [sizing](../../patterns/sizing.md)'s "the answer moves" case, the reason for pinning `-t 1` — most assemblers behave this way; never assert an exact count on a multi-threaded run.

**Pins** (data tier: the code's own version-matched test data — [reproduce a published fixture](../../practices/reference-from-tests.md)):

| | |
|---|---|
| image | `quay.io/aarchbio/flye@sha256:d87ccd4e…` (tag `2.9.6--py313h30571f8_1`, cosign-verified, `linux/arm64`) |
| reads / reference | Flye's toy `ecoli_500kb_reads.fastq.gz` / `.fasta` at tag 2.9.6 — `sha256:65b7cbd9…` / `de2efb0b…` |

**Run + verify.**
```sh
spawn task run --spec recipes/flye/01-assemble.task.json --wait
aws s3 ls s3://scicookbook-942542972736-us-east-1/runs/flye/r1/
```
Smoke check runs inside the task; the bucket listing is the second half ([exit 0 isn't proof](../../practices/container-path.md)). Re-running: bump the `-r1` suffix.

</details>
