# BLAST+ on the whole human proteome against SwissProt — Graviton4, Intel and AMD

> **Graviton4 is the cheapest place to run blastp: AMD costs 38% more per result and Intel 66% more.** AMD's 16
> full cores are no faster than Graviton4's 16 (3,861 s against 3,783), and Intel's 16 SMT threads on
> 8 cores take 5,385 s. The 7,990,605-row hit table is byte-identical on all three chips. Every query
> that hits anything finds itself at 100% identity, full length.

Workload: **every reviewed human protein** (all 20,525 SwissProt entries carrying taxid 9606,
11.4M residues) searched with `blastp` against **all of SwissProt** (487,728 sequences, 186M
residues), the database the queries were drawn from. Annotating a whole proteome against the curated
reference is a routine BLAST job, and this is a complete one, not a slice.
[DIAMOND](../../recipes/diamond/README.md) then runs the same queries against the same database, on
the same bytes, as the cross-code check.

**Data tier: RODA.** NCBI's preformatted `swissprot` BLAST database from `s3://ncbi-blast-databases`,
snapshot `2026-10-10-01-05-01` (database dated 2026-10-08), plus `taxonomy4blast.sqlite3`. All 13
files are pinned by sha256 in [`inputs.sha256`](inputs.sha256). RODA keeps only the last three
snapshots, so the RODA path will expire. The pin is the sha256 set, and the bucket copy holds the
bytes. A newer snapshot is a different database and a pin swap: re-run every assertion below.

## Run it

```sh
export AWS_PROFILE=aws B=<your bucket>
bash measurements/blast-real/stage-inputs.sh "$B"   # S3-to-S3 copy; S3 computes each object's sha256
for a in arm64 x86_64 amd; do                       # each 02 reads its own chip's 01 outputs
  spawn task run --wait --spec "$(python3 scripts/spec_arch.py blast $a "$B" r3 measurements/blast-real/01-blast.task.json)"
  spawn task run --wait --spec "$(python3 scripts/spec_arch.py diamond $a "$B" r3 measurements/blast-real/02-diamond.task.json)"
done
bash measurements/blast-real/collect.sh "$B" r3 r3  # results/<chip>/, results/crosschip.txt
```

[`run.sh`](run.sh) extracts the queries and the database FASTA with `blastdbcmd`, then runs the search.
[`diamond.sh`](diamond.sh) runs the cross-code task. [`mkspec.py`](mkspec.py) writes both specs and
pins the staged scripts by sha256.

```sh
blastdbcmd -db swissprot -taxids 9606 -no_taxid_expansion -outfmt '%o	%a	%T	%s'   # queries, keyed by OID
blastp -query queries.fa -db swissprot -num_threads 16 -max_target_seqs 500 \
  -outfmt '6 qseqid sallacc pident length qlen slen evalue bitscore'
diamond blastp -q queries.fa -d sp --very-sensitive --max-target-seqs 500 -p 16
```

## Results

