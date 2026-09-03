# STAR — spliced RNA-seq alignment against GRCh38 chromosome 20

> **Read this before copying the recipe for real work.** The index is chromosome 20
> only, so **6.67% of reads map uniquely and 92.7% come back "unmapped: too short."**
> That is the correct, expected result for a whole-transcriptome library aligned
> against one chromosome — most of these reads have no home in the reference at all,
> and `too short` is STAR's catch-all for a read that fails the minimum-mapped-length
> filter rather than a statement about read length. It is not a broken run, and it is
> not tunable away honestly. But it means this recipe answers "does STAR run and
> produce a real BAM," **not** "are these alignments right." For real work, index the
> whole genome — which does not fit here, see below.

Two tasks. `STAR --runMode genomeGenerate` builds a splice-aware index from chr20
plus its annotation; a second task aligns 200,000 read pairs and counts reads per
gene. Both are STAR in the same pinned image.

## Why chromosome 20, and why that is a real constraint

Not convenience — disk, at the time this recipe was written. A task got the AL2023
arm64 AMI default of **8 GiB, about 6.1 GiB usable** after the OS, and a full human
STAR index is ~30 GiB. It did not fit, and no field in the TaskSpec could make it
fit. A chr20 index is 619 MiB and leaves room for the reads, the BAM and the tar.

