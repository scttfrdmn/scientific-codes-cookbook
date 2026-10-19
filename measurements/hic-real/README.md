# pairtools + cooler on a whole 4DN Hi-C experiment — Graviton4, Intel and AMD

> **Graviton4 is the cheapest place to run this pipeline: $0.40 per verified result, against $0.42 on
> AMD and $0.46 on Intel.** AMD is 1.36× faster (1,168 s of tool time against 1,588), but its hour costs
> 35% more. On all three chips the pipeline rebuilds 4DN's published GM12878 matrix exactly: every pixel
> at all 13 resolutions, 1 kb to 10 Mb, and the ICE weights to within 1.5e-15.

Workload: a **complete 4DN experiment set**, [4DNESLLA3R1V](https://data.4dnucleome.org/experiment-set-replicates/4DNESLLA3R1V/).
That is in situ Hi-C on GM12878 (MboI), two biological replicates, 171.4M deduplicated pairs, which
become 151.4M contacts after the mapq filter. The recipe runs 4DN's own post-alignment steps on it,
with pairtools 1.1.3 and cooler 0.10.4: merge the replicates, filter, load at 1 kb, zoomify, balance.
The result is compared with what 4DN published from the same lists. One deeply sequenced library pair
is the unit a Hi-C user processes, and this is a whole one, not a region.

**Data tier: RODA.** The 4DN Data Portal's open-data bucket, `s3://4dn-open-data-public`. These are 4DN
pipeline v0.3.0 outputs (2024 reprocessing, GRCh38):

| file | what | records |
|---|---|---|
| `4DNFILGIU5G7.pairs.gz` | replicate 1, deduplicated by pairtools 1.1.0 | 89,084,119 |
| `4DNFI4EZMKAO.pairs.gz` | replicate 2 | 82,325,899 |
| `4DNFIHMMV629.pairs.gz` | 4DN's combined list: both merged, then `mapq1>30 and mapq2>30` | 151,438,963 |
| `4DNFIFBPTHCU.mcool` | 4DN's matrix from the combined list, 13 resolutions, balanced | — |
| `4DNFI823LSII.chrom.sizes` | hg38 main chromosomes, used by every step | 25 |

Each file is pinned twice. [`inputs.md5`](inputs.md5) holds the md5 the portal publishes, and
[`inputs.sha256`](inputs.sha256) holds ours. Both are checked on the box before anything runs.

## Run it

```sh
export AWS_PROFILE=aws B=<your bucket>
bash measurements/hic-real/stage-inputs.sh "$B"     # S3-to-S3 from 4dn-open-data-public, 7.1 GB
for a in arm64 x86_64 amd; do                       # each 02 reads its own chip's 01 output
  for t in 01-pairs 02-cool; do
    spawn task run --wait --spec "$(python3 scripts/spec_arch.py hic $a "$B" r2 measurements/hic-real/$t.task.json)"
  done
done
bash measurements/hic-real/collect.sh "$B" r2 r2    # results/<chip>/, results/crosschip.txt
```

[`pairs.sh`](pairs.sh) is task 1 (pairtools image), [`cool.sh`](cool.sh) and [`ident.py`](ident.py)
are task 2 (cooler image), and [`mkspec.py`](mkspec.py) writes both specs with the scripts pinned by
sha256. The commands, as the tasks run them:

```sh
pairtools merge --nproc 16 --memory 8G --tmpdir /var/tmp/hic 4DNFILGIU5G7.pairs.gz 4DNFI4EZMKAO.pairs.gz \
  | pairtools select 'mapq1>30 and mapq2>30' -o /tmp/out/combined.pairs.gz
cooler cload pairs -c1 2 -p1 3 -c2 4 -p2 5 4DNFI823LSII.chrom.sizes:1000 combined.pairs.gz /var/tmp/hic/base.cool
cooler zoomify -p 16 -r 1000,2000,5000,10000,25000,50000,100000,250000,500000,1000000,2500000,5000000,10000000 \
  -o /tmp/out/ours.mcool /var/tmp/hic/base.cool
cooler balance -p 16 --force --max-iters 1000 --ignore-diags 2 --mad-max 5 --min-nnz 10 --min-count 0 \
  --tol 1e-05 /tmp/out/ours.mcool::resolutions/10000      # and at 100000 and 1000000
```

## Results

16 threads on a `4xlarge` (16 vCPU, 32 GiB) of each chip. Same image digests (aarch.bio on Graviton4,
bioconda's x86 builds of the same versions on both x86 chips), same staged bytes:

| chip | instance | pairtools | cooler | billed | peak RSS | $/hr | compute $/result | billed $/result |
|---|---|---|---|---|---|---|---|---|
| **Graviton4** | `c8g.4xlarge` | 1,065 s | 523 s | 2,242 s | 8.0 GiB | 0.6381 | 0.282 | **0.397** |
| Xeon 6975P-C | `c8i.4xlarge` | 1,071 s | 502 s | 2,215 s | 8.0 GiB | 0.7497 | 0.328 | 0.461 |
| EPYC 9R45 | `c8a.4xlarge` | **801 s** | **367 s** | **1,759 s** | 8.0 GiB | 0.8622 | **0.280** | 0.421 |

A *result* is both tasks, every check passed. *pairtools* is the wall time of task 1's concurrent block
(merge+filter and three dedup checks; the longest is a dedup piped into a second dedup). *cooler* is
`cload` + `zoomify` + three `balance` runs. *Billed* is launch to terminate for both tasks, at spawn's
printed price. *Peak RSS* is the largest `ru_maxrss` of any step. That is GNU `sort`'s 8 GiB buffer
under `pairtools merge --memory 8G`, a setting, not a need. `cload` peaks at 3.2–3.7 GiB. Threads per
core: 1 on Graviton4 and AMD, 2 on Intel. OpenBLAS: `neoversev2` on Graviton4, `Cooperlake` on both x86
chips. ICE balancing uses numpy elementwise and sparse ops, not GEMM.

- **Graviton4 wins on cost, AMD on time.** AMD's tool time is 26% shorter and its compute cost per
  result is level with Graviton4's. Its billed cost is 6% higher, because boot and staging are paid at
  its dearer hour too. Intel's 8 cores × 2 threads match Graviton4's 16 cores on wall time here, but
  its hour is 17% dearer.
- **The work is single-threaded Python, run side by side.** `pairtools dedup` runs at about 160k
  pairs/s per process on Graviton4 with its default chunk. The 16 vCPU are used by running eight steps at once, not by any
  one tool. `cooler cload pairs` is serial too, at 286 s on Graviton4 and 183 s on AMD.
- **The box is sized by staging, not by compute.** Task 1 stages 6.0 GB of inputs and writes a 3.1 GB
  list into `/tmp`, a tmpfs of half the RAM. A `2xlarge` has 8 GiB of tmpfs, so it cannot hold task 1.
- **29–34% of the bill is boot, staging and checks.** That share includes the 360 s sorted-multiset
  comparison with 4DN's list (below). These timings are not compute cost.
- n = 1 per cell. Every step is deterministic except the parallel balance, which varies at the
  1e-15 level, so a repeat re-measures only timing.

## The identities, at scale, on all three chips

| observable | assertion | observed (every chip) |
|---|---|---|
| staged bytes | md5 == 4DN's published, sha256 == pinned | all 5 |
| **merge conserves, then the filter** | our merged + filtered list **== 4DN's combined list, as a multiset** | 151,438,963 records, same sorted-body md5 |
| sort-key order (`chrom1 pos1 chrom2 pos2`) | **identical to 4DN's, line for line** | same md5 |
| dedup bookkeeping | **`total == nodups + dups`**, every run | 3 of 3 |
| **dedup is idempotent** | `dedup(dedup(rep1))` **finds 0** | 0 of 89,084,106 |
| **planted duplicates** | rep2 merged with itself: **dups == 82,325,899**, kept list **byte-identical to rep2** | both |
| **load conserves** | `.cool` total **== task 1's record count**, at every resolution | 151,438,963 × 13 |
| **4DN's published matrix** | bins and **every pixel** (`bin1_id`, `bin2_id`, `count`) **== 4DNFIFBPTHCU**, all 13 resolutions | 113.8M pixels at 1 kb … 49,038 at 10 Mb |
| upper triangle | **no pixel with `bin1 > bin2`** | 0 |
| symmetry | genome-wide 1 Mb matrix **`M == M.T` exactly** | 0.0 over 8,007,979 nonzeros |
| **ICE weights vs 4DN's** | same masked bins; max rel diff **< 10·√tol** (3.2e-2) | 0 at 1 Mb, ≤ 1.5e-15 at 100 kb and 10 kb |
| 4DN's 1.1.0 lists re-deduplicated by 1.1.3 | observation | rep1: 13, rep2: 0 |

- **The published matrix is reproduced exactly, and it is the strongest check here.** 4DN's pipeline built
  `4DNFIFBPTHCU` from these same lists. Matching every pixel at
  every resolution checks the merge, the filter, `cload`'s binning and `zoomify`'s coarsening together
  against an independent run. This is the [published-result](../../practices/cross-checks.md) form of
  cross-validation. The conservation identities then hold at 15,000× the recipe's 9,998 contacts.
- **Our list holds the same records as 4DN's in a different byte order.** The two files agree on every
  record and on the sort-key sequence, and differ only in how lines with equal keys are ordered. A
  byte comparison would have failed on a tie-break; the multiset and the key order are the claims that
  matter downstream, and the matrix identity confirms it.
- **The ICE bound comes from the stop rule, not from the agreement.** Balancing stops when the variance
  of the marginals is below `tol` (4DN recorded `tol=1e-05`, `mad_max=5`, `min_nnz=10`,
  `ignore_diags=2` on its weights, and the task reads them from there). Two converged solutions can
  differ by order √tol. Observed agreement is 13 orders tighter, because both runs perform the same
  iterations.
- **Duplicate removal is exercised, both ways.** It removes exactly the 82,325,899 planted copies and
  keeps rep2 byte for byte. On its own output it finds nothing.
- **pairtools 1.1.3 finds 13 duplicates in 4DN's already-deduplicated rep1.** They sit at two pileups
  on chr16 (34,583,087 and 46,390,698–9). A 100× larger chunk and 1,000× larger carryover finds the same
  13, so our result does not depend on dedup's streaming window. Whether 4DN's run (over the much larger
  pre-dedup pileup) did is not testable from the published files. It is an observation, not an
  identity, and the cause is not established.

## Across chips

- **Every count, digest and pixel is identical on all three.** The combined list's body md5
  (`2b6b6782…`), the planted run's kept list, the 13 re-found duplicates, and the sha256 of every pixel
  array at all 13 resolutions match on Graviton4, Intel and AMD, across two builds (aarch.bio arm64,
  bioconda x86). These are integer results, a fixed point, and `collect.sh` asserts them equal.
- **ICE weights: identical at 1 Mb, within 9.4e-16 relative at 100 kb and 10 kb, with the same masked
  bins.** That gap is not the chip. Two runs on the same Graviton4 differ at the same level (1.46e-15
  against 1.22e-15 from 4DN's at 100 kb), because `balance -p 16` sums chunks in arrival order. It is a
  converged fixed point, and the comparison is reported, not asserted.

## Caveats

One experiment set and one instance size. Threads, size and generation were not swept. The pipeline
starts from 4DN's deduplicated pairs, not from FASTQ, so alignment and `pairtools parse` are not
covered (the [recipe](../../recipes/hic/README.md) says the same). Spend for this write-up: **$1.63**,
including one failed task 1 and one probe of task 2.
