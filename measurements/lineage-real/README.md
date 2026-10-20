# pangolin and Nextclade on 100,000 designated genomes — Graviton4, Intel and AMD

> **pangolin puts 99.75% of 100,000 INSDC genomes in the part of the Pango tree their curators
> designated, and 97.99% on exactly the designated lineage. Every field of both tools' output is
> identical on all three chips.** AMD is fastest and cheapest. Both callers together cost
> $0.15–0.26 per 100,000 genomes. In this image pangolin silently uses the slow `usher` unless one
> Open MPI variable is set. With it set, the 2,000-genome probe took 43 s, not 1,688 s.

Workload: **100,000 SARS-CoV-2 genomes** drawn at random (seed 20261020) from the **1,009,509**
samples that pango-designation v1.38 designates **and** that also have one unambiguous record in
INSDC. Each is labelled with the lineage its curators assigned, and 2,977 distinct lineages appear.
Both callers assign every genome, and both are scored against those labels and against each
other. 100,000 genomes is a whole-programme retrospective batch, the size where per-genome cost
dominates boot. That is the opposite regime from the 165-genome fixture in
[recipes/lineage](../../recipes/lineage/README.md).

**Data tier: durable public ids.** The labels are `lineages.csv` from
[pango-designation](https://github.com/cov-lineages/pango-designation) at tag **v1.38** (commit
`5d58f88`). v1.38 is the release the pinned image's pangolin-data (`PUSHER-v1.38`, asserted on
every output row) was built from. The labels name GISAID strains, and GISAID bytes cannot be
redistributed, so the genomes come from Nextstrain's open INSDC release instead. Task 00 matches
each designated name to Nextstrain's `strain` (97,419 genomes) or, for COG-UK, through its ENA
run accession (2,581). The release is pinned by S3 object version in `nextstrain-data`: the
2026-10-17 `sequences.fasta.zst`, `metadata.tsv.zst` and `cog_uk_accessions.tsv.zst`, copied
server-side by [`stage-inputs.sh`](stage-inputs.sh). Every file is sha256-pinned in
[`results/choose/inputs.sha256`](results/choose/inputs.sha256), and the chosen genomes are also
written out as INSDC `accession.version` ids. A sample is dropped if its name maps to more than
one INSDC record (9,441 were). FASTA headers are accessions only, so no tool sees a strain name.

## Run it

```sh
export AWS_PROFILE=aws B=<your bucket>
bash measurements/lineage-real/stage-inputs.sh "$B"            # the release (server-side) + scripts
spawn task run --wait --spec "$(python3 scripts/spec_arch.py lineage arm64 "$B" r1 measurements/lineage-real/00-choose.task.json)"
for a in arm64 x86_64 amd; do                                   # each chip's 02 reads its own 01
  for s in $(python3 scripts/spec_arch.py lineage $a "$B" a1 \
      measurements/lineage-real/01-nextclade.task.json measurements/lineage-real/02-pangolin.task.json); do
    spawn task run --wait --spec "$s"
  done
done
bash measurements/lineage-real/collect.sh "$B" r1 a1            # results/<chip>/, results/crosschip.txt
```

```sh
nextclade run -j 8 --input-dataset dataset --output-tsv nextclade.tsv query.fasta.gz
export OMPI_MCA_plm_rsh_agent=false                             # or pangolin falls back to the old usher
pangolin query.fasta.gz --outdir work --outfile pangolin.csv --no-temp --threads 8 --skip-designation-cache
```

[`choose.py`](choose.py) builds the query (task 00). [`nextclade.sh`](nextclade.sh) and
[`pangolin.sh`](pangolin.sh) run the callers, [`check.py`](check.py) makes every check, and
[`mkspec.py`](mkspec.py) writes the specs with the scripts pinned by sha256.

## Results

Same image digests on each chip: aarch.bio's pangolin 4.4 and Nextclade 3.22.0 on Graviton4, and
bioconda's x86 builds of the same versions on Intel and AMD. Same staged bytes. Each tool ran at 8
threads on a `2xlarge` (8 vCPU, 32 GiB).