**That constraint has since been lifted.** spawn 0.103.0 added
`resources.disk_gib`, which wires into the same root-volume size `--volume-size`
sets on the launch path (spawn#556/#558); `--dry-run` now prints the resolved root
disk either way. A whole-genome index is therefore expressible now — set
`disk_gib` to ~60 and the shape of this recipe doesn't otherwise change.

It is deliberately **not** done here. This recipe's job is "STAR runs on Graviton4
and produces a real BAM," which chr20 demonstrates for pennies; a whole-genome index
is a different, slower, more expensive recipe and belongs to the round that cares
about realistic mapping rates. What's recorded above is the constraint the run was
shaped by, kept because the 6.67% mapping rate below is only interpretable if you
know why the reference is one chromosome.

## Why two tasks

STAR is one tool, so a single task would have been legal. It is split because **the
index is the expensive, reusable artifact**: it does not depend on the reads, and
every future sample aligned against this reference wants the same one. Task 2 can be
re-run — different reads, different output options — without rebuilding it. That is
the resumability the S3 round-trip buys.

## The index travels as a tar, not as an S3 prefix

A STAR index is a directory of 16 files. `spawn` does support directory staging — a
`Manifest` source ending in `/` gets `aws s3 cp --recursive`
([`wrapper.go:288`](https://github.com/spore-host/spawn/blob/main/pkg/taskproto/wrapper.go)) —
but **a directory output cannot work on the container path.** The wrapper `mkdir -p`s
the parent of every *input* destination and no output source
([`wrapper.go:87`](https://github.com/spore-host/spawn/blob/main/pkg/taskproto/wrapper.go)),
so an output's host directory gets created by dockerd instead, as root, and the
container — running as the image's own user — cannot write into it. The index is
therefore tarred to a single flat file in `/tmp`, which is `1777` and the one reliably
writable mount. Filed as spore-host/spawn#564.

## Two flags that are not defaults and must not be dropped

| flag | value | why |
|---|---|---|
| `--genomeSAindexNbases` | `11` | STAR's default of 14 is sized for a whole genome. The documented rule is `min(14, log2(genomeLength)/2 - 1)`; for chr20's 64,444,167 bases that is 11. Leaving it at 14 wastes memory and time, and STAR warns about it. |
| `--sjdbOverhang` | `74` | `readLength - 1`. These reads are **75 bp**, uniformly — checked, not assumed. |

Also worth knowing: `genomeGenerate` **ignores `--outFileNamePrefix`** and writes its
`Log.out` into the genome directory. The recipe reads its completion line from
`idx/Log.out` for that reason; a check against a prefixed filename would have looked
for a file that never exists.

## Pins

| | |
|---|---|
| image | `quay.io/aarchbio/star@sha256:90331f64bd73eadaefbebc8d4aecfed3ebed1a7e40b761041acbbcf53b3e13ae` |
| | tag `2.7.11b--h0aa3852_8`, cosign-signed, manifest is `linux/arm64` only |
| reference | Ensembl release-116 `Homo_sapiens.GRCh38.dna.chromosome.20.fa.gz`, byte for byte |
| | `sha256:1b8cd336cc563d36595fc4148956fbafceb6441f6d8003ff2a78d7e31765d10c` (18,833,053 B, 64,444,167 bases) |
| annotation | chr20 records of Ensembl release-116 `Homo_sapiens.GRCh38.116.gtf.gz` |
| | `sha256:2ca5f41221505ddc5a87933b6240d6854423e8a7761aeec5d49e7b50b8380c94` (1,972 genes, 125,469 exons) |
| reads | ENA `ERR188026` (Geuvadis, GBR lymphoblastoid), first 200,000 pairs, 75 bp |
| | `sha256:1198ed07e41fdf6f53710dfe4eee376d978e1bf61f0d9dcdd8b7ec2ce7c5432e` / `sha256:6104ee4641702156ce3f5330b750db6fd461e597de43bb280158bc751a7e120a` |

**Data tier: stable public source with a durable id.** Ensembl `release-116/` is
immutable, which is what makes it pinnable; `pub/current_*` is not and does not
qualify. The reads are the same fixed slice `recipes/salmon` uses, so the two RNA-seq
recipes are directly comparable.

The annotation is subset rather than used whole: the full GTF is 141 MB compressed
and STAR warns, correctly, about annotation on chromosomes absent from the index.
`stage-inputs.sh` verifies that every kept record really is chr20 and that no
coordinate runs past the end of the chromosome — so a bad subset fails at staging
rather than producing a quietly wrong index. Ensembl names this chromosome `20`,
not `chr20`, and the fasta and GTF must agree; that is checked too.

## Smoke check

Measured in this image, on this input, before any launch.

| observable | assertion | observed |
|---|---|---|
| input reads | exactly 200000 | 200000 |
| uniquely mapped | 4000–40000 | **13346** |
| uniquely mapped % | 2.0–20.0 | **6.67** ← see the caveat at the top |
| multi-locus reads | 100–10000 | 1160 |
| splices, total | 500–20000 | 2612 |
| splices, annotated | > 500 | 2139 |
| BAM bytes | > 200000 | 2284962 |
| BAM magic bytes | exactly `1f8b0804` | `1f8b0804` |
| `ReadsPerGene` rows | 1900–2100 | 1976 = 4 + 1972 chr20 genes |
| genes with ≥1 read | 100–1500 | 423 |
| `ReadsPerGene` header rows | exactly 4 | 4 |
| *index task:* chromosomes | exactly 1 | 1 |
| *index task:* chr name | exactly `20` | `20` |
| *index task:* chr length | exactly 64444167 | 64444167 |
| *index task:* index size | 300000–1200000 KiB | 633068 (~619 MiB) |
| *index task:* sjdb junctions | > 5000 | 17342 |

The mapping bands are wide because the mapping rate is a property of this
reference/library mismatch, and because a future STAR could shift it. They are still
tight enough that a broken index, an empty BAM or a wrong-chromosome reference fails.

There is **no samtools in this image** — one tool per image is deliberate — so the BAM
is checked two ways without it: its size, and its first four bytes against the BGZF
magic `1f 8b 08 04`. That is the cheapest real evidence the file is a BAM rather than
a truncated write. A `samtools flagstat` would mean a second task; it is not worth one
here, because STAR's own `Log.final.out` already reports the numbers a flagstat would.

The annotated-splice check is the one that proves the *annotation* was used at all:
2,139 of 2,612 splices match a known junction, which cannot happen if `--sjdbGTFfile`
was silently ignored.

## Resources, and what the timings mean

| task | shape | measured work |
|---|---|---|
| `01-index` | 8 vCPU / 16 GiB, `c8g`, TTL 20m, cap $0.13 | 25s |
| `02-align` | 8 vCPU / 16 GiB, `c8g`, TTL 30m, cap $0.18 | 4m37s |

Each cap is `lifecycle.cost_limit` — TTL × the on-demand rate, the same ceiling TTL
already implies, stated explicitly now that a TaskSpec can carry it (spawn#558).

The align step is slow for 200,000 reads — STAR reports 2.60 million reads/hour —
and that is the chr20 effect again: STAR searches exhaustively before giving up on a
read, so the 92.7% that cannot map are the ones costing the time. A whole-genome index
would align these reads *faster*.

**These timings are not compute cost.** Each task pays instance boot, image pull and
S3 staging before the tool starts — on recipe #1 that overhead was ~5 minutes against
78 seconds of work. Boot dominates; the 25-second index task is essentially all boot.

Disk: task 1 peaks at ~1.5 GiB (65 MiB fasta + 116 MiB GTF decompressed + 619 MiB
index + 619 MiB tar); task 2 holds ~1.3 GiB for its whole run (619 MiB staged tar +
619 MiB extracted index + 27 MiB reads). Both are comfortable against ~6.1 GiB
usable — the whole point of chr20.

**Task 2 does not delete the tar after extracting it, and must not.** The first run
of this task died trying: host `/tmp` is sticky (`1777`) and stage-in runs as the
instance user while the container runs as the image's own user, so the container gets
`EPERM` unlinking a staged input it does not own, and `rm -f` does not suppress `EPERM`. Deleting a directory the container
*created* is fine, which is why task 1's `rm -rf idx` works — the rule is only about
staged inputs.

## Running it

```sh
spawn task run --spec recipes/star/01-index.task.json --wait
spawn task run --spec recipes/star/02-align.task.json --wait
aws s3 ls s3://scicookbook-942542972736-us-east-1/runs/star/r1/
```

`--wait` exiting 0 does **not** prove the outputs exist: a task whose declared output
fails to stage is still recorded `completed` / `exit_code: 0`
(spore-host/spawn#561). The smoke check runs *inside* the task, where it can fail the
task; the bucket listing is the second half of the same check.

**Re-running one task.** `task_id` is fixed, so a re-run overwrites the previous
`completion.json` and `command.log`. Bump the `-r1` suffix in both `task_id` and the
output prefix to keep both records.
