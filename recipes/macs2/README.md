# MACS2 — ChIP-seq peak calling, treatment vs matched input control

One task. MACS2 calls peaks from a CTCF ChIP-seq treatment **against its matched input
control** — the enrichment model, which is how MACS2 is actually run — and the smoke check
asserts a real enriched peak set on chr20.

> **What this recipe does and does not cover.** It calls peaks on one chromosome of a real
> CTCF ChIP-seq experiment with its matched control — enough to prove MACS2's peak-calling
> works on Graviton4 and produces a real, enriched result. Not a benchmark; no differential
> binding or motif analysis.

## Why a real ChIP experiment with a control (not the WGS fixture)

MACS2 is the one Batch-4 tool the 30× WGS fixture could **not** serve: run on flat WGS
coverage it finds **0 peaks** — correct behaviour (no enrichment to call), but a *degenerate*
identity (asserting "0 peaks" would pass even if peak-calling were broken — the empty-output
trap CLAUDE.md rejects). Peak-calling only means something on data with real enrichment. So
this recipe uses a genuine **CTCF ChIP-seq treatment + matched input control** — CTCF is a
sharp-peak factor with strong signal, and the control is what makes the call treatment-vs-input
enrichment rather than peaks-vs-background. The result is a property of the enrichment, not of
threshold luck.

## Identity: a real enriched peak set

- **Peaks = exactly 1390.** MACS2 `callpeak -t CTCF -c input -f BAM -g hs` on chr20 calls
  **1390** peaks — and the count is **bit-identical across architectures**: exactly 1390 on
  local Apple arm64 *and* on Graviton4. The recipe first asserted a wide 1000–2000 band on the
  theory that cross-arch floating-point at the q<0.05 cutoff might shift borderline peaks by a
  handful — but the Graviton run returned 1390 exactly, so that jitter was hypothetical, not
  real: MACS2 joins the recipes that are exactly reproducible given a pinned image and fixed
  input. So the assertion is **exact (`== 1390`)**, not a band — the strongest honest form.
  (What made the *first* band legitimate was different from a tolerance: it was a
  correct-vs-degenerate discriminator — a working matched-control ChIP gives a large enriched
  set, the WGS degenerate case gave ~0 — and its job was to catch that failure mode, which the
  exact count now does more sharply.)
- **Deterministic** — model-based call (tag size 76 bp, fold [5,50], q<0.05), reproducible
  byte-for-byte within and across architectures.

## Pins

| | |
|---|---|
| image | `quay.io/aarchbio/macs2@sha256:ca577fd2e65087538f4d51c3abf263453c9ec1485621d0e12f3a7822c5ab7a92` |
| | tag `2.2.9.1`, cosign-verified (`sign-existing.yml`), `linux/arm64` |
| treatment | ENCODE **ENCFF933NSJ** (CTCF ChIP, HCT116, GRCh38), chr20 subset — `sha256:32db48ec…` (864,347 reads) |
| control | ENCODE **ENCFF768XTH** (matched input), chr20 subset — `sha256:a33c376a…` (1,297,910 reads) |

**Data tier: stable public source with a durable id (ENCODE accessions).** Both BAMs were
range-subset to chr20 from the full ENCODE alignments (the 30×-fixture extraction pattern) and
sha256-pinned. Single-end reads → `-f BAM`. **Reusable:** a matched ChIP+control pair serves any
peak-caller comparison (MACS2 vs SEACR/epic2) or a future ChIP-QC recipe.

## Smoke check

Measured in the pinned image, before any launch.

| observable | assertion | observed |
|---|---|---|
| **peaks** | exactly 1390 (CTCF chr20 vs input; bit-identical local + Graviton) | 1390 |
| enrichment real | ≥ 1000 (not the degenerate 0-peak WGS case) | yes |

## Resources, and what the timings mean

2 vCPU / 4 GiB, `c8g` (resolves to `c8g.large`), TTL 5m, cap $0.02. The call is seconds on
~2.2M chr20 reads.

**These timings are not compute cost.** Boot, the Docker install, and pulling the MACS2 image
are the whole task. The recorded run's window was **80s** (01:06:09 → 01:07:29 UTC), exactly
1390 peaks (matching local). TTL/cap already minimal. Disk is trivial (two chr20 BAMs ≈ 110 MB).

## Running it

No `stage-inputs.sh` — the chr20 subsets are already staged (see Pins for the ENCODE accessions).

```sh
spawn task run --spec recipes/macs2/01-callpeak.task.json --wait
```

Then **check the bucket**, every time:

```sh
aws s3 ls s3://scicookbook-942542972736-us-east-1/runs/macs2/r1/
```

The smoke check runs *inside* the task; the bucket listing is the second half of it. Expect two
objects (`peaks.narrowPeak`, `smoke-check.txt`).

**Re-running.** `task_id` is fixed; bump the `-r1` suffix in both `task_id` and the output prefix
to keep both records.
