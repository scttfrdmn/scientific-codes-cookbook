# foldseek on the whole human proteome, all against all: three chips

> **AMD finishes in 29 minutes, 1.7× faster than Graviton4 and 1.8× faster than Intel, and is the
> cheapest per result. Every protein but one finds itself as a perfect alignment on all three chips.
> The exception is ataxin-8, which is Met plus 79 glutamines. Intel and AMD produce byte-identical
> 5.5M-row tables, and Graviton4 matches 99.1% of those rows byte for byte.** Graviton4 and x86 disagree
> on 52 hits, 0.001% of them. In 47 of the 52, the prefilter picked a different candidate list.

Workload: **the complete AlphaFold DB v6 human proteome** (UP000005640, UniProt 2025_03), 23,586
predicted models and 15,088,388 residues. That is EBI's published `num_predicted_structures`, and
proteins over 2,700 residues come as overlapping fragments (`-F1`, `-F2`, …), as AFDB ships them. It is
searched against itself with foldseek's defaults (3Di+AA, `-e 10`, `--max-seqs 1000`). An all-vs-all of a
reference proteome is a standard foldseek job: structural similarity networks, remote homology for a
genome. This is the whole proteome, not a slice of it. The fixture's 16 PDB entries become 23,586 models
and 5.5 million hits.

## Run it

```sh
export AWS_PROFILE=aws B=<your bucket>
bash measurements/foldseek-real/stage-inputs.sh "$B"     # 5.18 GB from EBI -> bucket, fetched on AWS
for a in arm64 x86_64 amd; do
  spawn task run --wait --spec "$(python3 scripts/spec_arch.py foldseek $a "$B" "$(date +%s)" measurements/foldseek-real/real.task.json)"
done
spawn task run --wait --spec "$(python3 scripts/spec_arch.py foldseek arm64 "$B" "$(date +%s)" measurements/foldseek-real/compare.task.json)"
bash measurements/foldseek-real/collect.sh "$B"          # results/<chip>/, results/compare/
```

What `real.sh` runs is the call a user writes, on the AFDB archive as downloaded:

```sh
foldseek createdb UP000005640_9606_HUMAN_v6.tar db --file-include '.*\.cif\.gz$'
foldseek search db db aln tmp -a --threads 16
foldseek convertalis db db aln aln.tsv --format-output query,target,fident,alnlen,mismatch,gapopen,qstart,qend,tstart,tend,qlen,tlen,evalue,bits,alntmscore,qtmscore,ttmscore,lddt,prob
```

foldseek reads the tar directly. `--file-include` keeps it to the mmCIFs, because the archive also
holds every model as `.pdb.gz`. `-a` keeps the backtraces that convertalis needs for the TM-score and
LDDT columns.

## Results

`c8g/c8i/c8a.4xlarge` (16 vCPU, 32 GiB), 16 threads. aarch.bio's foldseek 8.ef4e960 runs on Graviton4,
and bioconda's build of the same version runs on Intel and AMD. Same archive bytes on all three:

| chip | instance | cores × threads | tool wall | billed | peak RSS (box) | $/hr | compute $/result | billed $/result |
|---|---|---|---|---|---|---|---|---|
| Graviton4 | `c8g.4xlarge` | 16 × 1 | 2930 s | 3504 s | 4.01 GiB | 0.6381 | 0.519 | 0.621 |
| Xeon 6975P-C | `c8i.4xlarge` | 8 × 2 | 3144 s | 3579 s | 3.98 GiB | 0.7497 | 0.655 | 0.745 |
| **EPYC 9R45** | `c8a.4xlarge` | 16 × 1 | **1715 s** | **2091 s** | 3.98 GiB | 0.8622 | **0.411** | **0.501** |

A *result* is the whole proteome searched against itself, with the alignment table written. *Tool
wall* is `createdb` + `search` + `convertalis`. *Billed* is spawn's recorded window: boot, staging the
5.18 GB archive, the tool, the checks below (the 2-thread subset re-search is most of them: 261–448 s)
and stage-out. *$/hr* is spawn's cost estimate over that window. *Peak RSS* is the container cgroup's
`memory.peak`. foldseek runs as a chain of subprocesses, so there is no single process to read
`ru_maxrss` from.

