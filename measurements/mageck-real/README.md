# MAGeCK on a real genome-wide screen: Brunello in A375, Graviton4 vs Intel vs AMD

> **253M reads, 77,441 guides: the count table equals an independent recount exactly, and
> every output but MLE's permutation p-value is byte-identical on all three chips.** AMD is
> 2.2× faster than Graviton4 and 37% cheaper per billed result. MAGeCK is single-threaded Python, and
> the arm64 Python build has no PGO or LTO.

Workload: the **complete** Brunello dropout screen in A375 from Sanson et al. 2018
(*Nat Commun* 9:5416, PRJNA508200). That is the plasmid library (60.1M reads) and both
end-point replicates (99.1M and 93.6M), against the full 77,441-guide library. It is the
screen Broad used to show Brunello separates essential from non-essential genes. It is not
subsampled, so it is the size of a real CRISPR-screen analysis.

## Run it

```sh
export AWS_PROFILE=aws
bash measurements/mageck-real/stage-inputs.sh "$COOKBOOK_BUCKET"   # fetches from ENA on AWS, once
for a in arm64 x86_64 amd; do
  spawn task run --spec "$(python3 scripts/spec_arch.py mageck $a "$COOKBOOK_BUCKET" "$(date +%s)" measurements/mageck-real/screen.task.json)" --wait
done
bash measurements/mageck-real/compare.sh "$COOKBOOK_BUCKET" r3   # cross-chip sha256 of every output
```

One task per chip runs `run.sh` (sha256-pinned). It runs `mageck count`, `mageck test`
(RRA) and `mageck mle`, then the comparisons, an independent recount (`recount.py`) and
`check.py`. The x86 legs use the recipe's `images_x86_64` pin: the same 0.5.9.5 package
from bioconda.

## Results

8 vCPU, one `2xlarge` per chip, same input bytes, `mle --threads 8`:

| chip | instance | tool wall: count / RRA / MLE | tool wall | overhead | billed | $/hr | compute $/result | **billed $/result** | peak RSS |
|---|---|---|---|---|---|---|---|---|---|
| Graviton4 | `m8g.2xlarge` | 1224 / 11 / 86 s | 1321 s | 99 s | 2951 s | 0.3590 | 0.132 | **0.294** | 2.05 GiB |
| Xeon 6975P-C | `m8i.2xlarge` | 738 / 8 / 91 s | 837 s | 107 s | 1864 s | 0.4234 | 0.098 | **0.219** | 1.92 GiB |
| **EPYC 9R45** | `m8a.2xlarge` | **530** / 7 / 55 s | **592 s** | 122 s | 1360 s | 0.4869 | **0.080** | **0.184** | 1.78 GiB |

*Billed* is spawn's task window, wrapper start to stage-out done
([`results/billed-windows.txt`](results/billed-windows.txt)), at spawn's printed price. It holds
about 1,500, 1,000 and 700 s of verification (a second count, four extra tests and the recount)
that a user's own run would not pay for. *Overhead* is boot, Docker install, image pull and
staging 7.7 GB in and out: the measured windows (2987, 1908 and 1427 s; launch to terminate per
CloudTrail on Intel and AMD, unattributed on Graviton4) minus the task's command.

- **`mageck count` is 93% of the tool wall, and it runs on one core.** Average busy cores were
  1.2–1.45 of 8, the extra coming from MLE's workers. So this is a single-thread race, and
  the 8 vCPU only buy 16 GiB of tmpfs for the 7.7 GB of FASTQ. More cores buy `count`
  nothing.
- **AMD is 2.23× and Intel 1.58× faster than Graviton4, and both are cheaper per result**
  despite the higher hourly price: 39% and 25% less compute $, 37% and 26% less billed.
  Graviton4 is the most expensive chip for this code.
- **Part of the gap is the image, not the chip.** The arm64 image's CPython (conda-forge
  3.12.13, cross-compiled) was built without `--enable-optimizations` or `--with-lto`. The
  x86 images' CPython 3.12.11 has both (PGO + full LTO). `interp.py` times mageck count's two
  halves on the same bytes in each image, single-threaded:

  | | gzip `'rt'` line read | 9-offset slice + dict lookup |
  |---|---|---|
  | Graviton4 | 2.56 M lines/s | 0.60 M reads/s |
  | Xeon 6975P-C | 3.85 (1.50×) | 1.19 (1.98×) |
  | EPYC 9R45 | 5.02 (1.96×) | 1.60 (2.67×) |

  This cannot separate the build from the core, because no one image runs on both. It does
  say that a "Graviton is slow at Python" reading would be a claim about this build too.
- MLE is the only multi-process step: 86 / 91 / 55 s. Intel's 8 vCPU are 4 cores × 2 threads
  (`thread_siblings 0,4`); Graviton4's and AMD's are 8 cores.
- **n = 2 per chip.** An earlier full pass on the same bytes (`r2`) gave tool walls of
  1363 / 850 / 598 s, within 3%. Every output it produced matches the shipped run's byte
  for byte, apart from MLE's permutation columns.
- Peak RSS is under 2.1 GiB on every chip. The box is sized by tmpfs (half of RAM must hold
  the FASTQ), not by MAGeCK.

## What held at scale, on all three chips

