# MAFFT on a real gene family — 919 sequences, L-INS-i, three chips

> **L-INS-i returns the same alignment byte for byte on Graviton4, Intel and AMD, and agrees with
> Pfam's curated alignment on 92.76% of Pfam's residue pairs.** That reproducibility comes from
> `--threadit 0`, which runs the refinement on one core. Drop it and the run is 3.7× faster on
> Graviton4, just as accurate, and a different alignment every time, on every chip.

Workload: the **whole** Pfam 38.2 seed of `PF01255.25` (Prenyltransf), 919 sequences of 203–349
residues from bacteria, archaea and eukaryotes, gaps removed and realigned. This is the family
[iqtree-real](../iqtree-real/README.md) builds its tree from, so this is the alignment step that
comes before that tree. A gene-family study aligns hundreds to a few thousand sequences in MAFFT's
accurate mode, and 919 is in that range. The recipe's 906 GPCRs take 3 s under `--auto`; this takes
2 to 8 minutes per alignment, and Pfam's curated seed is a reference to score it against.

## Run it

```sh
export AWS_PROFILE=aws B=<your bucket>
bash measurements/mafft-real/stage-inputs.sh "$B"     # PF01255.25 from iqtree-real's Pfam-A.seed.gz
for a in arm64 x86_64 amd; do
  spawn task run --spec "$(python3 scripts/spec_arch.py mafft $a "$B" "$(date +%s)" measurements/mafft-real/01-align.task.json)" --wait
done
bash measurements/mafft-real/collect.sh "$B" <scratch dir>   # results/<chip>/, results/compare.txt
```

The task runs `real.sh` (pinned by sha256, like both inputs), which aligns the family five times:

```sh
mafft --localpair --maxiterate 1000 --threadit 0 --thread 8 pf01255.25_unaligned.fa   # L-INS-i, twice
mafft --localpair --maxiterate 1000 --thread 8 pf01255.25_unaligned.fa                # threaded refinement, twice
mafft --auto --thread 8 pf01255.25_unaligned.fa                                       # FFT-NS-2
```

## Results

`c8*.2xlarge` (8 vCPU, 16 GiB), `--thread 8`, the same staged bytes. MAFFT 7.525 is aarch.bio's
arm64 build on Graviton4 and bioconda's x86 build on Intel and AMD. Times are the mean of the two
runs of each mode. Billed $/result is the tool wall plus that chip's overhead (the billed window
minus the command: Docker install, image pull, staging; 34–47 s), at the hourly price spawn printed.

| chip | instance | cores × threads | L-INS-i, `--threadit 0` | threaded refinement | peak RSS | $/hr | compute $/result | billed $/result |
|---|---|---|---|---|---|---|---|---|
| Graviton4 | `c8g.2xlarge` | 8 × 1 | 484 s | **131 s** | 0.57 GiB | 0.3190 | 0.0428 / **0.0116** | 0.0470 / **0.0158** |
| Xeon 6975P-C | `c8i.2xlarge` | 4 × 2 | 468 s | 209 s | 0.57 GiB | 0.3748 | 0.0488 / 0.0218 | 0.0537 / 0.0267 |
| EPYC 9R45 | `c8a.2xlarge` | 8 × 1 | **374 s** | 114 s | 0.57 GiB | 0.4311 | 0.0447 / 0.0137 | 0.0488 / 0.0178 |

$/result is given as L-INS-i with `--threadit 0` / with threaded refinement. Each task ran all five
alignments in a billed window of 1,283 s (Graviton4), 1,405 s (Intel) and 1,015 s (AMD). Spend for
the three runs plus a sizing probe was $0.44.

- **With `--threadit 0`, L-INS-i is a serial code.** Pairwise alignment takes about 20–35 s on 8
  threads. The refinement then runs on one core for the rest of the run: 1.28–1.50 cores busy on
  average. The single-core ranking is AMD, then Intel and Graviton4 together (AMD is 1.29× Graviton4).
  Graviton4 is the cheapest per result because its hour costs least, but no chip is more than 14%
  apart, and 7 of every 8 vCPUs you pay for sit idle.
