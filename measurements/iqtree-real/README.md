# IQ-TREE on a real gene family — 919 sequences, ModelFinder + UFBoot, three chips

> **AMD EPYC does the same work at 2.8× Graviton4's speed and half its cost, and every chip's
> LG+G4 optimum agrees with RAxML-NG to within 5e-4 log-likelihood units.** The searches do not agree, and
> are not meant to: at `-T 8` IQ-TREE's FreeRate fits vary run to run on one chip, so each run ends
> on its own tree, after its own number of iterations.

Workload: the **whole** Pfam 38.2 seed alignment of `PF01255.25` (Prenyltransf: undecaprenyl
diphosphate synthase and the eukaryotic cis-prenyltransferases), **919 sequences × 533 columns**
from bacteria, archaea and eukaryotes, run the way a gene-family tree is run: ModelFinder, ML search,
1,000 ultrafast bootstraps. Published gene-family trees are this size, hundreds to a few thousand
sequences. The recipe's 114-taxon fixture takes 7 minutes; this took 1.6 to 9.7 hours.

## Run it

```sh
export AWS_PROFILE=aws B=<your bucket>
bash measurements/iqtree-real/stage-inputs.sh "$B"     # Pfam-A.seed.gz -> bucket (md5 vs EBI), PF01255.25 out
spec() { python3 scripts/spec_arch.py "$1" "$2" "$B" "$(date +%s)" "measurements/iqtree-real/$3.task.json"; }
for a in arm64 x86_64 amd; do
  spawn task run --spec "$(spec iqtree $a 01-iqtree)" --wait        # the workload
  spawn task run --spec "$(spec iqtree $a 02-eval)" --wait          # its tree under LG+G4, to a fixed point
  spawn task run --spec "$(spec raxml-ng $a 03-raxml)" --wait       # RAxML-NG at and from that point
done
aws s3 cp "s3://$B/runs/iqtree-real/pf01255/iqtree/real.treefile" "s3://$B/inputs/iqtree-real/g4-ml.treefile"
for a in x86_64 amd; do spawn task run --spec "$(spec iqtree $a 04-eval-g4tree)" --wait; done
spawn task run --spec "$(spec iqtree amd 05-mf-repeat)" --wait      # ModelFinder again, same chip
bash measurements/iqtree-real/collect.sh "$B" <scratch dir>          # results/, results/compare.txt
```

Task 1 runs `real.sh` (pinned by sha256, like every script and input here):

```sh
iqtree3 -s pf01255.25_seed.fa -m MFP -mset LG,WAG,JTT,Q.pfam -B 1000 -T 8 --seed 12345 --prefix real
```

`-mset` keeps ModelFinder to the four general-purpose matrices that suit a nuclear or bacterial
enzyme. The default grid adds 24 more (mitochondrial, chloroplast, viral, clade-specific), each refit
with up to 10 FreeRate categories. `g4-ml.treefile` is pinned in `pins.sha256`.

## Results

8 vCPU (`2xlarge`), `-T 8 --seed 12345`, the same staged bytes. IQ-TREE 3.1.3 is aarch.bio's arm64
build on Graviton4 and bioconda's x86 build on the other two. Run as shipped:

| chip | instance | ModelFinder | search (iterations) | tool wall | billed | peak RSS | $/hr | compute $/result | billed $/result |
|---|---|---|---|---|---|---|---|---|---|
| Graviton4 | `c8g.2xlarge` | 4,305 s | 30,455 s (1,001) | 34,950 s | 35,147 s | 1.53 GiB | 0.3190 | 3.0969 | 3.1144 |
| Xeon 6975P-C | `c8i.2xlarge` | 3,067 s | 24,384 s (1,001) | 27,613 s | 27,706 s | 1.61 GiB | 0.3748 | 2.8748 | 2.8845 |
| EPYC 9R45 | `c8a.2xlarge` | 1,514 s | 4,297 s (400) | 5,868 s | 5,982 s | 1.51 GiB | 0.4311 | 0.7027 | 0.7163 |

The same work on each chip: the identical 29 ModelFinder fits, and the search per iteration.

