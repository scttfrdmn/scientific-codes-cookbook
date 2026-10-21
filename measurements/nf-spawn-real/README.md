# nf-spawn on a real gene family: the recipe's DAG over 919 Pfam sequences, three chips

> **nf-spawn ran the recipe's five processes on their own instances and changed nothing.** Each
> engine process ran the same script as the direct run, and its alignments match the direct run's
> byte for byte. Five of the six trees match too. The sixth, Intel's MUSCLE tree, ended on a different
> optimum, and a second run without the engine landed on a third (−260526.2050): the search wanders, not
> the engine. An unchanged `-resume` re-runs nothing in 9 s.
> Renaming a process's image re-runs nothing either: nf-spawn leaves the image out of the cache key.
> AMD finished in 2.2 h for $0.98, Graviton4 in 5.0 h for $1.35.

Workload: the recipe's own `main.nf`, **unchanged**, over the **whole** Pfam 38.2 seed of `PF01255.25`
(Prenyltransf), 919 sequences of 203–349 residues with the gaps removed. That is the family
[mafft-real](../mafft-real/README.md) aligns and [iqtree-real](../iqtree-real/README.md) builds its
tree from, so this run uses their staged bytes (`pf01255.25_unaligned.fa`, sha256 `bf393f17…`, read in
place, not copied). The DAG is the recipe's: MAFFT and MUSCLE in parallel, an ML tree from each, and a
Robinson-Foulds comparison of the two trees. Hundreds to a few thousand sequences is the size of a
gene-family study, and at this size the job takes hours of compute on five instances per run rather than
the fixture's ten minutes. Data tier: a versioned immutable release (Pfam 38.2, md5-checked against EBI
by mafft-real's staging).

## Run it

```sh
export AWS_PROFILE=aws B=<your bucket> JAVA_HOME=<a JDK 17+>
bash measurements/nf-spawn-real/stage-inputs.sh "$B"          # main.nf's five scripts, rendered, for the direct run
for a in arm64 x86_64 amd; do
  for r in 1 2 3 4; do bash measurements/nf-spawn-real/run.sh $a "$B" r1 $r; done   # the engine, four times
  bash measurements/nf-spawn-real/direct-chain.sh $a "$B" r1                          # the same five steps, no engine
done
for s in 15-eval 16-tree-muscle-repeat; do                    # Intel's TREE (muscle) pair: fixed points, a repeat
  spawn task run --wait --spec "$(python3 scripts/spec_arch.py nf-spawn x86_64 "$B" r1 measurements/nf-spawn-real/$s.task.json)"
done
bash measurements/nf-spawn-real/collect.sh "$B" r1             # results/<chip>/, results/compare.txt
```

`run.sh` is the recipe's invocation with this workload's parameters and [`real.config`](real.config)
in place of the recipe's config:

```sh
nextflow run recipes/nf-spawn/main.nf -c real.config -profile arm64 \
  --seqs s3://$B/inputs/mafft-real/pf01255.25_unaligned.fa --expect_seqs 919 --expect_residues 207712
```

The four runs come from one launch directory per chip. Run 1 is cold. Run 2 is `-resume` with nothing
changed. Run 3 resumes with OBSERVE_RF's image named `repo:tag@digest` instead of `repo@digest`, the
same bytes. Run 4 resumes with OBSERVE_RF's `cache` off. `real.config` differs from the recipe's config
in sizing and bookkeeping only. It uses nf-spawn 0.11.0, the first release with `ext.costLimit`, so
every process carries a cost cap as well as a TTL. It gives MUSCLE a `4xlarge`. It sets a work dir and
publish prefix per chip and run, and publishes every process's outputs.

The direct run ([`direct.sh`](direct.sh), [`10`–`14-*.task.json`](10-mafft.task.json)) runs each process
as a plain spawn task on the same image, instance size and bytes. Its script is the process's own:
[`render.py`](render.py) fills in `main.nf`'s `!{}` placeholders, and `compare.py` checks the result
against the `.command.sh` the engine actually ran. It is run the way nf-spawn runs it
(`bash .command.sh 1>.command.out 2>.command.err`), and it records tool wall, cgroup `memory.peak` and
cores busy, which `main.nf` cannot do unchanged.

## Results

