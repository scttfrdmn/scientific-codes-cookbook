# MrBayes run to convergence: 114 taxa, LG+G4, 8 MPI ranks, three chips

> **Every chip's chain converged on its own stop rule at the same generation (560,000), every chip's
> consensus has the same 106 splits, and all 74 splits with posterior ≥ 0.95 are in the ML tree.**
> AMD EPYC does it 3.7× faster than Graviton4 for 37% of the price per result. Intel and AMD run
> one identical chain. Graviton4's matches them for 549,500 generations of one run and all of the other.

Workload: the [recipe's](../../recipes/mrbayes/README.md) own alignment, Pfam 38.2 `PF26127.1` seed
(114 sequences × 477 columns, staged by [iqtree](../../recipes/iqtree/README.md)), run the way a
Bayesian protein tree is run for a paper: `mb-mpi`, 2 runs × 4 chains on 8 ranks, sampling until
ASDSF < 0.01. The recipe stops at 100,000 generations (ASDSF 0.038), so its consensus is not a
posterior anyone would publish. The chain was the short part, not the data. 114 taxa is a typical
MrBayes analysis, and these are the bytes the two ML recipes already ran, so the converged
posterior can be checked against both ML trees. The 919-taxon alignment of
[iqtree-real](../iqtree-real/README.md) is beyond Bayesian convergence on any budget here.

## Run it

```sh
export AWS_PROFILE=aws B=<your bucket>
make stage RECIPE=iqtree && make stage RECIPE=raxml-ng   # once; the alignment and both ML trees
bash measurements/mrbayes-real/stage-inputs.sh "$B"      # scripts + the ML results, pinned
spec() { python3 scripts/spec_arch.py mrbayes "$1" "$B" "$(date +%s)" "measurements/mrbayes-real/$2.task.json"; }
for a in arm64 x86_64 amd; do spawn task run --wait --spec "$(spec $a 01-mcmc)"; done
aws s3 cp "s3://$B/runs/mrbayes-real/mcmc/mb.consensus.nwk" "s3://$B/inputs/mrbayes-real/graviton4/mb.consensus.nwk"
for a in arm64 x86_64 amd; do spawn task run --wait --spec "$(spec $a 02-compare)"; done
bash measurements/mrbayes-real/collect.sh "$B"           # results/<chip>/, plus the chain diff
```

`01-mcmc` runs `mcmc.sh` (pinned by sha256 with every input in `pins.sha256`):

```text
lset rates=gamma ngammacat=4;  prset aamodelpr=fixed(lg);
mcmcp ngen=5000000 samplefreq=500 nchains=4 nruns=2 diagnfreq=10000 relburnin=yes burninfrac=0.25
      stoprule=yes stopval=0.01 seed=20261019 swapseed=20261019;   mcmc;  sumt;  sump;
mpirun --oversubscribe --bind-to none -np 8 mb-mpi run.nex
```

`02-compare` runs `compare.sh` in the iqtree image. `splits.awk` turns a Newick tree into its
bipartitions, so the trees are compared split by split.

## Results

8 vCPU (`2xlarge`), 8 ranks, the same seed and staged bytes. MrBayes 3.2.8: aarch.bio's build on
Graviton4, bioconda's on the other two. Both images ship `mb-mpi` (Open MPI 4.1.6).

| chip | instance | generations | tool wall | billed | peak RSS | $/hr | compute $/result | billed $/result |
|---|---|---|---|---|---|---|---|---|
| Graviton4 | `c8g.2xlarge` | 560,000 | 6,528 s | 6,648 s | 0.48 GiB | 0.3190 | 0.5785 | 0.5891 |
| Xeon 6975P-C | `c8i.2xlarge` | 560,000 | 6,101 s | 6,250 s | 0.48 GiB | 0.3748 | 0.6352 | 0.6508 |
| **EPYC 9R45** | `c8a.2xlarge` | 560,000 | **1,765 s** | **1,833 s** | 0.48 GiB | 0.4311 | **0.2113** | **0.2195** |

| chip | cores × threads | likelihood calculator (MrBayes' own line) | generations / s |
|---|---|---|---|
| Graviton4 | 8 × 1 | standard non-SSE, single precision | 85.8 |
| Xeon 6975P-C | 4 × 2 | SSE, single precision | 91.8 |
| EPYC 9R45 | 8 × 1 | SSE, single precision | 317.3 |

- **AMD is 3.7× Graviton4 and 2.7× cheaper per converged posterior.** Intel's 4 SMT cores match
  Graviton4's 8 at 17% more per hour. All three ran exactly 560,000 generations, so the rows compare
  the same work.
- **The arm64 build has no vector likelihood kernel; both x86 runs use MrBayes' SSE one.** So
  this compares builds as well as chips, as in [iqtree-real](../iqtree-real/README.md).
- **MPI pays.** The recipe's serial `mb` ran 16.4 gen/s on `c8g.large`. `mb-mpi` gives each of the 8
  chains a rank: 85.8 gen/s, 5.2×, for 4× the hourly price. Peak memory is under 0.5 GiB, so the
  box is sized by chains (one core each), not memory.
- Overhead is 68–149 s of each bill. Where the chain stops is a property of the seed, not the
  chip: a different seed converges at a different generation.
- n = 1 per chip. A fixed seed on the same chip reproduces `.p` and `.t` exactly (asserted), so a
  repeat would give the same numbers.
- Probes (20,000 generations per chip) and the three compare tasks cost $0.089. Total spend $1.55.

## What held at scale, on all three chips

| claim | why it must hold | Graviton4 | Xeon | EPYC |
|---|---|---|---|---|
| 8 MPI ranks, parallel build | MrBayes' own banner | yes | yes | yes |
| model `Fixed(LG)`, 4 gamma categories | like-with-like vs the ML trees | yes | yes | yes |
| stopped by its own rule, ASDSF < 0.01 | the run's bound | 0.009557 | 0.009389 | 0.009389 |
| sumt's ASDSF from the written `.t` == the sampler's last | same samples, same burn-in | equal | equal | equal |
| `.p` rows and `.t` trees per run == 560,000/500 + 1 | sampling arithmetic | 1,121 ×2 | same | same |
| topology posteriors sum to 1.000 | conservation | 1,677 topologies | same | same |
| majority-rule consensus == the splits `.tstat` puts above 0.5 | definition | 106 = 106 | same | same |
| MrBayes lnL at IQ-TREE's tree, lengths, alpha vs IQ-TREE `-blfix` there | same likelihood | Δ 2.3e-4 | 2.2e-4 | 2.2e-4 |
| same at RAxML-NG's point, vs IQ-TREE and vs RAxML-NG's own | same likelihood | Δ 4.8e-4 / 4.7e-4 | 4.9e-4 / 4.8e-4 | same |
| best state either cold chain ever visited ≤ the ML point | nothing beats the maximum | −52795.45 ≤ −52706.73 | same | same |
| same seed, same 8 ranks → identical `.p` and `.t` | determinism | yes | yes | yes |

The likelihood bound is 0.01. MrBayes computes in single precision, and one float32 rounding of a
52,707-unit total is 3.1e-3. That bound is set by the arithmetic, not by how closely the codes
happened to agree. The ML point is IQ-TREE's tree and RAxML-NG's own, which are the same 111 splits
(asserted). The posterior mean lnL is 99 below it (MrBayes' arithmetic mean, −52806.01), close to
the k/2 = 113 expected for 226 free branch-length and shape parameters.

Against the ML tree (reported, not asserted):

| | Graviton4 | Xeon | EPYC |
|---|---|---|---|
| consensus splits in the ML tree | 102 of 106 | same | same |
| highest posterior of a split the ML tree lacks | 0.659 | 0.655 | 0.655 |
| splits with posterior ≥ 0.95 / of those, missing from the ML tree | 74 / **0** | same | same |
| ML splits the consensus leaves unresolved | 9 | same | same |
| TL / alpha: PSRF | 1.001 / 1.000 | 1.001 / 0.999 | same as Xeon |
| TL / alpha: min ESS | 201 / 678 | 310 / 708 | same as Xeon |

**Where the posterior is confident, it agrees with ML.** The four consensus splits ML lacks all have
posterior ≤ 0.66.

## Across chips: what is a fixed point and what is a path

**The fixed point agrees to the calculator's precision.** MrBayes' lnL at IQ-TREE's point is
−52706.730467 on Graviton4 and −52706.730477 on both x86 chips (1.0e-5). At RAxML-NG's point it is
−52706.731880 and −52706.731885. That gap is the non-SSE and SSE kernels rounding differently in
single precision. Intel and AMD agree to every digit.

**The chain is a path, and it stayed on one path a long way.** Intel's and AMD's `.p` files are
byte-identical: the same binary on two vendors. Graviton4's run 1 matches them on all 1,121 samples.
One logged lnL differs in its seventh significant figure (gen 247,000: −5.282386e+04 vs
−5.282387e+04), and the chain did not move. Run 2 matches through generation 549,500. From 550,000
its last 21 samples differ, presumably where a 1e-5 lnL difference first tipped a Metropolis
decision. So the diagnostics differ in the fourth figure: ASDSF 0.009557 against 0.009389, TL's min
ESS 201 against 310. All three runs stopped at the same check.

**What a reader takes from the posterior is chip-independent.** The consensus split set is
identical on all three chips. Split posteriors differ by at most 0.008, on 25 of the 106 splits.
None of the cross-chip differences is asserted. The [stochastic-search
rule](../../practices/cross-checks.md) allows an exact assertion only on one build, and the
determinism check is that.

MrBayes does not link a BLAS, so the [OpenBLAS kernel
question](../../patterns/cross-architecture.md) does not arise.

## Caveats

- One seed. The convergence generation depends on it. The recipe's 1/√n guess of ~1.5M generations
  overestimated this seed's 560,000 by 2.7×.
- ASDSF < 0.01 is the conventional stopping rule, not a proof of convergence. PSRF ≈ 1 and ESS ≥ 200
  on both parameters, here, are consistent with it.
- `usebeagle=no`, as in the recipe; BEAGLE's CPU kernels were not tried.
- The Graviton4 and Intel runs used `cost_limit` 1.50 (base), the AMD run 1.45 (same TTL). Actual
  spend was well under each.
- Prices are on-demand us-west-2: spawn's `cost_estimate` divided by the billed window.

## Files

`mcmc.sh`, `compare.sh`, `splits.awk` (the tasks), `stage-inputs.sh`, `mkpins.sh`, `pins.sha256`,
`mkspec.py` (writes the specs), `collect.sh`. `results/<chip>/mcmc/` holds `score.tsv`,
`checks.txt`, `mb.out`, the `.p` files and the MrBayes summaries. The `.t` samples are in the
bucket. `results/<chip>/compare/` holds the comparison.
