# sourmash gather on two whole published mock-community runs against GTDB R226: three chips

> **Every chip produces the same bytes: the same sketches, the same gather table, the same taxonomy.**
> AMD EPYC is 1.42× faster than Graviton4 and 6% cheaper billed; Intel is 4% faster and 11% dearer.
> Both runs cost $0.15–0.17 together, and sketching, not the database search, is the long pole.

Workload: the two **whole** Illumina shotgun runs of the ZymoBIOMICS standards from Nicholls et al.
2019 (GigaScience, [PRJEB29504](https://www.ebi.ac.uk/ena/browser/view/PRJEB29504)), the same staged
bytes [metaphlan-real](../metaphlan-real/README.md) and [kraken2-real](../kraken2-real/README.md) use,
each sketched and `gather`ed against **all 143,384** GTDB R10-RS226 species-representative sketches:

| run | standard | reads | bases | what it tests |
|---|---|---|---|---|
| `ERR2984773` | D6300 **even**: 8 bacteria at 12%, 2 yeasts at 2% | 17,571,462 (MiSeq 2×151) | 2.65 Gbp | the closed list |
| `ERR2935805` | D6310 **log**: 89.1% down to 0.000089%, one decade per step | 95,665,106 (HiSeq 2×101) | 9.66 Gbp | depth and dynamic range |

This is sourmash's canonical job: sketch a whole metagenome, find the minimum set of reference genomes
that covers it, and classify that set. Whole runs of 10–100M reads against the full GTDB
representative set is what researchers run, and a defined mixture gives the truth to check against.

## Run it

```sh
export AWS_PROFILE=aws B=<your bucket>
bash measurements/metaphlan-real/stage-inputs.sh "$B"        # the two Zymo runs (RODA -> FASTQ.gz)
bash measurements/sourmash-real/stage-inputs.sh "$B"         # 00-fetch: GTDB R226 sketches -> bucket
for a in arm64 x86_64 amd; do
  spawn task run --wait --spec "$(python3 scripts/spec_arch.py sourmash $a "$B" "$(date +%s)" \
    measurements/sourmash-real/01-gather.task.json)"
done
bash measurements/sourmash-real/collect.sh "$B"              # results/<chip>/, results/compare.txt
```

`01-gather` checks every staged object against `pins.sha256`, then `run.py` runs one pipeline per
sample, side by side:

```sh
sourmash sketch dna -p k=31,scaled=1000,abund ERR2935805_1.fastq.gz ERR2935805_2.fastq.gz \
  --name ERR2935805 -o ERR2935805.sig.zip
sourmash gather ERR2935805.sig.zip gtdb-rs226-reps.k31.sig.zip -o gather.csv --save-matches matches.zip
sourmash tax metagenome -g gather.csv -t gtdb-rs226-reps.lineages.csv -F csv_summary -o ERR2935805
```

## Results

8 vCPU (`r8*.2xlarge`, 64 GiB: the RAM is for the tmpfs, which holds 12.2 GiB of staged reads and
database), sourmash 4.9.4 (aarch.bio's build on Graviton4, bioconda's on the other two), the same
staged bytes. Both samples in one task; a sample's wall is its sketch + merge + gather + tax + score:

| chip | instance | even wall | log wall | tool wall | billed | peak RSS | $/hr | compute $/sample | billed $/sample |
|---|---|---|---|---|---|---|---|---|---|
| Graviton4 | `r8g.2xlarge` | 505 s | 1,077 s | 1,077 s | 1,196 s | 4.01 GiB | 0.4713 | 0.0705 | 0.0783 |
| Xeon 6975P-C | `r8i.2xlarge` | 534 s | 1,033 s | 1,032 s | 1,124 s | 4.01 GiB | 0.5557 | 0.0797 | 0.0867 |
| **EPYC 9R45** | `r8a.2xlarge` | **385 s** | **759 s** | **759 s** | **833 s** | 4.01 GiB | 0.6390 | **0.0674** | **0.0739** |

| chip | cores × threads | sketch R1+R2, log (9.66 Gbp) | sketch rate, one thread | gather, even / log | overhead |
|---|---|---|---|---|---|
| Graviton4 | 8 × 1 | 652 s | 14.8 Mbp/s | 354 / 419 s | 119 s |
| Xeon 6975P-C | 4 × 2 | 589 s | 16.4 Mbp/s | 366 / 437 s | 92 s |
| EPYC 9R45 | 8 × 1 | 441 s | 21.9 Mbp/s | 275 / 313 s | 74 s |

- A *sample* is one run sketched, gathered, classified and checked; $/sample is the two-sample task's
  cost halved, since both ran at once on one box. *Billed* is the completion record's window
  (spawn's wrapper start to end); *$/hr* is the price that record was costed at.
- **AMD is 1.42× Graviton4 on the whole task** and 1.48× on the sketch alone. At 36% more per hour
  that makes it 4% cheaper in compute and 6% cheaper billed. **Intel's 4 cores with SMT run six
  single-threaded processes 4% faster than Graviton4's 8 cores**, and at 18% more per hour it is 11%
  dearer billed.
- **Sketching is the long pole, and it is single-threaded per file.** The log run's R1+R2 sketch is
  57–61% of its pipeline on every chip. Sketching R1 and R2 as separate processes and `sig merge`-ing
  them took 325–348 s on Graviton4 against 652 s for one process, with an identical result (below), so
  that is the way to use more cores.
- **gather is 4.0 GiB peak RSS on both samples** whatever the depth: it is the database scan, not the
  query. Every other step stays under 0.3 GiB. At 12.2 GiB of tmpfs plus 4 GiB of RSS, an
  `m8g.2xlarge` (32 GiB) would also fit; it was not run.
- **Overhead is 74–119 s** per task (stage-in of 13.0 GB, Docker, image pull), 8–10% of the bill.
- n = 1 per cell. Every output is byte-identical on all three chips, so a repeat would change the
  timings and nothing else.

## What held at scale, on all three chips

| claim | why it must hold | even | log |
|---|---|---|---|
| sequences sketched == ENA's `read_count` (read from the sketch log, not asserted in-task) | the whole run, nothing dropped | 17,571,462 | 95,665,106 |
| **sketch(R1+R2) == sig merge(sketch R1, sketch R2)** | FracMinHash is a set; abundances add | 201,687 hashes, 2,109,682 weighted | 353,242 / 6,551,824 |
| `query_n_hashes`, `query_bp`, `total_weighted_hashes` == the sketch | gather read the query whole | yes | yes |
| `remaining_bp` == `query_bp` − running sum of `unique_intersect_bp` | gather partitions the query | 19 rows | 21 rows |
| **greedy replay from `matches.zip`, every row** | each row's unique hashes and weight, recomputed independently | 19/19 | 21/21 |
| query ∩ ∪(matches) == the assigned hashes | a hash in any chosen genome was assigned to one | 24,832 | 17,911 |
| each rank's `f_weighted_at_rank` sums to 1; every clade == the sum of its children | tax is a tree over the partition | worst 1.1e-16 | worst 1.1e-16 |
| classified species == gather's weighted cover | every matched genome has a lineage | 0.7206 | 0.8498 |
| **required Zymo species detected** | closed list | **8/8 bacteria** | **2/2** |
| **unexpected species above 1%** | closed list | **0** (largest 0.39%) | **0** (largest 0.31%) |
| top two == the 89.1% and 8.9% species, in order | a decade apart | – | *L. monocytogenes* 73.4%, *P. aeruginosa* 7.7% |

The replay is the strong check: `score.py` loads every matched sketch and re-runs gather's greedy
subtraction from the query's own hashes, so a wrong overlap or weight in any row fails it. The
float tolerances (1e-9) are a sum of a few dozen doubles, not a band. `zymo.tsv` is the truth, with
the GTDB names each Zymo species is filed under (*B. subtilis* ATCC 6633 is *B. spizizenii*;
GTDB's `_B`-style suffixes on a species are accepted) fixed before the first run.

Observed, not asserted:
- **The detection floor is 0.0089%, the same as [MetaPhlAn's](../metaphlan-real/README.md).**
  *L. fermentum* at 0.0089% of DNA was found (0.0078%), *E. coli* and *S. enterica* at 0.089% were
  (0.037%, 0.059%); *E. faecalis* (0.00089%) and *S. aureus* (0.000089%) were not, at 95.7M reads.
- **The even mix comes out at 5.9–10.8% per bacterium against 12%,** and *B. subtilis* (as
  *B. spizizenii*) at 10.7%, where MetaPhlAn under-calls it about 200×. sourmash weights by
  k-mer abundance across the whole genome, so no marker set can miss it.
- **28% (even) and 15% (log) of the weighted k-mers are unclassified.** GTDB is prokaryotes only, so
  the two yeasts cannot match, and the Zymo strains are not the GTDB representatives, so their
  strain-specific k-mers have nowhere to go. Most distinct hashes in a raw-read sketch are
  sequencing errors: the matches cover 85% of the log run's weighted k-mers but 5.1% of its
  distinct ones.
- Unexpected species below 1% are close relatives of the eight: *Escherichia* sp., *S. diarizonae*,
  *P. paraeruginosa*, and in the log run *L. farberi*, *L. innocua* and three more *Listeria*.
- The database has 230 fewer sketches than GTDB R226 has species representatives (143,384 of
  143,614); `fetch.py` checks the direction that matters, that every sketch has a lineage.

## Across chips: what is a fixed point and what is a path

**Everything is identical, byte for byte.** The query signatures (sourmash `md5` and the sha256 of
every hash and abundance), `gather.csv`, the `tax` summary, `species.tsv` and `checks.txt` match on
Graviton4, Intel and AMD (`results/compare.txt`). That holds across two builds and two Pythons
(3.14 on aarch.bio, 3.13 on bioconda). Sketching is integer hashing and gather is set arithmetic,
so there is no path here at all: the floats in the gather table are ratios of the same integers. No
BLAS is involved, so the [OpenBLAS kernel question](../../patterns/cross-architecture.md) does not
arise.

## Pins

| | data tier |
|---|---|
| reads | `ERR2984773`, `ERR2935805`: RODA (`s3://sra-pub-run-odp`) → FASTQ.gz by metaphlan-real, sha256 in `pins.sha256` |
| database | `gtdb-rs226-reps.k31.sig.zip` sha256 `6f3cc74f…`, `gtdb-rs226-reps.lineages.csv` `98bceab2…`: sourmash's prepared GTDB R10-RS226 release, fetched on AWS twice with the same sha256 |

The database is a versioned release (GTDB R226) on the sourmash project's own host
(`farm.cse.ucdavis.edu/~ctbrown/sourmash-db/gtdb-rs226/`), not a DOI archive, so the sha256 is the
pin: a different file under that name is a different database, and `01-gather` refuses it.

## Caveats

- Two samples are a small cohort; per-Gbp sketch rate and the 5–7 minute gather against the full database are what transfer.
- The database zip carries no stored manifest, so every `gather` builds one while it scans. That is
  part of the gather time here, and it is what a user of this file pays too.
- Prices are on-demand us-west-2, as spawn's completion record costed these instances.