One run per chip, the same staged bytes, nf-spawn 0.11.0 and Nextflow 26.10.2 driving from a laptop.
MAFFT 7.525, MUSCLE 5.3 and IQ-TREE 3.1.3 are aarch.bio's arm64 builds on Graviton4 and bioconda's x86
builds on Intel and AMD (the recipe's pins). Each process runs on its own instance, sized per process:
MAFFT and OBSERVE_RF on a `large`, MUSCLE on a `4xlarge`, each TREE on an `xlarge` (`-T 4`).

| chip | instances | workflow wall | slowest process | billed (5 windows) | peak RSS | $/hr | compute $/result | billed $/result |
|---|---|---|---|---|---|---|---|---|
| Graviton4 | `c8g` | 17,999 s | 17,809 s | 27,630 s | 5.19 GiB | 0.0798–0.6381 | 1.325 | 1.346 |
| Xeon 6975P-C | `c8i` | 13,303 s | 13,146 s | 23,824 s | 5.19 GiB | 0.0937–0.7497 | 1.400 | 1.408 |
| **EPYC 9R45** | `c8a` | **7,964 s** | **5,887 s** | **13,870 s** | 5.19 GiB | 0.1078–0.8622 | **0.962** | **0.985** |

A *result* is one run of the DAG: two alignments, two ML trees and their RF distance. *Workflow wall*
is Nextflow's run 1, from the driver. *Slowest process* is TREE (mafft)'s tool wall in the direct run.
It is the same search on every chip (697 iterations, the same tree), so it is the one like-for-like
compute number here. *Billed* is the sum of spawn's five windows for the engine's processes, and
*billed $/result* is the sum of their `cost_estimate`. *$/hr* runs from the `large` to the `4xlarge`,
as spawn priced them. *Compute $/result* is each process's direct tool wall at its instance's rate.
*Peak RSS* is MUSCLE's cgroup `memory.peak`, the largest of the five. Threads per core: 1 on Graviton4
and AMD, 2 on Intel. None of the three tools links OpenBLAS.

- **The trees are the bill.** They make up 93–96% of every chip's billed time. On the like-for-like
  tree, AMD is 3.0× Graviton4 and Intel 1.35×. The arm64 IQ-TREE build runs its 128-bit SSE2 kernel and
  both x86 runs use AVX+FMA, so this compares builds as well as chips (as
  [iqtree-real](../iqtree-real/README.md) found). Intel's `xlarge` is 2 cores with SMT, AMD's is 4 cores.
- **AMD is the fastest and cheapest per result.** Its hour costs 35% more than Graviton4's and a result
  27% less. Graviton4 has the cheapest hour and is second on price per result.
- **nf-spawn adds almost nothing per process.** Each engine process's spawn window exceeds the direct
  tool wall by 35–133 s on the short processes (Docker install, image pull, S3 sync of the work dir),
  where a direct spawn task pays 34–50 s for its own staging. Run 1's wall exceeds the sum of its
  critical path's billed windows by 84 / 71 / 67 s: three boots, which spawn's windows leave out, plus
  Nextflow's polling. The critical path is the MAFFT branch on Graviton4 and Intel, the MUSCLE branch on AMD.
- **MUSCLE cannot run on the recipe's `large`.** `muscle -align` on 919 sequences peaks at 5.19 GiB, and
  a `large` has 4 GiB. Its memory grows as about n²: the sizing probe measured 0.17 / 0.52 / 1.42 GiB at
  115 / 230 / 460 sequences ([`00-probe`](00-probe.task.json)).
- **Run 2 costs nothing.** The resume took 8–9 s on the laptop and launched no instance. Run 4 re-ran
  one `large` for 73–74 s.
- n = 1 per cell. The alignments and TREE (mafft) are deterministic on each chip, so a repeat would
  re-measure only timing. TREE (muscle) is not (below).

## The identities, at scale, on all three chips

| observable | assertion | observed (every chip) |
|---|---|---|
| run 1 | 5 processes `COMPLETED` exit 0 (trace) **and** every work dir's `.exitcode` object is `0` (bucket) | 5 / 5 |
| input | sha256 == the pin | `bf393f17…` |
| **every process's script** | engine `.command.sh` == the direct run's rendered script | 5 / 5 |
| MAFFT, MUSCLE | the stage's own identity line, engine == direct | `seqs=919 residues=207712` |
| **alignments** | every row == its input row with gaps removed (names, residues); one width | 919 rows; 693 / 1,079 columns |
| **alignments, engine vs direct** | byte-identical | 2 / 2 |
| MAFFT vs [mafft-real](../mafft-real/README.md) | == its `--auto` output on the same bytes, which chose FFT-NS-2 | `db0911ee…` |
| every tree, engine and direct | the input's 919 names, unrooted binary: n − 3 = 916 splits | 12 / 12 |
| **OBSERVE_RF** | `iqtree3 -rf` == an independent count of the splits in one tree and not the other, on each run's own trees | 6 / 6 |
| **run 2**, `-resume` unchanged | 5 of 5 `CACHED`, no `Submitting task` in the log; published RF bytes == run 1's | yes |
| run 3, OBSERVE_RF's image renamed | 5 of 5 `CACHED` (the image is not in the key, below) | yes |
| **run 4**, OBSERVE_RF's cache off | exactly OBSERVE_RF re-runs, in a new work dir, on the renamed image (Docker's pull line); 4 `CACHED`; its output == run 1's bytes, made from trees that run 1's instances wrote | yes |
| Intel's two TREE (muscle) trees | each logL a fixed point: re-optimised on its own topology twice, moves < 0.01 then < 0.001 ([`15-eval`](15-eval.task.json)) | −260442.2447 → .2450 → .2450; −260543.0218 → .0217 → .0217 |

