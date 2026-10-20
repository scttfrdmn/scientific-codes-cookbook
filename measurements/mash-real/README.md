# mash on a whole GTDB genus — Graviton4, Intel and AMD

> **mash sketches all 3,158 *Corynebacterium* genomes and computes all 9,972,964 ordered distances
> in about 70 s. Its nearest GTDB representative matches GTDB R232's own species call for 2,832
> of 2,837 genomes, and its per-species ANI ranks with GTDB's published skani values at ρ = 0.978.
> Every output byte is identical on all three chips.** Graviton4 is cheapest per result. Intel,
> at 8 cores and 16 threads, is slowest.

Workload: **every genome GTDB R232 places in g__Corynebacterium**: 3,165 genomes in 321 species
clusters, of which 3,158 can still be fetched (below). They are sketched at `-k 21 -s 10000`, the
recipe's settings, and compared all against all. Then every genome is screened against the 321
species representatives, which is the GTDB-Tk-style use. A whole genus is the unit people actually
dereplicate, cluster or type against. It has 132 species with three or more genomes, so a
taxonomic claim can be tested many times, and 12 species whose GTDB ANI radius is above 95%, so the
species boundaries are not all easy. The [recipe](../../recipes/mash/README.md)'s fixture is 20
genomes of 10 well-separated genera. This one is the opposite case: 8.0 Gbp of close relatives.

**Data tier: durable public ids.** GTDB release **R232** (15 Apr 2026): `bac120_metadata_r232.tsv.gz`
and `sp_clusters_r232.tsv`, checked against GTDB's own `MD5SUM.txt`. The genomes are the NCBI
assemblies GTDB names, by versioned accession, each checked against NCBI's own `md5checksums.txt`,
and every fetched length equals GTDB's `genome_size`. Task 00 ([`prep.sh`](prep.sh)) fetched them on
AWS into one flat tar. The tar and the tables are pinned by sha256 in [`pins.sha256`](pins.sha256).
The reproducible pin is the per-genome md5 set, and the tar is only its envelope. **Seven
non-representative genomes have been withdrawn by NCBI since the release.** One is
`status=suppressed`. Six UHGG MAGs no longer list a `genomic.fna.gz` in their directory. They are
listed in [`results/prep/missing.tsv`](results/prep/missing.tsv), and prep fails if a withdrawn
genome is a representative. All 321 are present.

## Run it

```sh
export AWS_PROFILE=aws B=<your bucket>
bash measurements/mash-real/stage-inputs.sh "$B"            # scripts, then task 00 fetches GTDB + NCBI on AWS
for a in arm64 x86_64 amd; do bash measurements/mash-real/launch.sh "$B" $a r1; done
bash measurements/mash-real/collect.sh "$B" r1              # results/<chip>/, results/crosschip.txt
```

```sh
mash sketch -p 16 -k 21 -s 10000 -l files.txt -o all        # 3,158 genomes
mash dist   -p 16 all.msh all.msh > dist.tsv                # 9,972,964 rows
mash dist   -p 16 reps.msh all.msh > screen.tsv             # every genome vs the 321 representatives
```

[`run.sh`](run.sh) is task 01. It runs mash and makes every check in awk and sort, because neither
mash image ships python. [`sourmash_xcheck.py`](sourmash_xcheck.py) is task 02, which puts the same
bytes through sourmash. [`mkspec.py`](mkspec.py) writes the specs.

## Results

Same image digests on each chip: aarch.bio's mash 2.3 on Graviton4 and bioconda's x86 build of the
same version on Intel and AMD. Same staged bytes. 16 threads on a `4xlarge` (16 vCPU, 32 GiB).

| step | chip | instance | tool wall | billed | peak RSS | $/hr | compute $/result | billed $/result |
|---|---|---|---|---|---|---|---|---|
| mash sketch + all-vs-all | Graviton4 | `c8g.4xlarge` | **68.8 s** | **192 s** | 0.73 GiB | 0.6381 | **0.0122** | **0.0340** |
| | Xeon 6975P-C | `c8i.4xlarge` | 99.9 s | 284 s | 0.72 GiB | 0.7497 | 0.0208 | 0.0591 |
| | EPYC 9R45 | `c8a.4xlarge` | 69.9 s | 211 s | 0.72 GiB | 0.8622 | 0.0167 | 0.0505 |
| sourmash cross-check | Graviton4 | `c8g.4xlarge` | 202.9 s | 266 s | 0.18 GiB | 0.6381 | 0.0360 | 0.0471 |
| | Xeon | `c8i.4xlarge` | 226.8 s | 298 s | 0.18 GiB | 0.7497 | 0.0472 | 0.0621 |
| | EPYC | `c8a.4xlarge` | 142.7 s | 187 s | 0.18 GiB | 0.8622 | 0.0342 | 0.0448 |

A *result* is the genus sketched and compared all against all. *Tool wall* is `mash sketch` plus
`mash dist` (13.3 + 55.5 s on Graviton4, 17.5 + 82.4 on Intel, 11.3 + 58.6 on AMD). *Billed* is
spawn's completion window, from wrapper start to stage-out, priced by its `cost_estimate`. It also
covers the invariance runs (a second full sketch, the representatives at one thread, the screen)
and the checks. *Peak RSS* is mash's own `VmHWM`, read each second. The container's `memory.peak`
was 4.4 GB on every chip, almost all of it page cache from the extracted tar. Threads per core: 1
on Graviton4 and AMD, 2 on Intel, so `c8i.4xlarge` is eight cores. mash links no BLAS. Sourmash's
time is the sketch plus a single-threaded Python loop over 910,677 genome–representative pairs
(`jaccard_ani`).