| phase (s) | Graviton4 | Xeon | EPYC |
|---|---|---|---|
| `createdb`, 23,586 models from the tar | 15 | 22 | 15 |
| `search` (prefilter + 3Di+AA alignment) | 1622 | 1606 | 970 |
| `convertalis` with TM-score and LDDT | 1293 | 1516 | 730 |
| *check: 472-query subset at 2 threads (search + convertalis)* | *448* | *299* | *261* |

- **AMD is 1.7× faster than Graviton4 and 19% cheaper per result billed.** Its higher hourly price
  is more than covered by the speed. Graviton4 is 17% cheaper than Intel per result: the two search
  in the same time, but Intel's TM-score/LDDT pass is 17% slower and costs more per hour.
- **The two builds are different binaries.** aarch.bio compiles foldseek for arm64 and bioconda for
  x86-64, each for its own SIMD instruction set, so a chip comparison here is also a build comparison. The two cannot be separated with one build per architecture.
- **Intel loses on cores, not per core.** At 2 threads, on two physical cores, its subset search is 1.6×
  Graviton4's speed. At 16 threads on 8 SMT cores it falls back to parity.
- **The search scales well.** In the probe, Graviton4's subset searched 7.4× faster at 16 threads
  than at 2, and its full search took 1622 s, against 1750 s extrapolated from the probe's 472 queries.
- **Memory is not the constraint.** It peaks at 4 GiB of 32 on every chip, so a `c8*.2xlarge` would
  fit. The 5.18 GB archive sits in the 16 GiB `/tmp` tmpfs, and the database and alignment
  files on the root disk.
- **n = 2 per chip.** Each chip ran the search twice on separate boxes, and the two sorted tables
  were byte-identical (below). The timings above are from the second run. The first runs took
  1636/1299 s, 1604/1520 s and 975/750 s for search/convertalis. These timings are not compute cost in
  general ([layout](../../patterns/layout-and-effective-cost.md)).

## What held at scale, on all three chips

| claim | why it must hold | result (every chip) |
|---|---|---|
| 23,586 database entries, 23,586 distinct names | EBI's published model count | 23,586 / 23,586 |
| every entry's 3Di string is as long as its sequence | one 3Di state per residue | 0 of 23,586 differ; 15,088,388 residues |
| **every protein finds itself** | the queries are the database | 23,585 of 23,586 |
| **every self-hit is the identity alignment** | full length on both sides, identity 1.000, no mismatch or gap | 23,585 of 23,585 |
| **every self-hit has TM-score 1 and LDDT 1** | a structure superposed on itself | alntmscore, qtmscore, ttmscore and LDDT all 1.000 |
| **nothing outscores itself on TM-score** | TM-score normalised by query length cannot exceed 1 | 0 queries outscored; 0 ties at 1.000 |
| **the proteins with no hit at all are exactly {Q156A1}** | named in `real.sh`, so a new one fails the run, and so does its disappearing | {Q156A1} |
| a 1-in-50 subset (472 proteins, chosen by name) re-searched at 2 threads gives the full 16-thread run's rows | a query's hits depend on the query and the database, not the batch or thread count | 105,937 rows, byte-identical |
| two runs per chip on separate boxes give the same table | determinism | byte-identical on each chip, though `createdb` numbered the entries differently each time |

**Observed, not asserted:** no protein is outscored by another on bits either (0 on every chip). The
3Di+AA score of the identity alignment is not provably maximal, so this is an observation.

**Ataxin-8 (Q156A1) is a homopolymer, and the defaults cannot see one.** Its sequence is M followed
by 79 Q. Re-searched with `--exhaustive-search 1 -e inf`, which skips
the prefilter and keeps every hit, it finds itself perfectly (TM-score 1, LDDT 1). That self-alignment
scores **8 bits, E = 85.6**, far outside `-e 10`. The prefilter also passes nothing for it, with or
without `--comp-bias-corr 0`. The same thing happens on every chip. Searching for low-complexity
proteins needs `--exhaustive-search` and a looser `-e`.