- **Threaded refinement is where the chips separate.** All 8 cores are busy (7.6–7.8). Graviton4
  is 3.7× faster than its own `--threadit 0` run and the cheapest result in the table. Intel's 8 vCPU
  are 4 cores with SMT, so it gains only 2.2× and costs 1.7× Graviton4's billed $/result. AMD takes
  13% less time than Graviton4, at 13% more billed $/result.
- **Memory is not the constraint:** 0.57 GiB anonymous and 0.07 GiB of `/tmp` at peak, on a 16 GiB
  box. The `c8g` family (the least memory per core) is the right one. A smaller box for the
  `--threadit 0` mode was not measured.
- n = 2 per cell. Each repeat finished within 1 s of the first under `--threadit 0`, and within
  2–18 s with threaded refinement.

## What held at scale, on all three chips

| claim | why it must hold | Graviton4 | Xeon | EPYC |
|---|---|---|---|---|
| input == Pfam seed, gaps removed | the staging is lossless | 919 / 207,712 | same | same |
| every row of every output == its input, gaps removed | an aligner may only insert gaps: names, order and residues | 5 of 5 | 5 of 5 | 5 of 5 |
| every row the same length | the definition of an alignment | 5 of 5 | 5 of 5 | 5 of 5 |
| every Pfam residue located in the output | the scorer saw the whole family | 0 unmapped | 0 | 0 |
| scorer control: Pfam scored against itself | SP = TC = 1 exactly, or the scorer is wrong | 1 / 1 | 1 / 1 | 1 / 1 |
| L-INS-i `--threadit 0`, two runs | byte-identical | `a34c93ca…` | same | same |

**Agreement with Pfam's curated alignment** (an observation, not an assertion: an MSA has no
defined precision to set a tolerance from). SP is the fraction of the 88,476,208 residue pairs Pfam
puts in one column that the alignment also puts in one column. TC is the fraction of Pfam's 473
columns it reproduces whole.

| mode | columns | SP | TC |
|---|---|---|---|
| L-INS-i, `--threadit 0` (every chip, every run) | 631 | **0.927568** | 0.264271 |
| L-INS-i, threaded refinement (6 runs) | 625–647 | 0.927580–0.927842 | 0.264271–0.266385 |
| `--auto` → FFT-NS-2 (every chip) | 693 | 0.924346 | 0.234672 |

L-INS-i is better than FFT-NS-2 on both scores: 0.3 points of SP, 3 points of TC, and 62 fewer
columns. Threading the refinement costs nothing in accuracy. Its six runs fall either side of the
reproducible one.

## What differed across chips

**Nothing that is deterministic.** Under `--threadit 0`, L-INS-i output is one file on all three chips
(`a34c93ca15854761…`), and so is `--auto` (`db0911ee5c7a5247…`). Graviton4 on `m8g.2xlarge` in the
sizing probe produced the same `a34c93ca…`. MAFFT links no BLAS (only libc, libm and libpthread), so
no OpenBLAS kernel choice is involved.

**Threaded refinement differs on every run, on every chip, and the chip adds nothing measurable.** Six
runs gave six alignments. Intel against Graviton4 agrees on SP 0.998301 of pairs. AMD against
Graviton4 agrees on 0.998176. Two runs on the same chip agree on 0.998113–0.998471 (`results/compare.txt`).
The difference between chips is no larger than the difference between two runs on one chip. So threaded
refinement follows a different path on each run, and the chip does not change that. An exact assertion
about a threaded-refinement alignment can therefore never be reproduced, and the recipe's
`--auto` column count stays exact because FFT-NS-2 does no refinement.

## Caveats

Pfam's seed is a curated alignment, not a structural truth set such as BAliBASE, so SP and TC measure
agreement with Pfam, not correctness. The
billed windows start when spawn's wrapper starts, after boot. Pins: `pins.sha256`, both inputs from
Pfam 38.2 (md5 against EBI's `md5_checksums`), with the reference byte-identical to iqtree-real's
`pf01255.25_seed.fa` once uppercased and `.`→`-`.