- **Graviton4 and AMD tie on wall time, but they win at different steps.** AMD sketches fastest
  (11.3 s against 13.3). Graviton4 computes the 10M distances fastest (55.5 s against 58.6). AMD is
  also fastest at one thread (the representatives' sketch, 16.0 s against 20.0). Graviton4's
  cheaper hour makes it the cheapest per result, compute and billed.
- **Intel is 1.45× slower at 16 threads.** That is eight SMT cores against sixteen full ones. At
  one thread it sits between the other two (18.1 s).
- **The workload is small for the bill.** Docker install, image pull, staging the 2.3 GB tar and
  the extra invariance runs are about two thirds of each billed window. mash's per-pair cost does not grow with genome
  count, so a bigger genus moves the balance toward compute.
- **Memory doesn't size the box.** Under 1 GiB for 3,158 sketches of 10,000 hashes. The 2.3 GB tar
  in the half-RAM `/tmp` is what needs the 32 GiB.
- n = 1 per cell. Graviton4 ran the same task twice (r1 and r2) and its step times agreed to 0.05 s.
  Every output is identical on every chip, so a repeat only re-measures timing.

## The identities, at scale, on all three chips

| observable | assertion | observed (every chip) |
|---|---|---|
| binary | ELF `e_machine` == `uname -m` | 183 / 62 |
| inputs | sha256 == pins; tar == `genomes.tsv`; reps == GTDB's 321 clusters | 3,158 / 321 |
| **sketch** | **two sketches of every genome, byte-identical** | `ccf6c11f…` both |
| sketches | one per genome, each full (10,000 hashes) | 3,158 |
| rows | exactly N² | 9,972,964 |
| self distance | exactly 0 with 10000/10000 shared, every genome | 3,158 |
| **closed form** | **d == −ln(2j/(1+j))/21, j = shared/10000, at print precision, every row** | 0 off (max rel 4.92e-06 of a 5e-06 rounding bound) |
| symmetry | d(a,b) == d(b,a) and the same shared count, every pair | 4,984,903 pairs |
| **batch + thread invariance** | **reps sketched alone at `-p 1`, screened against all == those rows of the `-p 16` all-vs-all** | 1,013,718 rows, identical |
| **nearest representative** | **== GTDB's species call, ≥ 0.95** | **2,832 of 2,837 = 0.99824** |
| **published ANI** | **per-species mean 100(1−d) to the rep vs GTDB's mean intra-species ANI (skani), Spearman ≥ 0.6, species with ≥ 3 genomes** | **ρ = 0.9776** over 132 |
| sourmash: nearest rep | == GTDB's ≥ 0.95; == mash's ≥ 0.98 | 0.99753; 0.99894 |
| sourmash: ANI to own rep | Spearman vs mash's 1−d ≥ 0.9, every member | ρ = 0.9987 over 2,837 |
| sourmash: published ANI | Spearman vs GTDB's mean ANI ≥ 0.6 | ρ = 0.9757 over 132 |

The floors (0.95, 0.6, 0.98, 0.9) were set before the run as backstops for a broken sketch or
screen. They are not fits to these values.

- **The five genomes mash places differently from GTDB sit between two representatives.** Each is
  within 95% ANI of both, mash's preferred rep is closer by under 0.001 in distance (≤ 0.1 ANI
  points), and GTDB broke the tie with skani ANI and alignment fraction. Three are *C. amycolatum*
  against `GCA_003955985.1`, and one each is *C. phoceense* and *C. tuberculostearicum*. All five
  are among sourmash's seven disagreements, which are the same kind of case. No genome is
  misplaced across a clear species gap. Every one of the 2,837 is within d ≤ 0.05 of its own
  GTDB rep (max 0.0471), and 63 of the 321 representatives have another within 0.05.
- **mash's ANI is skani's to within a fraction of a point, as a rank.** The per-species median
  absolute difference is 0.154 ANI points (range −0.72 to +0.91). mash's distance mixes in gene
  content that skani's aligned-fraction ANI leaves out, so raw values are not asserted. The mean
  ranks agree at ρ = 0.978, and the per-species minimum ANI at 0.942 (observation).
- **244 ordered pairs of distinct genomes have identical sketches** (d = 0, 10000/10000 shared).
  These are clonal isolates and duplicate submissions. The closed-form check takes them as an
  exact case.

## Across chips

- **Identical on all three**: the sketch file, the 9,972,964-row distance table, the screen, and
  every derived table (nearest representative, representative spacing, per-species ANI), plus
  sourmash's screen ([`results/crosschip.txt`](results/crosschip.txt)). Sketching is integer
  hashing and each distance is a closed-form function of an integer count. So this is a fixed
  point with no floating-point path to diverge, and it holds across two builds (aarch.bio arm64,
  bioconda x86). It is asserted (`collect.sh`).

## Caveats

GTDB clusters with skani, which reads the same genomes, so mash is being checked against another
estimate of the same quantity, not against an independent truth. A genus is one slice of GTDB.
The full 199,923-representative set would be a database build, not this all-vs-all. Instance size
and generation were not swept. Spend for this write-up: **$0.43**, of which $0.055 is a first
x86 run whose BusyBox awk stopped on a division by zero (a pair of identical sketches). Graviton4
had passed because mawk returns NaN there. The guard changed `run.sh`, so all three chips were
re-run.
