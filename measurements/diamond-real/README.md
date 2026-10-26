# DIAMOND on every reviewed human protein against all of UniRef50: three chips

> **AMD EPYC is the cheapest place to run this search: 1.74× faster than Graviton4 and 20% cheaper
> per result. Intel costs 35% more than Graviton4.** Every representative query that hits anything
> finds itself at 100% identity, on every chip. Intel and AMD return byte-identical hit tables.
> Graviton4's table differs from theirs in 5 of 8.1 million rows, all for one query.

Workload: **every reviewed human protein** in UniProt release 2026_03 (20,431 Swiss-Prot entries
with `OX=9606`, 11.4M residues) searched with `diamond blastp --very-sensitive` against **the whole
of UniRef50 2026_03** (38,840,027 clusters, 12.18 billion residues). That database is 65× the
SwissProt that [blast-real](../blast-real/README.md) searches. Annotating a proteome against
UniRef50 is a standard DIAMOND job, and a database this size is why researchers pick DIAMOND over
BLAST+. This is the whole job, not a slice of it.

**Data tier: a versioned release.** UniProt 2026_03's `uniref50.fasta.gz` (8.78 GB),
`uniprot_sprot.fasta.gz` and `HUMAN_9606_idmapping_selected.tab.gz` (UniProt's own cluster
membership per human entry). `fetch.py` checks each file's size and md5 against UniProt's
`RELEASE.metalink`, and the sha256 pins are in [`pins.sha256`](pins.sha256). UniProt serves these
from `current_release/` only until the next release, so the bucket copy holds the bytes. `fetch.py`
refuses to fetch once the release notes name a release other than 2026_03.

## Run it

```sh
export AWS_PROFILE=aws B=<your bucket>
bash measurements/diamond-real/stage-inputs.sh "$B"     # 00-fetch: UniProt -> bucket, on AWS, md5- and sha256-checked
for a in arm64 x86_64 amd; do
  spawn task run --wait --spec "$(python3 scripts/spec_arch.py diamond $a "$B" "$(date +%s)" \
    measurements/diamond-real/01-search.task.json)"
done
spawn task run --wait --spec "$(python3 scripts/spec_arch.py diamond arm64 "$B" "$(date +%s)" \
  measurements/diamond-real/02-compare.task.json)"      # the hit tables, compared in the bucket
bash measurements/diamond-real/collect.sh "$B" <nonce>  # results/<chip>/, results/crosschip.txt
```

`01-search` runs [`search.sh`](search.sh) in the recipe's own DIAMOND image, using the recipe's
invocation:

```sh
diamond makedb --in uniref50.fasta.gz -d uniref50 -p 16
diamond blastp -q queries.fa -d uniref50 --very-sensitive --max-target-seqs 500 -p 16 \
  --outfmt 6 qseqid sseqid pident length qlen slen evalue bitscore -o hits.tsv
```

[`mkspec.py`](mkspec.py) writes the three specs and pins the staged scripts by sha256.
`02-compare` ([`compare.sh`](compare.sh)) reads the three chips' 135 MB hit tables, which stay in
the bucket.

## Results

16 threads on a `4xlarge` (16 vCPU, 32 GiB) of each chip. DIAMOND 2.2.6: aarch.bio's build on
Graviton4, bioconda's x86 build on both x86 chips. Same staged bytes on all three:

| chip | instance | `makedb` | `blastp` | tool wall | billed | peak RSS | $/hr | compute $/result | billed $/result |
|---|---|---|---|---|---|---|---|---|---|
| Graviton4 | `c8g.4xlarge` | 125 s | 9,355 s | 9,480 s | 9,975 s | 16.6 GiB | 0.6381 | 1.680 | 1.768 |
| Xeon 6975P-C | `c8i.4xlarge` | 140 s | 10,865 s | 11,005 s | 11,489 s | 17.0 GiB | 0.7497 | 2.292 | 2.393 |
| **EPYC 9R45** | `c8a.4xlarge` | **121 s** | **5,371 s** | **5,492 s** | **5,902 s** | 17.0 GiB | 0.8622 | **1.315** | **1.414** |

A *result* is one whole proteome searched and checked: database build plus search. *Billed* is spawn's
task window, wrapper start to stage-out done, at spawn's printed price. *Peak RSS* is the `blastp` process's own `VmHWM`;
`makedb` peaks at 2.1 GiB. DIAMOND links no BLAS, so there is no OpenBLAS kernel to record. Threads
per core: 1 on Graviton4 and AMD, 2 on Intel (8 cores).

- **AMD wins here, the reverse of BLAST+.** AMD's 16 full cores run the search 1.74× faster than
  Graviton4's 16. That is enough to beat AMD's 35% dearer hour by 20% per result. In
  [blast-real](../blast-real/README.md), `blastp` ran no faster on AMD than on Graviton4, while
  DIAMOND on SwissProt ran 2.3× faster. So the chip to rent depends on the tool, not on the
  workload type. Intel's 8 SMT cores take 1.16× Graviton4's time at a 17% dearer hour.
