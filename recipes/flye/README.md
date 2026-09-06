# Flye — long-read de novo assembly of a small E. coli region

One task. Flye assembles a set of long reads into contigs; the smoke check confirms the
assembly is deterministic and that its largest contig recovers the reference region. This
is the catalog's **first long-read recipe** — a new input class.

> **What this recipe does and does not cover.** It assembles Flye's own committed toy
> dataset (a ~420 kb E. coli region, 945 long reads) and checks the result is deterministic
> and recovers the reference length — enough to prove Flye assembles correctly on Graviton4.
> Not a benchmark, not a full genome. It is **not** a bit-for-bit reproduction of Flye's
> published `test_toy.py` number (that test uses the *HiFi* read file with `--pacbio-corr`;
> this recipe uses the standard toy reads with `--nano-hq` — see below).

## Long reads, and two things the run surfaced

The input is Flye's own `ecoli_500kb_reads.fastq.gz` (945 long reads over a 419,860 bp E.
coli reference), **pinned to the tag matching the image (2.9.6)** — the test-suite-staging
pattern (`recipes/siesta`/`recipes/vina`): a code's committed test data, version-matched, so
the recipe reproduces the tool's own fixture rather than an arbitrary input. This opens
**long reads as an input class the catalog lacked** — reusable for medaka, racon, and
minimap2's actual long-read mode (the `recipes/minimap2` recipe uses short reads).

Three findings from validating it:
- **`--nano-hq`, not the raw modes.** `--pacbio-raw` and `--nano-raw` both **OOM at 7.75 GiB**
  (the raw modes' error-correction stage is memory-hungry); `--nano-hq` (the high-quality
  long-read mode, less correction) is what this recipe uses.
- **`-t 1` for determinism.** Flye's contig count varies with thread count (thread scheduling
  changes the assembly graph — the same class of nondeterminism `recipes/iqtree` documents):
  `-t 4` gave 2 *or* 3 contigs across runs, while **`-t 1` is deterministic** (3 contigs,
  byte-identical across three local runs). Single-threaded is the reproducible choice; the
  compute is seconds either way.
- **8 GiB (`m8g.large`), not 4 — and local Docker could not show why.** The recipe first ran on
  `c8g.large` (4 GiB) and **failed on Graviton**: `samtools sort: couldn't allocate memory for
  bam_mem`. Flye's consensus stage hardcodes `samtools sort -@ 4 -m 1G` — a **4 GB up-front
  reservation**, independent of `-t 1` or the tiny dataset. It ran fine on local Docker at 4, 3,
  even 2.5 GiB, because Docker allows memory **overcommit**: the 4 GB reservation is lazy and
  never faults for this data. The Graviton box accounts strictly and refuses the reservation. So
  it *is* a memory limit — but a reservation-vs-usage one that local Docker structurally cannot
  reproduce (the sharpest case yet of "local Docker can't prove the real box" — the same lesson
  as the sticky-bit `rm` trap, now for memory). Sized `m8g` (balanced, 8 GiB at 2 vCPU — flye
  `-t 1` doesn't need more cores, it needs headroom for the reservation); the first `m8g.large`
  run passed.

## Identity: deterministic assembly that recovers the reference

- **Contig count = 3 (exact, deterministic at `-t 1`).** A broken assembly gives a different
  count; thread nondeterminism is removed by `-t 1`. **Be precise about what "reproduces
  Flye's test" means here:** Flye's `test_toy.py` expects ~1 contig, but it runs the *HiFi*
  reads with `--pacbio-corr`; this recipe runs the *standard* toy reads with `--nano-hq`, a
  different path, so 3 is the correct, deterministic answer for *this* input+mode, not a
  mismatch with the test. What's reproduced is Flye's **version-matched toy data** assembled
  deterministically — not `test_toy.py`'s contig number. The 3 are the ~420 kb region in one
  contig plus two short fragments (below); the recipe asserts that structure, not "== 1".
- **Total assembly = 466,356 bp (exact).**
- **Largest contig recovers the reference.** The largest contig is **420,910 bp** against the
  **419,860 bp** reference — a difference of **1,050 bp (0.25%)**, asserted as "within 25 kb".
  This is the biological check: a correct assembler reconstructs the ~420 kb region in one
  contig (the other two are short repeat/redundant contigs). A band, justified by "recovers
  the region", not a fitted tolerance.

## Pins

| | |
|---|---|
| image | `quay.io/aarchbio/flye@sha256:d87ccd4e29f2995e6bbcea9f72e90f575897a5489b472111695320bd8528dc12` |
| | tag `2.9.6--py313h30571f8_1`, Flye 2.9.6, cosign-verified (`sign-existing.yml`), `linux/arm64` |
| reads | Flye's toy `ecoli_500kb_reads.fastq.gz`, pinned to tag 2.9.6 — `sha256:65b7cbd9…` (945 reads) |
| reference | Flye's toy `ecoli_500kb.fasta` — `sha256:de2efb0b…` (419,860 bp) |

**Data tier: the code's own version-matched test data.** Staged from the Flye repo at the
`2.9.6` tag — the same discipline as SIESTA pinning its pseudopotential to the `5.4.2` tag.

## Smoke check

Measured in the pinned image (validated verbatim, `--user 1000:1000`).

| observable | assertion | observed |
|---|---|---|
| contigs | exactly 3 (`--nano-hq -t1`, deterministic) | 3 |
| total bp | exactly 466356 | 466356 |
| **largest contig vs reference** | within 25 kb of 419860 | 420910 (Δ 1050) |

## Resources, and what the timings mean

**2 vCPU / 8 GiB, `m8g` (resolves to `m8g.large`) — balanced, the catalog's first `m8g`.** TTL 5m,
cap $0.02. Not compute-bound (assembly is ~40–60 s) and not thread-bound (`-t 1`) — it's the 8 GiB
that matters, for flye's internal `samtools sort -@ 4 -m 1G` 4 GB reservation (see the third
finding above). `c8g.large`'s 4 GiB was too tight and **failed on Graviton**; `m8g.large` passed
first try.

**These timings are not compute cost.** Boot, the Docker install, and pulling the Flye image
are much of the task. The recorded `m8g.large` run's window was **147s** (00:39:28 → 00:41:55 UTC),
3 contigs / 466,356 bp, largest 420,910. TTL **retightened from that run**: 10m → **5m**,
`cost_limit` $0.03 → $0.02. Disk is trivial.

## Running it

No `stage-inputs.sh` — Flye's toy data is already staged under `inputs/flye/` (pinned to 2.9.6).

```sh
spawn task run --spec recipes/flye/01-assemble.task.json --wait
```

Then **check the bucket**:

```sh
aws s3 ls s3://scicookbook-942542972736-us-east-1/runs/flye/r1/
```

Expect `assembly.fasta`, `assembly_info.txt`, `smoke-check.txt`. The smoke check runs inside
the task; the bucket listing is the second half of it.

**Re-running.** `task_id` is fixed; bump the `-r1` suffix in `task_id` and the output prefix.