**Why the subset is chosen by name.** `createdb` numbers the entries in a different order on each run
(`sha_db_lookup_by_key` differs on all six runs), even though the set of names is identical. So a
1-in-50 subset by key picks different proteins on different boxes. The probes and the first full runs did
exactly that. The order never changed a result: the sorted tables match across runs.

## Across chips: identical on x86, 99.1% of rows on Graviton4

- **Intel against AMD: byte-identical**, all 5,499,733 rows (sha256 `d10fd658…`). It is the same
  bioconda binary, and the hit set, alignments, E-values and float TM-scores all agree.
- **Graviton4 against x86** ([`results/compare/`](results/compare/)): 5,499,743 rows against 5,499,733.
  **5,452,599 rows (99.14%) are byte-identical.**
  - **Hit set:** all but 52 pairs agree. 31 are found only on Graviton4 and 21 only on x86, which is
    0.0009% of the hits. 27 of the 52 involve one protein, ASTE1 (Q2TB18). Their E-values run from
    1.2e-7 to 9.7, so the `-e 10` cutoff alone does not explain them.
  - **Alignments**, for pairs both found: identity, length, mismatches, gap opens and coordinates
    differ in at most 111 of 5.45M pairs. E-value differs in 6,934 and bits in 3,139.
  - **Structure scores**: TM-scores differ in about 20,000 pairs, LDDT in 1,511 and the probability in
    1,233. These are floating-point superpositions, so they follow a different path per build. They
    are reported, not asserted.
- **Where the 52 come from** ([`diag.task.json`](diag.task.json)): the 28 queries involved were
  re-searched with `--exhaustive-search 1` on Graviton4 and on AMD (whose binary is Intel's), which aligns every pair and skips the
  prefilter. Both builds then find **47 of the 52**, so in the default run one build's prefilter
  dropped them from the candidate list. The remaining 5 all involve ASTE1, with E-values of 7.7–9.7.
  The two builds' alignment scores put them on opposite sides of `-e 10`.
- **Fixed points:** counts, every self-hit identity and the no-hit set are the same on all three chips.
  **Paths:** prefilter candidate lists at the margin, alignment scores near the cutoff, and the TM-score
  superposition all depend on the build. foldseek links no BLAS, so the OpenBLAS kernel rule does not apply.

## Pins

| | |
|---|---|
| structures | AFDB v6 `UP000005640_9606_HUMAN_v6.tar` from `ftp.ebi.ac.uk/pub/databases/alphafold/v6/`, 5,177,506,304 bytes (EBI's `download_metadata.json`), sha256 `1de6f3d7…` in `pins.sha256`, 23,586 `.cif.gz` + 23,586 `.pdb.gz` members. EBI publishes no checksum, so the pin is the sha256 the first fetch recorded. `fetch.py` checks size and model count, and the task checks the sha256 again at stage-in. Tier: versioned public release (AFDB is not on RODA). CC BY 4.0 |
| scripts | `real.sh` in `pins.sha256`; `compare.sh` and `diag.sh` in their own `.sha256`, checked in-task |
| image | the recipe's `quay.io/aarchbio/foldseek@sha256:871c61cc…` (arm64) and `quay.io/biocontainers/foldseek@sha256:f194c344…` (x86), both 8.ef4e960 |

`probe.task.json` is the sizing probe: `createdb` plus the 472-query subset at 16 and at 2 threads,
whose rows matched. It ran an earlier `real.sh`, which picked the subset by key. Per-chip scores,
phases, host facts and logs are in `results/`. The alignment tables (220 MB gzipped each) stay in the
bucket under `runs*/foldseek-real/real/`.

## Spend

Fifteen launches, **about $4.5**: $4.11 by spawn's estimates, plus about $0.36 for the first fetch. That fetch
ran one HTTPS stream from EBI (about 1 MB/s) and hit its TTL, and spawn recorded no cost for it. The
three verifying runs cost $1.87. The first full run on each chip ($1.88) gave byte-identical tables,
but failed on Q156A1, which `real.sh` now names. The rest went on two probes, two fetches, the
diagnostics and the comparisons.
