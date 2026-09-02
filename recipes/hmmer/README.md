# HMMER — profile HMM search, Pfam 38.2 against the human proteome

One task. `hmmsearch` scores the first 200 Pfam 38.2 profile HMMs against all
382,428 Ensembl 116 human protein sequences and writes a per-hit table.

One tool, so one task — there is nothing here to split. The multi-task shape shows
up when a recipe needs two *different* tools (see `recipes/bwa-samtools`), not as a
rule to be satisfied for its own sake.

## Pins

| | |
|---|---|
| image | `quay.io/aarchbio/hmmer@sha256:ecae1325123858761f0d744aacae948a89368440fd6d91f69c178402294fc792` |
| | tag `3.4--hfe13ca0_4`, cosign-signed, manifest is `linux/arm64` only |
| models | Pfam `releases/Pfam38.2/Pfam-A.hmm.gz`, first 200 complete models |
| | `sha256:c686564b490098a9de36c6738206496b746f69739fb11855cfb9cdad958e0d4b` (16,048,150 B) |
| targets | Ensembl release-116 `Homo_sapiens.GRCh38.pep.all.fa.gz`, byte for byte |
| | `sha256:9b43da92651b35814597af6a8b18f500b768679a49fa4678224f384917ce7668` (23,319,936 B, 382,428 proteins) |

**Data tier: stable public source with a durable id.** Both upstream paths are
versioned and therefore immutable — Pfam `releases/Pfam38.2/` and Ensembl
`release-116/`. Their mutable siblings (`Pfam/current_release/`,
`ensembl/pub/current_*`) are deliberately not used: they cannot be pinned, so by
this project's own rule they do not qualify as inputs.

The models are cut from the front of the release rather than sampled, because an
HMMER3 model ends with a line that is exactly `//` — so truncating after the 200th
such line yields a complete, valid file with no parsing of the format required.
`stage-inputs.sh` then checks that `NAME` and `ACC` counts both equal 200, so a
mis-cut cannot pass silently.

**This is a subsample, not all of Pfam.** 200 of ~24,000 families. Fine for "does
HMMER run and produce real hits"; not an annotation of the human proteome.

## Smoke check

Measured in this image, on this input, before any launch.

| observable | assertion | observed |
|---|---|---|
| models searched | exactly 200 | 200 |
| target sequences | exactly 382428 | 382428 |
| `tblout` hits | 40000–80000 | **59840** |
| models with ≥1 hit | 140–200 | 168 |
| hits at E < 1e-10 | 5000–20000 | 10050 |
| `hmmsearch` trailer | exactly `[ok]` | `[ok]` |

`hmmsearch` is deterministic: the same version over the same two inputs gives the
same numbers every time. The counts are banded anyway, because a future HMMER
release may legitimately shift them — the bands are wide enough for that and tight
enough that an empty, truncated or wrong-database search fails.

The trailer check is the one that earns its place. `hmmsearch` writes a literal
`[ok]` as the last line of its output **only** on clean completion, so it catches a
search killed part-way — which would otherwise leave a well-formed `tblout` holding
a partial answer, and a hit count that could still land inside the band.

32 of 200 models found no hit. That is expected: many Pfam families are bacterial,
viral or plant-specific and have no human member.

## Resources, and what the timings mean

8 vCPU / 16 GiB, `c8g` (resolves to `c8g.2xlarge`), TTL 20m. `hmmsearch --cpu 8`. Measured work: **3m12s** on 4
threads locally; memory stays around 1–2 GiB, so the box is sized for the thread
count, not the footprint.

**These timings are not compute cost.** Each task pays instance boot, image pull
and S3 staging before the tool starts — on recipe #1 that overhead was ~5 minutes
against 78 seconds of work. Boot dominates every recipe here.

Disk is comfortable: 39 MiB of inputs, ~160 MiB decompressed proteome, and 99 MiB
of output against ~6.1 GiB usable. `hmmsearch` cannot read a gzipped sequence
database, so the task decompresses first; `gzip` is a base-image utility, not a
second scientific tool. The 75 MiB per-domain report is gzipped before stage-out.

## Running it

```sh
spawn task run --spec recipes/hmmer/01-search.task.json --wait
aws s3 ls s3://scicookbook-942542972736-us-east-1/runs/hmmer/r1/
```

`--wait` exiting 0 does **not** prove the outputs exist: a task whose declared
output fails to stage is still recorded `completed` / `exit_code: 0`
(spore-host/spawn#561). The smoke check runs *inside* the task, where it can fail
the task; the bucket listing is the second half of the same check.

**Re-running.** `task_id` is fixed, so a re-run overwrites the previous
`completion.json` and `command.log`. Bump the `-r1` suffix in both `task_id` and the
output prefix to keep both records.
