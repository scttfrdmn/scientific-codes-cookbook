# Picard — MarkDuplicates on a fixed BAM, exact duplication metrics

One task. Picard's `MarkDuplicates` marks PCR/optical duplicates in a coordinate-sorted BAM
and writes a metrics file; the smoke check asserts its **exact** duplication counts. On a
fixed BAM the tool is deterministic, so every number is exact-or-wrong — no band.

> **What this recipe does and does not cover.** It runs one Picard tool on one small BAM and
> checks its metrics reproduce exactly — enough to prove Picard (and its JVM) work on
> Graviton4. Not a benchmark; no full-genome dedup or a metrics pipeline.

## Identity — exact-or-wrong, deterministic

`MarkDuplicates` on a fixed, coordinate-sorted BAM is deterministic (duplicate status is a
function of alignment coordinates + flags, not of run order), so the metrics reproduce
exactly:

- read pairs examined **52189**, unpaired reads examined **122**
- **read-pair duplicates 5012**, unpaired-read duplicates **25**
- percent duplication **0.096163**

No cross-code check: Picard has no natural sibling to run on the same bytes, so per the
like-with-like rule the exact metric is the honest claim rather than a manufactured
comparison. (This is the same reasoning as `recipes/fastp`'s conservation identity.)

## Pins

| | |
|---|---|
| image | `quay.io/aarchbio/picard@sha256:c6a742e8277b9010df9aa3b9a6bb40651792ff319627cc1c2bf8a70ac633e6bd` |
| | tag `3.5.0--hdfd78af_0`, Picard 3.5.0 (Java), cosign-verified (`publish.yml@refs/heads/main`), `linux/arm64` |
| input | the shared 30× fixture `inputs/highcov/HG00096.chr20_2.0-2.4Mb.30x.bam` (+ `.bai`) |

**Data tier: reused shared fixture.** The 30× fixture staged for `recipes/bcftools` /
`recipes/freebayes` (1000G NYGC, chr20:2.0–2.4 Mb, mean 35.2×) — its reuse here (and by the
assembly recipes) is why it was staged as a shared asset. Nothing new to stage;
`recipes/bcftools/stage-inputs.sh` documents its provenance.

## Smoke check

Measured in the pinned image (validated verbatim, `SMOKE CHECK PASSED`, identical on re-run).

| observable | assertion | observed |
|---|---|---|
| read pairs examined | exactly 52189 | 52189 |
| unpaired reads examined | exactly 122 | 122 |
| **read-pair duplicates** | exactly 5012 | 5012 |
| unpaired-read duplicates | exactly 25 | 25 |
| **percent duplication** | exactly 0.096163 | 0.096163 |

`LC_ALL=C` silences a harmless Picard-wrapper locale warning. The staged BAM is never
removed (sticky-`/tmp` EPERM rule); the marked BAM stays on the box — the metrics are the
evidence and the only staged output.

## Resources, and what the timings mean

2 vCPU / 4 GiB, `c8g` (resolves to `c8g.large`), TTL 5m, cap $0.02. MarkDuplicates on this
BAM is **~2 s** (the JVM start dominates the compute).

**These timings are not compute cost.** Boot, the Docker install, and pulling the Picard
image are the whole task. Recorded window **76s** (21:44:42 → 21:45:58 UTC), 5,012 duplicates
exact. TTL/cap are already minimal; retighten only if the JVM needs more
than 4 GiB on the first real run (it didn't locally). Disk is trivial.

## Running it

No `stage-inputs.sh` — reuses the shared 30× fixture.

```sh
spawn task run --spec recipes/picard/01-markdup.task.json --wait
```

Then **check the bucket**:

```sh
aws s3 ls s3://scicookbook-942542972736-us-east-1/runs/picard/r1/
```

Expect two objects (`dup_metrics.txt`, `smoke-check.txt`). The smoke check runs inside the
task; the bucket listing is the second half of it.

**Re-running.** `task_id` is fixed; bump the `-r1` suffix in `task_id` and the output prefix.