| step | chip | instance | tool wall | billed | peak RSS | $/hr | compute $/result | billed $/result |
|---|---|---|---|---|---|---|---|---|
| pangolin, 100k genomes | Graviton4 | `m8g.2xlarge` | 1515 s | 1727 s | 5.13 GiB | 0.3590 | 0.151 | 0.172 |
| | Xeon 6975P-C | `m8i.2xlarge` | 1495 s | 1692 s | 3.91 GiB | 0.4234 | 0.176 | 0.199 |
| | EPYC 9R45 | `m8a.2xlarge` | **628 s** | **808 s** | 4.02 GiB | 0.4869 | **0.085** | **0.109** |
| Nextclade, 100k genomes | Graviton4 | `m8g.2xlarge` | 297 s | 413 s | 0.45 GiB† | 0.3590 | 0.030 | 0.041 |
| | Xeon | `m8i.2xlarge` | 391 s | 484 s | 0.45 GiB† | 0.4234 | 0.046 | 0.057 |
| | EPYC | `m8a.2xlarge` | **205 s** | **289 s** | 0.45 GiB† | 0.4869 | **0.028** | **0.039** |

A *result* is 100,000 genomes assigned and checked. *Billed* runs from launch to terminate at
spawn's printed price. It includes the 2,000-genome single-thread run (pangolin 70–115 s,
Nextclade 29–48 s) and the checks. *Peak RSS* is `ru_maxrss` of pangolin's largest process.
† Nextclade's is the container's `memory.peak`. Threads per core: 1 on Graviton4 and AMD, 2 on
Intel, so `m8i.2xlarge` is four cores. No step is BLAS-bound (minimap2, scorpio, UShER and
Nextclade do integer and string work), so no OpenBLAS kernel was recorded. Choosing the genomes
(task 00) streamed the whole 9,439,976-genome release in 412 s on `r8g.xlarge`, billed $0.032,
once.

- **AMD is fastest at both callers**: 2.4× Graviton4 for pangolin and 1.45× for Nextclade. That
  makes it the cheapest per result despite the dearest hour.
- **Graviton4 and Intel tie on pangolin** (1515 vs 1495 s). Graviton4 is 1.3× faster on Nextclade
  and cheaper per result on both.
- **pangolin's time is scorpio and UShER, not alignment** ([`results/steps.txt`](results/steps.txt)).
  On Graviton4, minimap2 takes 109 s, scorpio's constellation calls 639 s, and UShER 731 s. That
  last figure is two equal passes: the `usher_to_report` rule prints `usher_inference`'s shell
  output and takes as long as it did, on every chip and in every run. That fits UShER being
  executed twice under this image's snakemake 9.23. It is reported here, not worked around.
- **Set `OMPI_MCA_plm_rsh_agent` or lose 40×.** The image's `usher-sampled` is linked against Open
  MPI, whose singleton start-up aborts when no `ssh`/`rsh` exists, which is true in the container.
  pangolin probes with `usher-sampled --help`, and when that fails it falls back to the original
  `usher`. It then prints `usher-sampled is not installed`, which blames the wrong thing. The 2,000
  genomes took **1,688 s that way and 43 s** with the variable set. The recipe's own 165-genome
  runs show the same message on arm64 and x86. [`pangolin.sh`](pangolin.sh) fails if it appears.
- **Memory doesn't size the box.** pangolin's largest process peaks at 4–5 GiB. The container's
  `memory.peak` reached 27–28 GiB, almost all page cache for the GB-scale intermediates on the root
  disk. Smaller boxes were not tried.
- n = 1 per cell. Every output is identical on every chip, so a repeat re-measures only timing.

## The identities, at scale, on all three chips

