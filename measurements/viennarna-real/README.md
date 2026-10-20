# ViennaRNA on every Rfam seed sequence — 109,390 RNAs, MFE and partition function, three chips

> **RNAfold returns the same 656,341 lines byte for byte on Graviton4, Intel and AMD, partition
> function included.** Graviton4 is the cheapest result at $0.32, against $0.43 on AMD and $0.62 on
> Intel. The McCaskill partition function equals an exhaustive enumeration of 620 million structures
> to 2e-12 kcal/mol, but only with energy smoothing off; RNAfold's default differs by up to 0.016
> kcal/mol. One `-j16` pass in seven printed an overflowed ensemble for one RNA, with exit 0.

Workload: **every seed sequence of every family in Rfam 15.1**: 4,227 families, 109,390 sequences of
19–4,065 nt, 12.9 Mnt in all. They are folded by `RNAfold -p` (MFE, ensemble free energy, base-pair
probabilities, centroid) at the defaults: `-d2`, 37 °C, Turner 2004. Rfam's curated consensus
structure for each family, mapped onto each sequence, is the reference. Folding a curated ncRNA set
like this is the standard benchmark for a structure predictor, and the length spread (median 84 nt,
307 sequences over 2 kb, rRNAs to 4 kb) is what makes the O(n³) cost real. The recipe's fixture is
50 short sequences folded in under a second.

**Data tier: a versioned immutable release.** `Rfam.seed.gz` from EBI's
`/pub/databases/Rfam/15.1/` (CC0), streamed into the bucket. Rfam publishes no checksum for it, so
[`pins.sha256`](pins.sha256) holds ours (`41f014f4…`), and the task checks it before it runs.

## Run it

```sh
export AWS_PROFILE=aws B=<your bucket>
bash measurements/viennarna-real/stage-inputs.sh "$B"     # Rfam.seed.gz + real.sh + real.py, pinned
for a in arm64 x86_64 amd; do
  spawn task run --spec "$(python3 scripts/spec_arch.py viennarna $a "$B" "$(date +%s)" measurements/viennarna-real/01-fold.task.json)" --wait
done
bash measurements/viennarna-real/collect.sh "$B" <scratch dir>   # results/<chip>/, results/compare.txt
```

The task runs `real.sh 16`, which converts the release to FASTA on the box (`real.py prep`). It then
folds the whole set twice:

```sh
RNAfold -p --noPS --noDP -j16 < seed.fa > fold.out
```

It also folds the first 5,000 records again at `-j1`, checks every record, and enumerates every
structure of the 1,287 sequences of 30 nt or fewer.

## Results

`c8*.4xlarge` (16 vCPU, 32 GiB), `-j16`, the same staged bytes. RNAfold 2.7.2 is aarch.bio's arm64
build on Graviton4 and bioconda's x86 build on Intel and AMD. Wall time is the mean of the two
whole-set folds. Billed $/result is that wall plus the chip's overhead (the billed window minus the
command: Docker install, image pull, staging; 48–61 s), at the hourly price spawn printed.

| chip | instance | cores × threads | fold wall | peak RSS | $/hr | compute $/result | billed $/result |
|---|---|---|---|---|---|---|---|
| **Graviton4** | `c8g.4xlarge` | 16 × 1 | 1,743 s | 3.81 GiB | 0.6381 | **0.309** | **0.320** |
| Xeon 6975P-C | `c8i.4xlarge` | 8 × 2 | 2,935 s | 3.98 GiB | 0.7497 | 0.611 | 0.621 |
| EPYC 9R45 | `c8a.4xlarge` | 16 × 1 | **1,731 s** | 3.85 GiB | 0.8622 | 0.415 | 0.426 |

Each task ran both folds, the `-j1` rerun, the checks and the enumeration. The billed windows were
3,748 s (Graviton4), 6,195 s (Intel) and 3,679 s (AMD). Spend for the three runs plus one sizing
probe on Graviton4 was $3.17.

- **RNAfold `-j` is embarrassingly parallel and keeps every core busy:** 15.99 of 16 cores on average
  on every chip. Graviton4 and AMD take the same time. Intel's 16 vCPU are 8 cores with SMT, so it
  takes 1.68× as long and costs 1.94× as much per result.
- **Graviton4 is the cheapest result,** because AMD's equal speed costs 35% more per hour. The
  answer is identical on all three, so price per hour decides.
- **AMD's two folds took 1,912 s and 1,551 s on the same box,** a 19% spread. Graviton4's two were
  1.4 s apart and Intel's 0.8 s apart. n = 2 per cell; the AMD mean is the honest number.
- **Memory is not the constraint.** RNAfold peaks at 3.8–4.0 GiB with 16 sequences in flight. The
  whole task peaks at 4.5 GiB, and `/tmp` at 0.18 GiB, on a 32 GiB box. `c8g`, the family with the
  least memory per core, is the right one. One 4 kb rRNA takes about 0.3 GiB; memory grows as n².
- **Cost is set by the long tail.** The 622 sequences over 1 kb (0.6% of them) carry 96% of the Σn³ work. A set
  of miRNAs or tRNAs costs a small fraction of this one per sequence.

## What held at scale, on all three chips