| claim | why it must hold | observed (all three) |
|---|---|---|
| sha256 of 3 FASTQs, library, CEGv2, NEGv1 == pins; FASTQs == ENA's md5 | provenance | 6 of 6 |
| reads per sample == ENA `read_count` | published count | 60,120,685 / 99,121,522 / 93,622,486 |
| **count table == independent recount, every guide × sample** (`recount.py`: same offset order, first exact 20-mer, MAGeCK's library rules) | the counting rule, reimplemented | **77,441 × 3, 0 differ**, for both offset sets |
| summary `Mapped` == table column sum == recount's counted reads; zero-count and total guides == table | conservation | holds, 6 of 6 columns |
| Gini == independent recompute | definition | ≤ 2.6e-14 |
| median-of-ratios size factors == recompute | definition | 0.0 relative difference |
| normalised counts == raw × size factor | definition | 0.0 |
| RRA rerun on the same table | determinism | byte-identical |
| RRA scores exactly the genes with a read; MLE every gene in the table | MAGeCK drops all-zero guides before RRA | 20,111 / 20,112 |
| NEGv1 genes ≤ 5% of RRA's depleted genes at FDR < 0.05 | NEG genes are true nulls, so they are false discoveries, which FDR 0.05 bounds | **6 of 1,484** (0.4%) |
| MLE beta finite; median beta, CEGv2 < NEGv1 | essential genes drop out | −1.603 < +0.107 |

Observed, not asserted:
- **MAGeCK's default `--trim-5 AUTO` leaves 18.6% of the replicates' reads uncounted.** Broad's
  staggered primers put the guide at nine offsets, 22 to 30. AUTO stops once its picks cover 75%
  of the first 100,000 reads, so it took six (23–26, 28, 29) and dropped 22, 27 and 30. The
  recount finds a library guide at an uncounted offset in 18,431,481 repA reads, 17,482,008
  repB and 7,450,886 plasmid (12.4%). With `--trim-5 22,23,24,25,26,27,28,29,30`, counted reads rise
  from 62.3% to 80.3% (repA), and what is left uncounted is 0.62%, all at offsets ≤ 21. **Here
  it cost depth, not hits.** The offset is a property of the primer, not of the guide, so the loss
  is spread evenly: AUTO's table gives 1,477 depleted genes against 1,484.
- **Non-targeting guides as RRA's null (`--control-sgrna`) call 85% of genes depleted:**
  16,265 of 19,112, including 683 of the 897 non-essential genes. In a cutting screen every
  targeting guide pays a DNA-damage cost that non-targeting guides do not, so against that null
  nearly every gene looks depleted. With the default null (all guides) it is 1,484 genes and 6
  NEG. Do not use non-targeting guides as the null for a knockout dropout.
- Recall of CEGv2 at FDR < 0.05 is 513 of 661 (0.776). Essential vs non-essential AUC is 0.9932
  by RRA rank and 0.9944 by MLE beta.
- Replicate vs replicate (repB vs repA): 0 depleted and 2 enriched genes at FDR < 0.05.
- MLE scores `controlguide371`, whose only guide has zero reads in every sample, at
  β = −0.788. RRA drops it. Read MLE betas for genes with no reads as undefined.
- 23 of Hart's 684 CEGv2 genes and 29 of 926 NEGv1 genes are missing from the library under
  those names (older symbols). The checks use the intersection.

## Across chips: what is a fixed point and what is a path

Every output is **byte-identical across Graviton4, Intel and AMD**, and across both passes. That
covers both count tables and summaries, the RRA gene and guide summaries, the normalised table,
the control-null, AUTO and replicate comparisons, MLE's guide summary, and MLE's `beta`, `z`,
`wald-p-value` and `wald-fdr`. The images ship different numpy and scipy (2.4.6/1.17.1 vs
2.3.2/1.16.1) and OpenBLAS picks different kernels (`neoversev2` vs `Cooperlake` on both x86
chips). The counts are integers. The size factors come from sorting, and RRA's permutation reproduces
exactly on a rerun. MLE's beta is the converged fixed point of a small per-gene fit, printed at
MAGeCK's precision, so none of them moved.

The one exception is MLE's permutation `p-value` and `fdr`. `mlemultiprocessing.py` shuffles
with an unseeded `np.random`, and MAGeCK exposes no seed. Those columns differ between two runs
on the same chip as much as between chips: that is a random draw, not a path through the arithmetic.

## Pins

| | |
|---|---|
| reads | ENA, PRJNA508200: plasmid `SRR8297998`, replicate A `SRR8279891`, replicate B `SRR8281054` (single-end 50 nt). Each was checked against ENA's `fastq_md5` on download and pinned by sha256 in `inputs.sha256`. `fetch.sh` fetches them on AWS: 7.7 GB at about 3 MB/s per stream, 43 min. |
| library | Brunello, `brunello.tsv` from Zenodo [10.5281/zenodo.5750854](https://doi.org/10.5281/zenodo.5750854): 77,441 guides, 1,000 non-targeting (`controlguide*`), no duplicate sequences |
| reference sets | Hart lab `CEGv2.txt` / `NEGv1.txt`, `hart-lab/bagel` at `53388adb` |
| scripts | `run.sh`, `recount.py`, `check.py`, `interp.py`: sha256 checked in-task (`mkspec.py` writes the pins into each spec) |
| image | the recipe's `quay.io/aarchbio/mageck@sha256:92a0f37e…` (arm64) and `quay.io/biocontainers/mageck@sha256:ef466ab3…` (x86, both Intel and AMD) |

`results/<chip>/` holds each chip's `check.txt`, step timings, host facts, footprint,
output hashes and `interp.txt`.