- **The direct run checks the engine, not just the tools.** Its scripts are rendered from `main.nf`
  independently of Nextflow, and they match what the engine ran, so the engine substituted the
  parameters, staged the inputs under the names the script expects, and ran the result. Run 4's join
  read trees that the previous session's instances left in the S3 work dir, which is the cross-session
  handoff a resume depends on.
- **Engine vs direct on the trees is reported, not asserted** ([the stochastic-search
  rule](../../practices/cross-checks.md)). TREE (mafft) gave the same newick and logL engine and direct
  on all three chips: bytes identical on AMD, one branch length different in the 10th decimal on
  Graviton4 and Intel. TREE (muscle) did the same on Graviton4 and AMD. On Intel the two runs took the
  same alignment, image, seed and `-T 4` and ended 100.78 log-likelihood units apart, on trees RF 674
  of 1,832 apart. Both are genuine optima: each is a fixed point (last row). A second direct run
  ([`16`](16-tree-muscle-repeat.task.json)) reached a third, −260526.2050. So `-T 4 --seed` does not make this search reproducible at 919 taxa, and the cause is the
  search, not the engine. OBSERVE_RF's distance follows its trees (694 engine, 792 direct).

## Across chips

- **MAFFT: one alignment.** Byte-identical on all three chips (`db0911ee…`), and identical to mafft-real's.
- **TREE (mafft): a fixed point.** The same topology (RF 0), the same 697 iterations and the same logL,
  −253070.2935, on all three chips. Intel and AMD produce the same bytes. Graviton4's differ from both
  in one of 1,835 branch lengths, in the 10th decimal.
- **MUSCLE: Graviton4 and Intel agree, AMD differs by one row.** Graviton4 and Intel produce the same
  bytes (`76a75678…`), with different builds. AMD runs the same x86 build as Intel and places one
  sequence differently. 918 of 919 rows are identical, 99.1% of residues sit in an identical column,
  and the width is the same (1,079). Each chip reproduced its own alignment engine vs direct, so this is
  a difference between chips, not between runs. It is not explained.
- **TREE (muscle): a path.** Intel and Graviton4 searched the same alignment, and four runs reached
  four different optima: Graviton4 −260580.5902 (243 iterations), Intel −260442.2447 (359),
  −260543.0218 and −260526.2050. AMD's alignment differs, and its tree reached −260364.8527 (621). The distances between
  them (RF 466–568) are no larger than the distance between Intel's two runs (674). So the RF
  observation is a draw too: 712 / 694 / 766, against 26 for the recipe's 114-sequence fixture.

## What the engine does that a reader should know

- **A changed image does not invalidate the cache.** Nextflow adds a task's container to its cache key
  only when a container engine is enabled (`TaskHasher`: `if( task.isContainerEnabled() )`). nf-spawn runs
  Docker itself and enables none, so run 3 resumed 5 of 5 with OBSERVE_RF's image renamed. The same holds
  for a real repin. Change a process's digest and `-resume` hands back outputs from the old image, on any
  chip. Use a new work dir after any image change (the recipe's `x86_64` profile already does), or turn
  that process's `cache` off.
- **The cost cap needs nf-spawn 0.11.0.** 0.10.1, the recipe's pin, writes only `lifecycle.ttl` into
  each task. 0.11.0's `ext.costLimit` adds `lifecycle.cost_limit`.

## Caveats

- One family, one run per chip. The DAG is the recipe's, and its tree step is a single LG+G4 search
  without ModelFinder or bootstraps, so it costs less than [iqtree-real](../iqtree-real/README.md)'s
  workload.
- The driver ran on a laptop for up to 5 h per chip and is not billed. A driver on an instance would add
  that instance's hour.
- Spend for this write-up: **$8.63**. That covers the two sizing probes (MUSCLE $0.07, one TREE stopped
  part-way, $0.52), the engine and direct runs on three chips, and Intel's repeat and evaluation.

## Files

`stage-inputs.sh`, `render.py` and `pins.sha256` (the direct run's scripts, pinned); `real.config` and
`run.sh` (the engine); `direct.sh`, `direct-chain.sh` and `10`–`14-*.task.json` (the direct run);
`15-eval` and `16-tree-muscle-repeat` (Intel's TREE (muscle) checks); `00-probe*` and `probe.sh`
(sizing); `collect.sh` and `compare.py`. Per-chip evidence is in `results/<chip>/`: `check.txt` and
`timing.tsv`; the driver's traces, logs and wall times for runs 1–4; each engine process's streams, exit
code, IQ-TREE log and spawn record; and each direct step's host, metrics, streams and hashes.
`results/compare.txt` is the cross-chip comparison.