| observable | assertion | observed (every chip) |
|---|---|---|
| query, labels | sha256 == task 00's pin | yes |
| Nextclade dataset | sha256 == the recipe's pin; tag self-reported | `2026-09-07--17-10-15Z` |
| rows | **one per genome, no repeats**, both tools, both runs | 100,000 / 2,000 |
| versions | **pangolin 4.4, `PUSHER-v1.38` on every row** | yes |
| UShER histogram | **`L(a/b)` counts sum to b, call is in it, `conflict == (b−a)/b`** | 101,949 rows, 0 violations |
| QC | **fail ⇒ `Unassigned`** | 51 fails, all Unassigned |
| `is_designated` | **== the aligned-sequence md5 is in pangolin-data's `lineages.hash.csv`** | exactly the 96,540 |
| **batch + thread invariance** | first 2,000 alone at 1 thread **== their rows** in the 100k run at 8 | identical, both tools |
| Nextclade totals | **== its own lists**: subs, dels, ins, missing, non-ACGTN, unknown aa, aa subs/dels, private = rev + labeled + unlabeled | 100,000 rows, 0 violations |
| Nextclade coverage | **== (end − start + 1 − missing − nonACGTN) / 29903, bit for bit** | 100,000 rows |
| alias tables | **v1.38 (image) ⊆ v1.41, unchanged** | 561 of 561 |
| **pangolin vs designation** | **hierarchy-compatible ≥ 0.95** | **0.99755** (exact 0.97986) |
| Nextclade vs pangolin | hierarchy-compatible ≥ 0.80 (the recipe's floor) | 0.99696 (exact 0.93247) |
| Nextclade vs designation | observation | 0.99565 (exact 0.92505) |
| hash table's label vs `lineages.csv` | observation | 0.99994 (exact 0.99940) |

- **245 of 99,949 calls (0.25%) are incompatible with the designation.** The ten `check.log`
  prints are neighbouring lineages or ties, such as `B.1.369` for `B.1.564`, `BF.24` for `BF.5`,
  `A` for `B`, or `JN.1.16.1` whose histogram is `JN.1.16.1(1/2) JN.1.64.1(1/2)`. Of the
  compatible calls, 1,122 are finer than the designation and 646 coarser. The 0.95 floor is a
  backstop for a broken caller, not a band fitted to this value.
- **The lookup is not the test.** 96,540 genomes (96.5%) are byte-identical, after alignment, to
  the designated sequence, and pangolin's default mode would just return the label from its hash
  table. Hence `--skip-designation-cache`. The 3,460 whose INSDC bytes differ from the designated
  copy agree nearly as well: 0.99619 compatible, 0.96691 exact.
- **Nextclade calls coarser.** Its 5,259 coarser-but-compatible calls are why its exact rate is 7
  points lower at the same compatible rate. It places on its own dataset tree (tag `2026-09-07`),
  not pangolin-data v1.38, and its names are unaliased through the v1.41 table.
- **Placements don't depend on the batch.** UShER runs with `-n`, which adds no sample to the
  tree, and the first 2,000 genomes alone at 1 thread reproduce their 100k-run rows exactly.
  Nextclade's rows at `-j 1` equal those at `-j 8`, the index column included.

## Across chips

- **Identical on all three.** pangolin's whole report (every column of all 100,000 rows), the
  2,000-genome run, and Nextclade's whole TSV (every column, all 100,000 rows) have the same
  canonical sha256 on Graviton4, Intel and AMD. So do all eight agreement tables
  ([`results/crosschip.txt`](results/crosschip.txt)). That holds across two builds of each tool,
  aarch.bio arm64 and bioconda x86, which **ship different minimap2** (2.31 and 2.30). A placement
  is a discrete parsimony optimum, so no floating-point path exists to diverge. It is asserted
  (`collect.sh`).

## Caveats

The designations are curated with UShER-assisted tooling, so pangolin against them is not fully
independent, and designated genomes are curators' clean exemplars. Routine surveillance genomes,
with more missing data, will agree less. The Pango labels are v1.38, so lineages designated since
are not in the test. Instance size and generation were not swept. Spend for this write-up:
**$1.10**, of which $0.33 is the 2,000-genome probe that ran the slow `usher`.