| claim | why it must hold | Graviton4 | Xeon | EPYC |
|---|---|---|---|---|
| families converted | the Rfam 15.1 README says 4,227 | 4,227 | same | same |
| records out == sequences in, same order and sequence | nothing dropped or reordered under `-j16` | 109,390 | same | same |
| every MFE and centroid structure is balanced, spans its sequence, and has only canonical pairs and hairpins of ≥ 3 | the search space's definition | 0 bad | 0 | 0 |
| `eval(MFE structure)` == printed MFE, `eval(centroid)` == printed centroid energy | the energy is the structure's, in integer dcal/mol | 109,390 / 109,390 | same | same |
| G_ensemble ≤ MFE | −kT ln Z can never exceed the MFE | 0 violations | 0 | 0 |
| printed MFE frequency == exp(−(E−G)/kT), to the printed precision | one Z behind both numbers | 0 violations | 0 | 0 |
| **no structure in the MFE search space beats the MFE**: Rfam's curated structure mapped onto each sequence, and the centroid | the MFE is a minimum | 0 below, over 105,440 Rfam and 106,712 centroid structures | same | same |
| two `-j16` folds of the whole set | deterministic | byte-identical | same | same |
| first 5,000 records at `-j1` == at `-j16` | threading changes nothing | identical | same | same |
| exhaustive enumeration (`subopt`) of 1,287 sequences ≤ 30 nt: every structure exactly once | count == an independent counting recursion | 620,147,117 structures, 0 mismatches | same | same |
| lowest enumerated energy and its structure == MFE | | 1,287 / 1,287 | same | same |
| **McCaskill G == −kT ln Σ exp(−E/kT)** over the enumeration, `pf_smooth=0` | the partition function's definition | max 2.1e-12 kcal/mol | same | same |
| **McCaskill base-pair probabilities == enumerated** | the same sum, by pair | max 3.1e-12 | same | same |
| RNAfold's printed G == the library's, to its 2 decimals | the CLI prints what the library computes | max 0.0050 | same | same |

The enumeration tolerance is 1e-9: a double-precision sum of up to 3.2M Boltzmann weights carries
about 1e-13 of error, and 1e-9 is still seven orders below the smoothing effect.

**Agreement with Rfam's curated structure** is an observation, not an assertion. RNAfold is a
single-sequence predictor, and Rfam's structures come from comparative analysis. A reference pair
counts only where both columns hold residues that can pair canonically with ≥ 3 nt between them:
2,709,961 of them. 118,001 gapped, 202,354 non-canonical, 3,038 too-short and 2,152 pseudoknot
columns were dropped.

| structure | sensitivity | PPV |
|---|---|---|
| MFE | 0.6880 | 0.4725 |
| centroid | 0.6753 | 0.5293 |

The median per-sequence F1 for the MFE is 0.667. For 7,048 sequences, Rfam's structure has exactly
the MFE energy. Every number in this section is the same on all three chips.

## What the checks found

- **The MFE is a minimum over interior loops of ≤ 30 nt (ViennaRNA's MAXLOOP), not over all
  structures.** 8 centroids have energy *below* the MFE, and every one of them contains an interior
  loop larger than 30 nt. 2,678 centroids and 3,950 mapped Rfam structures have such a loop, so they
  lie outside the space the MFE was minimised over. The bound "nothing beats the MFE" is asserted
  inside that space and holds there for every structure.
- **RNAfold's default partition function is not the Boltzmann sum over its own energies.**
  `pf_smooth` is on by default, and RNAfold has no CLI flag to turn it off. With it on, G differs from
  the enumerated sum on 98 of 1,287 sequences, by up to −0.0163 kcal/mol (`RF01106|L07937.1/6932-6956`).
  With `md.pf_smooth = 0` in the Python API the two agree to 2e-12. If you need exact Boltzmann
  statistics, turn smoothing off in the API.
- **A partition function can overflow under `-j`, and RNAfold still exits 0.** In the sizing probe
  (Graviton4, `-j16`, the set minus two families), one 1,824-nt RNA (`RF02462|CP003008.1/2390583-2392406`)
  came back as ` [100000.00]` with an all-dot centroid and `frequency ... inf`. The only signal was a
  `[WARNING] overflow while computing partition function` line on stderr. The same sequence is finite in all six later `-j16` passes and
  when folded alone. So the overflow depends on what else is being folded at the same time, not on the
  sequence. The task now flags any non-numeric record and refolds it at `-j1`; none appeared in the
  six passes. Every pass prints 46 `pf_scale too large` warnings, and that count is the same on every
  run. **Grep stderr for `overflow` after any `-p -j` batch.** The probe's outputs are kept at
  `runs/viennarna-real/rfam151-probe1/`.

## What differed across chips

**Nothing in RNAfold's output.** `fold.out` is one file on all three chips (`c978404975952937…`),
MFE and ensemble lines alike, and so are the per-record check tables. The MFE is integer dynamic
programming. The partition function is floating point, but it is a fixed point, and nothing moves
past the printed digits. RNAfold links no BLAS (libm, libmpfr, libgmp, libgomp only).

**The enumeration residuals are a path, and they follow the build.** The ~1e-13 differences between
McCaskill and the enumerated sum differ in the last bits on 302 of 1,287 sequences between aarch.bio's
arm64 build and bioconda's x86 build. Intel and AMD, which run the same build, agree exactly. All are
below 3.2e-12, so none comes near the tolerance.

## Caveats

n = 2 per cell (two whole-set folds per task). The billed windows start when spawn's wrapper starts,
after boot. Rfam's consensus structures are curated comparative models, not crystal structures, so
sensitivity and PPV measure agreement with Rfam, not correctness. Pins: [`pins.sha256`](pins.sha256)
(the release and both task scripts). The image digests are the recipe's.