| chip | cores × threads | kernel (IQ-TREE's own line) | ModelFinder | s / iteration | **$ per ModelFinder + 1,000 iterations** |
|---|---|---|---|---|---|
| Graviton4 | 8 × 1 | `SSE2` | 4,305 s | 30.4 | 3.08 |
| Xeon 6975P-C | 4 × 2 | `AVX+FMA` | 3,067 s | 24.4 | 2.86 |
| **EPYC 9R45** | 8 × 1 | `AVX+FMA` | **1,514 s** | **10.7** | **1.47** |

- **AMD is 2.8× Graviton4 on both phases**, and at 35% more per hour it costs half as much per unit
  of work. Intel is 1.25–1.40× Graviton4 and 7% cheaper. All three kept 7.92–7.96 cores busy.
- **The arm64 build runs IQ-TREE's 128-bit SSE2 kernel; both x86 runs use its 256-bit AVX+FMA one.**
  This compares builds as well as chips. Separating them would need an arm64 build with a native
  vector kernel.
- **Run as shipped, AMD was 4.3× cheaper ($0.72 against $3.11), but most of that is the path, not
  the chip.** AMD's bootstrap converged (correlation 0.990) at 400 iterations. Graviton4 (0.973) and
  Intel (0.981) ran to the 1,001-iteration cap and stopped with IQ-TREE's own "bootstrap analysis did
  not converge" warning. When a run stops is a draw (below), so use the second table to choose a chip.
- **Overhead is 93–197 s** of each bill. Peak RSS is about 1.5 GiB of 16, so the box is sized by
  cores, not memory.
- n = 1 per chip, and a repeat would not reproduce the run (below). The two phase rates are what
  should transfer.

## What held at scale, on all three chips

| claim | why it must hold | Graviton4 | Xeon | EPYC |
|---|---|---|---|---|
| taxa: alignment = IQ-TREE = ML tree = consensus tree | nothing dropped | 919 | 919 | 919 |
| ML tree has 2n−3 branches and n−3 UFBoot supports, all 0..100 | unrooted binary tree | 1,835 / 916 | same | same |
| AIC = −2L + 2k, BIC = −2L + k ln 533, as printed | closed form | yes | yes | yes |
| every ModelFinder row's AIC and BIC from its own lnL and df | closed form, 29 rows | 0 off | 0 off | 0 off |
| chosen model = BIC minimum over all 29 fits | definition | `Q.PFAM+I+R10` | same | same |
| final logL ≥ ModelFinder's logL for that model | the search starts there | −254905.02 ≥ −256067.97 | −254978.23 ≥ −256067.98 | −254959.48 ≥ −256067.97 |
| own ML tree under LG+G4: re-optimising moves logL < 0.001 | it is a fixed point | 2 passes | 3 | 2 |
| RAxML-NG at IQ-TREE's point, nothing optimised | same likelihood function | Δ 5.3e-5 | 5.0e-5 | 9.6e-5 |
| RAxML-NG re-optimising from it | nothing beats the maximum | +4.5e-4 | +6.1e-5 | +1.6e-4 |

Both RAxML-NG bounds are 0.01, the [recipe's](../../recipes/iqtree/README.md) bound, set by what
IQ-TREE prints (logL and alpha to 4 decimals), not by how closely the codes happened to agree.

Observed, not asserted:
- **One `-te` pass is not the likelihood of a tree.** Started from task 1's branch lengths, which
  were fitted under `Q.PFAM+I+R10` and are 25–44% too long for LG+G4, a single IQ-TREE pass stopped
  124–151 units short (Graviton4 −257113.89, optimum −256990.22). A second pass from its own
  output climbed the rest. RAxML-NG's optimiser from the same start stopped 1.52 short on Xeon's tree.
  So when you compare trees or models by re-optimising a fixed topology, repeat the evaluation until
  it stops moving.
- The chosen `+R10` is the largest rate-category count ModelFinder tries by default, so the grid
  edge may be binding for this family.
- Graviton4's search found the best tree, 54 units above EPYC's and 73 above Xeon's. That is the luck
  of the path, not a property of the chip.

## Across chips: what is a fixed point and what is a path

**Fixed points agree exactly.**
- ModelFinder's gamma and invariant fits (`LG`, `+I`, `+G4`, `+I+G4`), and its three smallest
  FreeRate fits (`+R2`, `+R3`, `+I+R2`), are identical to every printed digit on all three chips.
- Graviton4's tree re-optimised under LG+G4 on each chip (task 4) gives −256990.2156 everywhere,
  pass for pass, including the stalled first pass. Its 1,835 branch lengths are byte-identical on
  EPYC and differ by 1e-10 in one branch on Xeon.

**The other 22 FreeRate fits are a path, even on one chip.** They differ from EPYC's by
up to 2.21 lnL on Graviton4 and 0.72 on Xeon. They also differ by 2.13 between two EPYC runs of the
same image with the same seed and thread count (task 5). So neither Xeon nor EPYC can be told apart
from Graviton4 here: the difference is run to run. At this scale, `-T 8 --seed 12345` does not make
IQ-TREE 3.1.3 reproducible. The recipe's fixture, LG+G4 alone at 114 taxa, is byte-identical
across chips.

**Everything downstream follows that path.**
- The three ML trees differ by Robinson-Foulds 606 (Xeon) and 658 (EPYC) of 1,832 from Graviton4's.
- The final logL spans 73 units.
- UFBoot supports on shared splits differ by up to 82 points (mean 4–5).
- The search stopped after 1,001, 1,001 and 400 iterations.

None of these is asserted. The [stochastic-search rule](../../practices/cross-checks.md) would need
a reproducible run first, and this run is not one.

## Caveats

- The `-mset` restriction is a modelling choice for this family; another family may want the full grid.
- The `-te` stall measured here is IQ-TREE 3.1.3 from FreeRate-fitted lengths; other versions and
  models were not tried. Whether `-T 1` restores reproducibility was not tested.
- Prices are on-demand us-west-2, as spawn printed them on these instances.
