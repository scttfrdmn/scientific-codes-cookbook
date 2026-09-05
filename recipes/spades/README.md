# SPAdes — de novo genome assembly, cross-validated by QUAST

One task in a three-recipe chain. SPAdes assembles the staged short reads into contigs;
the smoke check asserts SPAdes' exact, deterministic assembly, and `recipes/quast`
independently measures this assembly (and MEGAHIT's) as the cross-validator.

> **What this recipe does and does not cover.** It assembles a ~400 kb chr20 region from
> ~35× reads into contigs and asserts the exact contig count and total length — enough to
> prove SPAdes runs deterministically on Graviton4. Not a benchmark; not a whole-genome
> assembly (the input is a subsampled region).

## Deterministic assembly is the identity

SPAdes with a fixed thread count (`--isolate -t 4`) is **deterministic** — verified by
assembling the same reads twice and getting an identical contig set (237 contigs, 428,168 bp
both times). So the recipe asserts those exact integers: a non-deterministic or broken
assembly changes them. SPAdes and MEGAHIT produce *different* contig sets on the same reads
(different algorithms — that is expected and correct); the two are not compared to each
other, they are each measured independently by QUAST. See `recipes/quast`.

## Pins

| | |
|---|---|
| image | `quay.io/aarchbio/spades@sha256:f8b7ad9acda742d695be9176c1fec0e9a33579a6a19294d3d2a3516ade3de81c` |
| | tag `4.3.0`, SPAdes 4.3.0, cosign-verified (`publish.yml`), `linux/arm64` |
| reads | `inputs/highcov/HG00096.chr20_2.0-2.4Mb.30x_reads_{1,2}.fq.gz` (51,933 pairs) |

**Data tier: reused shared fixture.** The reads are `samtools fastq` of the 30× fixture
(`recipes/bcftools/stage-inputs.sh`) — its **fourth reuse** (bcftools, freebayes, and the
depth-sensitive callers were the first three). Pinned by sha256, re-verified on the box.

## Smoke check

| observable | assertion | observed |
|---|---|---|
| contigs | exactly 237 (deterministic, `-t 4`) | 237 |
| total length | exactly 428168 bp | 428168 |

Exact-or-wrong: the numbers are a deterministic function of fixed reads + fixed threads.

## Resources, and what the timings mean

4 vCPU / 8 GiB, `c8g` (resolves to `c8g.xlarge`), TTL 5m, cap $0.02. Assembly was **~74 s**
locally, no memory pressure (fit well within 8 GiB).

**These timings are not compute cost.** Boot, the Docker install, and pulling the SPAdes
image are much of the task. The recorded run's window was **113s** (21:43:58 → 21:45:51 UTC),
237 contigs / 428,168 bp exact. TTL was **retightened from that first real run**: 10m → **5m**,
`cost_limit` $0.03 → $0.02; the recorded run used the original 10m. Disk is modest.

## Running it

```sh
spawn task run --spec recipes/spades/01-assemble.task.json --wait
aws s3 ls s3://scicookbook-942542972736-us-east-1/runs/spades/r1/
```

Expect `contigs.fasta` + `smoke-check.txt`. `recipes/quast` consumes `contigs.fasta` from
this prefix, so run SPAdes and MEGAHIT before QUAST.

**Re-running.** Bump the `-r1` suffix in `task_id` and the output prefix.
