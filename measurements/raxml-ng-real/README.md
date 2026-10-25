# RAxML-NG on a real gene family — 919 sequences, ML search + bootstrap, three chips

> **Graviton4, Intel and AMD return the same ML tree byte for byte, and IQ-TREE finds the same
> likelihood on it to 3e-4.** AMD EPYC does the work 3.3× faster than Graviton4 for 59% less.
> Intel is 1.26× faster and 7% cheaper. One bootstrap replicate of 20 came out 2e-4 log-likelihood
> units apart between the arm64 and x86 builds, on the same topology.

Workload: the **whole** Pfam 38.2 seed alignment of `PF01255.25` (Prenyltransf: undecaprenyl
diphosphate synthase and the eukaryotic cis-prenyltransferases), **919 sequences × 533 columns**
across bacteria, archaea and eukaryotes. It is run the way a gene-family tree is built: an
adaptive ML search under LG+G4, nonparametric bootstrap, and supports mapped onto the best tree.
Published gene-family trees are this size, hundreds to a few thousand sequences. The recipe's
114-taxon fixture searches in 7.6 minutes. This search took 1.2 to 4.0 hours, and the bootstraps
as long again.

**Data tier: a versioned immutable release, and the same bytes as [iqtree-real](../iqtree-real/README.md).**
That measurement streamed `Pfam-A.seed.gz` from EBI, checked it against EBI's md5, and pinned the
extracted family by sha256 (`d87b1fe2…`, in [`pins.sha256`](pins.sha256)). This measurement reads
that one object, so the two codes are compared on identical input.

## Run it

```sh
export AWS_PROFILE=aws B=<your bucket>
bash measurements/iqtree-real/stage-inputs.sh "$B"       # the alignment, if not already staged
bash measurements/raxml-ng-real/stage-inputs.sh "$B"     # checks it against pins.sha256, stages real.sh + iq.sh
spec() { python3 scripts/spec_arch.py "$1" "$2" "$B" "$(date +%s)" "measurements/raxml-ng-real/$3.task.json"; }
for a in arm64 x86_64 amd; do
  spawn task run --spec "$(spec raxml-ng $a 01-raxml)" --wait    # search, bootstrap, supports, checks
  spawn task run --spec "$(spec iqtree $a 02-iqtree)" --wait     # IQ-TREE on RAxML-NG's best tree
done
bash measurements/raxml-ng-real/collect.sh "$B" <scratch dir>     # results/, results/compare.txt
```

Task 1 runs [`real.sh`](real.sh) with the recipe's own flags:

```sh
raxml-ng --search    --msa pf01255.25_seed.fa --model LG+G4 --threads 8 --workers 1 --seed 12345 --prefix rx
raxml-ng --bootstrap --msa pf01255.25_seed.fa --model LG+G4 --threads 8 --workers 1 --seed 12345 --bs-trees 20 --prefix bs
raxml-ng --support   --tree rx.raxml.bestTree --bs-trees bs.raxml.bootstraps --bs-metric fbp,tbe --prefix sup
```

**20 replicates is a budget choice, not a recommendation.** RAxML-NG's own `--bsconverge` test
says 20 has not converged (average WRF 14.2%, on every chip). Each replicate costs $0.031 on AMD
and $0.073 on Graviton4 (the table below), so price the 100 to 1,000 a paper wants from that.

## Results

8 vCPU (`2xlarge`), `--threads 8 --workers 1 --seed 12345`, the same staged bytes. RAxML-NG 2.0.2 is
aarch.bio's arm64 build on Graviton4 and bioconda's x86 build on Intel and AMD. Tool wall is the
search plus the bootstraps. Billed is spawn's window for the task, at the price spawn recorded.

| chip | instance | search (13 starts) | 20 bootstraps | tool wall | billed | peak RSS | $/hr | compute $/result | billed $/result |
|---|---|---|---|---|---|---|---|---|---|
| Graviton4 | `c8g.2xlarge` | 14,320 s | 16,492 s | 30,812 s | 30,934 s | 0.74 GiB | 0.3190 | 2.7306 | 2.7414 |
| Xeon 6975P-C | `c8i.2xlarge` | 11,448 s | 12,946 s | 24,394 s | 24,511 s | 0.79 GiB | 0.3748 | 2.5400 | 2.5521 |
| **EPYC 9R45** | `c8a.2xlarge` | **4,269 s** | **5,092 s** | **9,361 s** | 9,449 s | 0.80 GiB | 0.4311 | **1.1209** | **1.1315** |

The same work on each chip, per unit:

| chip | cores × threads | SIMD kernel (RAxML-NG's own line) | s / ML search | s / replicate | **$ / replicate** |
|---|---|---|---|---|---|
| Graviton4 | 8 × 1 | `SSE3` | 1,102 | 825 | 0.0731 |
| Xeon 6975P-C | 4 × 2 | `AVX2` | 881 | 647 | 0.0674 |
| **EPYC 9R45** | 8 × 1 | `AVX2` | **328** | **255** | **0.0305** |

- **AMD is 3.3× Graviton4 on both phases**, and at 35% more per hour it costs 59% less per
  result. Intel is 1.25–1.27× Graviton4 and 7% cheaper, on 4 physical cores against 8.
- **The arm64 build runs RAxML-NG's 128-bit `SSE3` kernel; both x86
  runs use the 256-bit `AVX2` one.** So this compares builds as well as chips, the same caveat as
  [iqtree-real](../iqtree-real/README.md), where AMD led by a similar 2.8×.
- **The ratio is the same on both phases**, so it lives in the likelihood kernel, not in the search.
  A bootstrap replicate costs about three quarters of one search start.
- **Overhead is 88–122 s of a 2.6–8.6 hour bill.** Peak memory is under 1 GiB of 16
  (`--parse` estimated 580 MB), so the box is sized by cores. RAxML-NG recommends 6 threads for
  517 patterns; 8 is the recipe's pin, kept for comparability.
- n = 1 per chip. The searches are deterministic (below), so a repeat is the same run.

## What held at scale, on all three chips

| claim | why it must hold | Graviton4 | Xeon | EPYC |
|---|---|---|---|---|
| taxa, sites read = FASTA | nothing dropped | 919 / 533 | same | same |
| patterns, gap %, invariant % = independent count | RAxML-NG's compression matches the alignment | 517 / 57.59 / 12.20 | same | same |
| tips in best tree | every taxon placed | 919 | 919 | 919 |
| free parameters = 2n − 2 | 2n − 3 branches + alpha | 1,836 | 1,836 | 1,836 |
| AIC = 2k − 2lnL, BIC = k ln 533 − 2lnL | closed form | yes | yes | yes |
| final lnL = best of the 13 searches | definition | −256799.798588 | same | same |
| one ML tree and one start tree per search | conservation | 13 / 13 | same | same |
| lnL at its own optimum, nothing optimised | the likelihood function reproduces itself | Δ 3e-6 | 3e-6 | 3e-6 |
| re-optimising from the optimum | nothing beats the maximum (< 0.01) | +3.8e-4 | same | same |
| **IQ-TREE on RAxML-NG's tree, LG+G4, to a fixed point** | **same likelihood, unrelated code (< 0.01)** | **Δ 2.9e-4** | **2.9e-4** | **2.9e-4** |
| 20 replicates, each with all 919 tips | conservation | 20 | 20 | 20 |
| 916 = n − 3 FBP supports, each = round(100 × share of replicates holding the split) | recomputed from the replicate trees | 916 of 916 | 916 of 916 | 916 of 916 |
| completion sentinel, LG+G4m over sites 1–533 | clean finish, model honoured | yes | yes | yes |

The IQ-TREE bound is 0.01, the [recipe's](../../recipes/raxml-ng/README.md): IQ-TREE prints lnL to
4 decimals, so the print sets it. IQ-TREE starts at RAxML-NG's branch lengths and is at its fixed
point after one pass, which is the evidence that RAxML-NG's optimum is a maximum for both codes.
The two codes' alpha differs in the third decimal (1.00158 against 1.0000), so the likelihood is
flat there.

**Not checkable from the tool: TBE.** RAxML-NG 2.0.2 writes FBP ÷ 100 into `supportTBE` for every
branch, on every chip, with `--bs-metric tbe` alone too. Recomputing the transfer bootstrap from the
replicate trees here gives values up to 0.97 higher. It matches on the 275 cherries, where TBE and
FBP coincide by definition, and that is the check that the recomputation is right. The in-task
`TBE ≥ FBP` check passes on this output and cannot catch it. On this `--support` path 2.0.2 never computes
TBE: `command_support` hands `draw_bootstrap_support` the replicates' plain split counts, which it
copies for every metric (`main.cpp:2800`, `:2626`; from `995e191`), so TBE prints as FBP ÷ 100.
Until [amkozlov/raxml-ng#236](https://github.com/amkozlov/raxml-ng/issues/236) is fixed, compute
TBE another way.

## Across chips: what is a fixed point and what is a path

**The whole search is identical on all three.** All 13 per-search log-likelihoods match to six
decimals. The best tree, the 13 ML trees, the 13 start trees, the model and the FBP support tree
are byte-identical between Graviton4, Xeon and EPYC, although the arm64 build runs `SSE3` and the
x86 build `AVX2`. The pinned seed and thread count make RAxML-NG 2.0.2 reproducible at 919 taxa,
across architectures. [IQ-TREE](../iqtree-real/README.md) at the same scale is not reproducible even
on one chip.

**One bootstrap replicate is a path.** Xeon and EPYC match each other byte for byte on all 20
replicates. Replicate 11 on Graviton4 has the same topology and a log-likelihood 2.3e-4 apart
(−261103.182047 against −261103.182273): the branch lengths took a different last step. Nothing
downstream moved, so the supports are identical. A longer run could see a replicate whose
topology differs, so cross-chip agreement on supports is reported, not asserted.

## Caveats

- 20 replicates have not converged. The support values are real but coarse (5% steps) and are not
  the supports this family's tree would be published with.
- The SIMD kernel differs by build, so the speed ratios compare aarch.bio's arm64 build against
  bioconda's x86 one, not the silicon alone.
- Prices are on-demand us-west-2, as spawn recorded them on these instances.