16 threads on a `4xlarge` (16 vCPU, 32 GiB) of each chip. Same image digests (aarch.bio's BLAST+
2.16.0 on Graviton4, bioconda's x86 build of the same version on both x86 chips), same staged bytes:

| chip | instance | `blastp` wall | billed | peak RSS | $/hr | compute $/result | billed $/result |
|---|---|---|---|---|---|---|---|
| **Graviton4** | `c8g.4xlarge` | **3,783 s** | 3,882 s | 0.80 GiB | 0.6381 | **0.671** | **0.688** |
| Xeon 6975P-C | `c8i.4xlarge` | 5,385 s | 5,499 s | 0.79 GiB | 0.7497 | 1.121 | 1.145 |
| EPYC 9R45 | `c8a.4xlarge` | 3,861 s | 3,954 s | 0.79 GiB | 0.8622 | 0.925 | 0.947 |

| DIAMOND `--very-sensitive`, same box | search | billed | peak RSS | billed $/result |
|---|---|---|---|---|
| Graviton4 | 73 s | 131 s | 2.74 GiB | 0.023 |
| Xeon | 64 s | 128 s | 2.98 GiB | 0.027 |
| EPYC | **32 s** | 85 s | 3.00 GiB | **0.020** |

A *result* is one whole verified search with its checks. *Billed* runs from launch to terminate, at
spawn's printed price. *Peak RSS* is `blastp`'s (or `diamond`'s) own `VmHWM`. The BLAST container's
cgroup `memory.peak` was 1.03–1.05 GB on every chip. BLAST+ links no BLAS, so there is no OpenBLAS
kernel to record. Threads per core: 1 on Graviton4 and AMD, 2 on Intel.

- **Graviton4 wins on cost because AMD buys no speed here.** AMD runs 16 full cores to Graviton4's
  16 and finishes 2% slower, at a 35% dearer hour. Intel's 16 vCPU are 8 cores × 2 threads and run
  1.42× longer. Why AMD doesn't pull ahead on blastp is not established. The same AMD box runs
  DIAMOND 2.3× faster than Graviton4, so a wall clock alone can't attribute it.
- **The timings repeat.** Earlier runs of the same search failed only on check glue. They timed
  3,779 s (with the one extra OID below) and 3,783 s on Graviton4, 5,371 s on Intel and 3,653 s on AMD. Graviton4 and Intel
  repeat to 0.3%. AMD varied by 5.7%. Its spread doesn't close the cost gap: at 3,653 s AMD is still
  $0.90 billed.
- **Boot is 2–3% of a BLAST bill and 44–62% of a DIAMOND bill.** DIAMOND is 52× faster than BLAST+
  on Graviton4 and 121× faster on AMD. At that speed the box's fixed overhead is most of the cost.
  These timings are not compute cost.
- **Memory is not the constraint.** `blastp` peaks at 0.8 GiB, DIAMOND at 3 GiB, and the staged
  database is 0.47 GB. Size by cores, not memory.
- n = 1 per cell for the table, from the run whose checks passed. BLAST+ is deterministic and its
  output is identical on every chip, so a repeat re-measures only timing.

## The identities, at scale, on all three chips

| observable | assertion | observed (every chip) |
|---|---|---|
| database sequences / residues | **== NCBI's published 487,728 / 186,063,409** | 487,728 / 186,063,409 |
| exported FASTA records | **== database sequences** | 487,728 |
| query OIDs | **== distinct OIDs == OIDs with a 9606 defline** | 20,525 |
| queries with any hit | recorded | 20,522 |
| **queries whose hits contain themselves** | **== queries with any hit** | **20,522** |
| **self-rows at 100.000% identity** | **== self-rows** | **20,522** |
| self-alignments spanning the whole query | observation | 20,522 |
| self-hit is the top score | observation | 20,500 |
| DIAMOND: self-rows, at 100% | **== queries it hits** | 20,499, 20,499 |
| DIAMOND ∩ BLAST+ self-hits | observation | 20,499 (BLAST+ only: 23; DIAMOND only: 0) |

- **The self-hit identity holds at 22× the recipe's scale.** A self-row is a subject whose accession
  list contains the query's own. SwissProt is non-redundant, so a human protein identical to another
  species' protein shares one OID with it (1,864 such deflines). Matching on the OID, not the first
  accession, is what keeps the identity exact.
- **Every self-alignment is full length here.** On the recipe's Ensembl fixture, 102 of 931 stopped
  one residue short. On SwissProt, none of 20,522 do. So that truncation belongs to that input, not
  to BLAST+ at scale. Its cause on Ensembl is still not established.
- **Three queries hit nothing, not even themselves.** They are 8, 10 and 80 residues long. A perfect
  8- or 10-residue match cannot reach the default e-value of 10 against 186M residues. The 80-residue
  miss is not explained. In 22 queries another subject outscores a full-length 100% self-alignment,
  also not explained. Both are observations.
- **`-taxids 9606` expands to descendant taxa by default.** That adds one OID, `P84351`, whose only
  defline is taxid 63221 (*Homo sapiens neanderthalensis*). `-no_taxid_expansion` keeps the query
  set to 9606 itself. The task asserts the set is exactly that.
- **DIAMOND agrees on the self-hits it finds.** It finds 20,499 of BLAST+'s 20,522 (99.89%) and
  none that BLAST+ misses. That is the [like-with-like](../../practices/cross-checks.md) comparison
  (`--very-sensitive`, the same `--max-target-seqs`, the same staged bytes). Top-hit or score
  equality would compare tie-breaking and heuristic scoring instead.

## Across chips

- **BLAST+: identical on all three.** `hits.tsv` sha256 `504c664a…` on Graviton4, Intel and AMD:
  7,990,605 rows, every e-value and bit score, from two different builds (aarch.bio arm64, bioconda
  x86). The query FASTA and the exported database FASTA are identical too. That is asserted, since
  they are pure functions of the pinned bytes.
- **DIAMOND: identical counts, different bytes, and the difference is the build, not the chip.**
  Intel and AMD, which run the same x86 binary, produce byte-identical tables (`9c8ac259…`).
  Graviton4's aarch.bio build differs (`2647f14b…`) with every count above equal, down to the 748
  self-alignments that stop short of full length. Since the arm64 build only runs on arm64, build and
  architecture can't be separated here.

## Caveats

One database snapshot, one proteome, one thread count. Neither instance size nor generation was
swept. Spend for this write-up: **$6.27**, including four failed
runs, all on check glue.
