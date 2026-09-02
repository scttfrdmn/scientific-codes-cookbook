# Salmon — transcript quantification, Ensembl 116 human transcriptome

Two tasks. `salmon index` builds a 1.6 GiB index over the whole human
transcriptome; `salmon quant` maps 200,000 read pairs against it and writes a
transcript-level abundance table. Both run in the same pinned image, because both
are the same tool.

## Why this is two tasks and not one

Salmon is one tool, so a single task would have been legal under the recipe shape.
It is split anyway, for a reason that has nothing to do with the shape rule: **the
index is the expensive, reusable artifact.** It takes ~3 minutes to build, it does
not depend on the reads, and every future sample quantified against Ensembl 116
wants the same one. Splitting means task 2 can be re-run — different reads,
different `-l`, a fixed typo — without rebuilding it.

That is the resumability the S3 round-trip buys. It is not free: the index crosses
S3 twice and task 2 pays a second boot. For a 3-minute index that trade is roughly
break-even on this run and clearly right the second time you use the index.

## The index travels as a tar, not as an S3 prefix

A salmon index is a directory of nine files. `spawn` does support directory staging
— a `Manifest` source ending in `/` gets `aws s3 cp --recursive`
([`wrapper.go:288`](https://github.com/spore-host/spawn/blob/main/pkg/taskproto/wrapper.go)) —
but **a directory output cannot work on the container path.** The wrapper
`mkdir -p`s the parent of every *input* destination and no output source
([`wrapper.go:87`](https://github.com/spore-host/spawn/blob/main/pkg/taskproto/wrapper.go)),
so an output's host directory is created by dockerd instead, as root, and the
container — running as the image's own user — cannot write into it.

So the index is tarred to a single flat file in `/tmp`, which is `1777` and the one
reliably writable mount. `tar -cf` then `rm -rf` the directory, so peak disk stays
under the budget. Filed as spore-host/spawn#564.

## Pins

| | |
|---|---|
| image | `quay.io/aarchbio/salmon@sha256:7134f5116644d29ab5b8fbc1c1199214842d7391094438ba6166e5631ecb7a5e` |
| | tag `2.7.0--hb05d258_0`, cosign-signed, manifest is `linux/arm64` only |
| transcriptome | Ensembl release-116 `Homo_sapiens.GRCh38.cdna.all.fa.gz`, byte for byte |
| | `sha256:683eb19310c40bf1396e4718f45afa2ce86755717c0990f47a171f535d248ea1` (183,898,799 B, 453,553 transcripts) |
| reads | ENA `ERR188026` (Geuvadis, GBR lymphoblastoid), first 200,000 pairs, 75 bp |
| | `sha256:1198ed07e41fdf6f53710dfe4eee376d978e1bf61f0d9dcdd8b7ec2ce7c5432e` / `sha256:6104ee4641702156ce3f5330b750db6fd461e597de43bb280158bc751a7e120a` |

**Data tier: stable public source with a durable id.** Ensembl `release-116/` is an
immutable path — that is what makes it pinnable. `ftp.ensembl.org/pub/current_*` is
not, and does not qualify as an input under this project's rules. The reads are a
fixed byte-offset slice of a fixed ENA accession, so `stage-inputs.sh` reproduces
them exactly.

Salmon 2.x is a Rust reimplementation (`piscem`/`sshash` under the hood), not the
C++ 1.x line. Its flag surface was read from `--help` in this exact image rather
than from memory.

## Smoke check

Measured in this image, on this input, before any launch. Values that come straight
from the input are asserted **exactly**; the one that is a property of these reads
against this transcriptome is **banded**, with real headroom for a future salmon
shifting it a point or two.

| observable | assertion | observed |
|---|---|---|
| fragments processed | exactly 200000 | 200000 |
| `quant.sf` rows | exactly 453553 | 453553 |
| TPM sum | exactly 1000000 | 1000000 |
| `sum(NumReads)` | `== num_mapped` | 189012 = 189012 |
| percent mapped | 85–99 | **94.506** |
| equivalence classes | 50000–95000 | 73772 |
| expressed transcripts (TPM>0) | 8000–30000 | 17731 |
| *index task:* references | exactly 453553 | 453553 |
| *index task:* index size | 1000000–2500000 KiB | 1643552 (~1.6 GiB) |
| *index task:* index files | ≥ 8 | 9 |

`sum(NumReads) == num_mapped` is the useful one: it is an internal-consistency
identity, so it catches a `quant.sf` that was truncated mid-write — the exact
failure a row count alone would miss if the truncation landed on a zero-count tail.

Salmon 2.x reports this configuration as `deterministic` in its own log and
reproduced these counts exactly across runs, which is why so much of the table is
exact rather than banded.

## Resources, and what the timings mean

| task | shape | measured work |
|---|---|---|
| `01-index` | 8 vCPU / 16 GiB, `c8g`, TTL 20m | 2m53s |
| `02-quant` | 8 vCPU / 16 GiB, `c8g`, TTL 20m | 9.6s |

`--ramLimit 8` is salmon's own default, stated explicitly so the box's memory
request and the tool's budget are visibly the same number rather than a coincidence.
The index build's actual peak was **2,325 MB RSS**, well under that budget — which is
why the family is `c8g` (compute-bound, 2 GiB/vCPU) rather than a memory-heavier one.
Truffle resolves this to `c8g.2xlarge`.

TTL is 20m against ~9 minutes of expected wall time (boot, image pull, staging, then
3 minutes of work) — a bit over 2× headroom. TTL is also the cost cap, so it is not
set loose "just in case": a run that hits TTL instead of completing is a failure by
this project's rules, and a TTL far above the work is just a larger blast radius.

**These timings are not compute cost.** Each task pays instance boot, image pull
and S3 staging before the tool starts — on recipe #1 that overhead was ~5 minutes
against 78 seconds of actual work. Boot dominates every recipe in this cookbook.
Read the numbers above as "does this run and how long does the science take", never
as a benchmark.

Disk: 8 GiB root, ~6.1 GiB usable. Task 1 peaks at ~3.4 GiB (184 MiB input +
1.6 GiB index + 1.6 GiB tar); task 2 at ~3.3 GiB, and drops to ~1.7 GiB because it
deletes the tar right after extracting it.

## Running it

```sh
spawn task run --spec recipes/salmon/01-index.task.json --wait
spawn task run --spec recipes/salmon/02-quant.task.json --wait
```

Then **check the bucket**, every time:

```sh
aws s3 ls s3://scicookbook-942542972736-us-east-1/runs/salmon/r1/
```

`--wait` exiting 0 does **not** prove the outputs exist: a task whose declared
output fails to stage is still recorded `completed` / `exit_code: 0`
(spore-host/spawn#561). The smoke check runs *inside* the task, where it can fail
the task; the bucket listing is the second half of the same check.

**Re-running one task.** `task_id` is fixed in the spec, so a re-run overwrites the
previous `completion.json` and `command.log` under
`s3://spawn-results-<account>-<region>/tasks/<task_id>/`. Bump the `-r1` suffix in
both `task_id` and the output prefix to keep both records.
