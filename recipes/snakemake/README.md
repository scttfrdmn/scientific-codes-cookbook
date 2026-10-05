---
tool: snakemake
tool_version: "9.27.0 (executor plugin 0.4.0)"
shape: pipeline
depends_on: stage:mash
images:
  seqkit: quay.io/aarchbio/seqkit@sha256:5478aaad4dd7bf7d7f02eee168ee3ad90d17b6729ab5e889a9385b4458cde7c5
spawn_version: 0.122.0
last_verified: 2026-10-05
---
# Snakemake — a wildcard fan-out where the job count comes from the data (Shape F)

Hands a Snakemake workflow to the `spawn` executor, which dispatches each job to its own ephemeral instance. Four genomes in, four parallel jobs, one join — and the join checks every shard against NCBI. For anyone running Snakemake on spore.host.

## Run it

```bash
make stage RECIPE=snakemake      # once: 4 genomes as separate objects + NCBI's lengths
B=$(make -s print-bucket)
mkdir -p /tmp/smwork && cp recipes/snakemake/Snakefile /tmp/smwork/ && cd /tmp/smwork

snakemake -s Snakefile --config bucket="$B" \
  --executor spawn --shared-fs-usage none \
  --spawn-region us-west-2 --spawn-ttl 30m --spawn-cost-limit 0.05 \
  --default-storage-provider s3 --default-storage-prefix "s3://$B/runs/snakemake/r1" \
  --jobs 2
```

Five remote jobs: `stats` × 4 (one per genome, from the wildcard) then `aggregate` × 1.

## Make it yours

| In the recipe | Swap for | What to know |
|---|---|---|
| 4 genomes | your samples, any number | the job count is **discovered from the wildcard**, not written in the Snakefile — that's the difference from [nf-spawn](../nf-spawn/README.md)'s fixed DAG. |
| `--shared-fs-usage none` | **keep it** | **load-bearing.** Without it the node receives a Snakefile path relative to *your* machine and dies before any work ([#19](https://github.com/spore-host/snakemake-executor-plugin-spawn/issues/19)). |
| `--jobs 2` | more, with care | `--jobs N` on one rule shares a single completion record ([#19](https://github.com/spore-host/snakemake-executor-plugin-spawn/issues/19)) — and [truffle#175](https://github.com/spore-host/truffle/issues/175) refuses concurrent Graviton launches carrying a cost limit. |
| `docker run <digest>` in the shell | your tool's image | the executor emits no `container` field, so **the rule names the digest** — see below. |

**Leave the fixture.** Four complete bacterial genomes, each a single contig, is the smallest thing that makes the fan-out *checkable*: NCBI publishes each assembly's length, so every shard has an independent answer and their sum is a conservation identity. **Scale it** by adding samples — the DAG widens on its own.

## Shape, size, cost

Shape F costs **per job**, not one flat instance. Five instances, auto-sized from each rule's `threads`/`mem_mb` (the join landed on `t4g.small`); measured **112 s** for a `stats` job and **64 s** for the join, inside a **15m43s** wall at `--jobs 2`. Caps were `--spawn-cost-limit 0.05` per job, so **$0.25 worst case** and about **$0.01 actual**. Nearly all of each job is boot, `dnf install docker`, and image pull — **these timings are not compute cost** ([layout](../../patterns/layout-and-effective-cost.md)).

<details>
<summary>As shipped: four NCBI checks plus a sum identity, and why the rule runs docker itself</summary>

### The checks

| observable | assertion | observed |
|---|---|---|
| source tar | matches the sha256 `recipes/mash` pinned | `6fa8884c…` |
| fan-out width | exactly 4 shards joined | **4** |
| **each genome's length** | **== NCBI's recorded assembly length** | **4/4 OK** |
| **fan-out total** | **== the sum of the four published lengths** | **18,220,173 == 18,220,173** |
| each genome | one contig (complete chromosomes) | `num_seqs 1` each |
| workflow | every step completes | **6 of 6 steps, exit 0** |

```text
sample            organism                     seqkit_sum_len  ncbi_length  match
GCF_000005845.2   Escherichia_coli             4641652         4641652      OK
GCF_000006945.2   Salmonella_enterica          4951383         4951383      OK
GCF_000009045.1   Bacillus_subtilis            4215606         4215606      OK
GCF_000195955.2   Mycobacterium_tuberculosis   4411532         4411532      OK
TOTAL             -                            18220173        18220173     OK
```

**The per-shard checks are against a published number, not internal consistency** — `labels.tsv` travels inside the pinned tar carrying NCBI's own length for each assembly, the same sourcing move as [reference-from-tests](../../practices/reference-from-tests.md). The total is then a conservation identity: a fan-out that lost, duplicated or mis-joined a shard cannot reproduce it.

**And that identity earned its place on this run.** The executor assigns one `task_id` per *rule*, so all four `stats` jobs shared a single completion record and the poll reported whichever record it found to every shard ([#19](https://github.com/spore-host/snakemake-executor-plugin-spawn/issues/19), filed from this run). A shard that failed could have been reported as succeeded. The data check is what would have caught it — which is the catalog's [exit codes are not evidence](../../practices/container-path.md) rule arriving from the engine side.

### Why the rule runs `docker` instead of using `container:`

Pinning by digest is not optional here, and neither mechanism Snakemake would normally use is available:

- **The executor emits no `container` field.** 0.3.0 added a passthrough; 0.4.0 **removed** it, because routing a Snakemake re-invocation through `spec.container` lands it inside a one-tool image that has no Snakemake ([#16](https://github.com/spore-host/snakemake-executor-plugin-spawn/issues/16), resolved by removal rather than patch).
- **Snakemake's own `container:` needs apptainer**, which stock AL2023 lacks, and the plugin's install preamble is not user-settable.

So the rule's shell installs docker idempotently and runs the cosign-verified image by `@sha256:`. The digest lives in the Snakefile, which is the thing a reader has to be able to check.

### Pins (data tier: derived from RODA, via recipes/mash)

| | |
|---|---|
| genomes | re-cut from `inputs/genomes20/genomes20.tar`, pinned `6fa8884c…` by [mash](../mash/README.md) — nothing new fetched |
| truth | `labels.tsv` from inside that tar: NCBI assembly lengths |
| seqkit | `@sha256:5478aaad…` (2.13.0), reused from [seqkit](../seqkit/README.md) |

Four separate objects rather than the tar, because a tar would collapse four jobs into one — the wildcard is the unit of parallelism.

### Run + verify

```sh
make stage RECIPE=snakemake
# ... the invocation above ...
aws s3 cp "s3://$B/runs/snakemake/r1/summary.tsv" -
```

`summary.tsv` is the artifact that settles it; the workflow exits non-zero if any shard disagrees with NCBI or the total is off.

### Two rough edges worth knowing

**Run from a directory Snakemake can walk.** Starting it in this repo's root failed with `FileNotFoundError: '.claude/scheduled_tasks.lock'` — Snakemake inventories the working directory and tripped on a stale deleted file. A clean workdir avoids it.

**The node's pip install ends in a self-declared conflict** — `snakemake 9.27.0 requires packaging<26,>=24.0, but you have packaging 26.3` — because `snakemake-storage-plugin-s3` pulls `packaging` over snakemake's own pin. Non-fatal on this workflow; reported with [#19](https://github.com/spore-host/snakemake-executor-plugin-spawn/issues/19).

</details>