- **The search is the whole cost.** `makedb` takes about 2 minutes. Everything else (boot, staging
  8.9 GB, the checks) takes 410–495 s, 5–7% of the bill. On the
  [recipe fixture](../../recipes/diamond/README.md), boot is most of it.
- **Size the box by memory, not cores.** `blastp` peaks at 16.6–17.0 GiB at its default
  `--very-sensitive` block size (31 reference blocks of 400M letters). The 16 GiB tmpfs holds the
  staged input. A 32 GiB `4xlarge` fits; a 16 GiB box would not.
- **The timing repeats.** A second Graviton4 run took 9,436 s (0.9% slower) and wrote a
  byte-identical hit table. n = 1 per cell otherwise. DIAMOND is deterministic within a build, so a
  repeat re-measures only timing.

## The identities, at scale, on all three chips

| observable | assertion | observed (every chip) |
|---|---|---|
| database sequences (`dbinfo`) | **== release note's 38,840,027 == FASTA headers**, no duplicate ids | 38,840,027 |
| database letters (`dbinfo`) | **== residues counted from the FASTA** | 12,182,780,325 |
| queries | **== UniProt's 20,431 reviewed human entries**, no duplicates | 20,431 |
| queries without a UniRef50 cluster | **== queries shorter than 11 residues** (UniRef leaves them out) | 8 == 8 |
| clusters UniProt maps a query to | **all present in the FASTA** | 20,423 of 20,423 |
| queries that are their cluster's representative | **mapped to the cluster named after them** | 17,293 |
| queries aligned (DIAMOND's log) | **== distinct queries in the table** | 20,383 |
| every row's `qlen` | **== that query's length** | 8.1M rows, 0 mismatches |
| **representatives with any hit** | **every one hits itself** | **17,271 of 17,271** |
| **those self-rows** | **all at 100% identity** | **17,271** |
| self-alignments spanning the whole query | observation | 16,654 |
| queries whose hits include UniProt's own cluster for them | observation | 20,336 of 20,423 (99.57%) |

- **The self-hit identity holds on a database 100× the recipe's** (38.8M sequences against 382,428). A representative's sequence is in
  UniRef50 verbatim, so a search that finds anything for it must find it. The 22 representatives with no hit at all are
  observed, not explained.
- **UniProt's own clustering is a second, independent truth.** UniRef50 was built by MMseqs2. DIAMOND
  finds the cluster UniProt put each protein in for 99.57% of queries, from a heuristic search
  sharing no code with that build. This is an observation, not a bound: membership is transitive
  through UniRef90, so a member need not align to its representative.
- **The cluster mapping was a guess first.** "Every reviewed protein has a cluster" failed on the
  first Graviton4 run: 8 do not. They are exactly the 8 queries under 11 residues. The assertion now
  states that, and all three chips ran it.

## Across chips

- **Intel and AMD: byte-identical.** Same x86 binary, same hit table (`3a839206…`, 8,107,585 rows),
  same `.dmnd` (`df878d24…`).
- **Graviton4: the same science, slightly different bytes, and the build differs too.** Every count
  in the table above is equal, and so is the set of self-hits (`2b266fe5…`). All 8,107,580 of
  Graviton4's (query, subject) pairs are in the x86 tables. The x86 tables have 5 more, all for one
  query, and 198 shared rows differ in a reported value. The built database differs as well: same
  sequence and letter counts, but a different `.dmnd` and a different DIAMOND database hash (`17cf59a1…`
  against `57695896…`). `makedb` masks sequences as it builds, which is where a build difference could
  enter, but this was not traced. The arm64 build runs only on arm64, so build and architecture
  can't be separated, which is the same finding as [blast-real](../blast-real/README.md)'s DIAMOND
  check.

## Caveats

- One release, one query proteome, one thread count, one mode (`--very-sensitive`, the recipe's).
  Neither instance size nor block size (`-b`) was swept. A larger `-b` trades memory for speed.
- Prices are on-demand us-west-2: spawn's `cost_estimate` divided by the billed window.
- Spend for this write-up: **about $8.30**. That covers the fetch ($0.03), the three counted runs
  ($5.58), the first Graviton4 run ($1.79, which failed only on the cluster-mapping assertion
  above), and about $0.9 for three runs stopped at their check glue. On BusyBox `awk` (the x86
  image), hashing 38.8M ids would have taken hours. `search.sh` now hashes the 20k clusters instead.

## Files

`fetch.py` + `00-fetch.task.json` (UniProt to bucket), `stage-inputs.sh`, `search.sh` +
`01-search.task.json`, `compare.sh` + `02-compare.task.json`, `mkspec.py`, `collect.sh`,
`pins.sha256`. Per-chip evidence is in `results/<chip>/` (`check.txt`, `timing.txt`, `host.txt`,
DIAMOND's logs, spawn's completion record). `results/arm64-run1/` is the first Graviton4 run, and
`results/compare/compare.txt` is the cross-chip table comparison.
