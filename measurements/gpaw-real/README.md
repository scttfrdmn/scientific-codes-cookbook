# GPAW: the same rank sweep is 82% efficient across boxes and 68% on one

> **Four ranks run 17.9% faster on a 16-vCPU box than on a 4-vCPU box — same ranks, same work,
> same answer.** The [recipe page](../../recipes/gpaw/README.md) already documents that effect and
> uses it to argue for measuring each rung on the box you would rent. This completes the other
> half: run all three rank counts on *one* box and the 4→16 speedup is **2.71× (67.8%)**, not the
> 3.27× (82%) the cross-box rungs give. Two honest numbers, answering different questions.

GPAW 25.7.0 (aarchsci `dft` env), Pt(111) 3×3×4 slab, PW 400 eV, 4×4×1 k-points, PBE, 36 atoms.
**Every run below converges in 27 SCF iterations to −219.541414 eV with a Fermi level of 2.050224
eV** — nine runs, four chips, three rank counts, three instance sizes, one answer. That invariance
is what makes the timings comparable at all.

## The four generations, at 16 ranks

| generation | instance | SCF wall |
|---|---|---|
| Graviton2 | `c6g.4xlarge` | 779.3 s |
| Graviton3 | `c7g.4xlarge` | 479.8 s |
| Graviton4 | `c8g.4xlarge` | 442.5 s |
| **Graviton5** | `c9g.4xlarge` | **334.6 s** |

2.33× over four generations, and the Graviton3→4 step is the weak one here — 1.08× for a higher
hourly rate, the same rung that [hmmer](../hmmer-real/README.md) found actively unprofitable.

## The correction: two ways to measure a rank sweep, 14 points apart

Both legs vary ranks from 4 to 16 on Graviton4. They differ only in what box the ranks sit on.

| ranks | same 16-vCPU box | box sized to the ranks | under-filling gains |
|---|---|---|---|
| 4 | **1227.0 s** | 1446.2 s (`c8g.xlarge`) | **+17.9%** |
| 8 | **726.2 s** | 789.4 s (`c8g.2xlarge`) | **+8.7%** |
| 16 | 452.2 s | 442.5 s (`c8g.4xlarge`) | −2.1% |

| | 4 → 16 speedup | implied efficiency |
|---|---|---|
| right-sized boxes | 3.27× | **81.7%** |
| **same box** | **2.71×** | **67.8%** |

**The 16-rank row is the control.** There both legs *are* the same configuration — `c8g.4xlarge`
at 16 ranks — and they land 2.2% apart. That is run-to-run variation on this workload, and it is
what licenses comparing the other two rows: the 17.9% and 8.7% gaps are an order of magnitude
larger than the noise floor the experiment measures on itself.

### Which number answers which question

The [gpaw recipe](../../recipes/gpaw/README.md) reports 3.27× and calls it 82% parallel
efficiency, and **the ratio is right for the question the page is asking**: choosing between a
`c8g.xlarge` at 4 ranks and a `c8g.4xlarge` at 16, you really do get 3.27× for 22% more money.
The page also already documents the box effect below its fold — it has the 1227.0 vs 1446.2
comparison and uses it to argue, correctly, that projecting a small-box cost from a big-box run
understates the cheapest option by 18%.

What the page does not do is carry that effect through to the word it uses for 3.27×. "Parallel
efficiency" credits parallel scaling with something it did not do: roughly 14 of those 82 points
are the extra memory bandwidth four ranks enjoy when twelve cores sit idle beside them. The two
numbers answer different questions and both belong on the record —

- **81.7%** — what more ranks *and* a bigger box buy together. The purchasing number.
- **67.8%** — what more ranks buy on a node you already have. The scaling number.

A reader sizing a cluster, or deciding how many ranks to give a job on a fixed node, needs the
second one, and until now only the first was written down.

The general trap: **a scaling study that resizes the instance at each rung is not measuring
scaling.** It measures scaling plus whatever else changed. Hold the box fixed, or report both.

## Why under-filling helps here, and did not for NWChem

The same experiment on [NWChem](../nwchem-real/README.md) found the opposite: packing sixteen
one-rank jobs onto one box cost **0.0–6.5%** against running each alone on a right-sized box — in
other words, filling the box was nearly free there.

Both results are consistent, and the difference is the working set. NWChem was converging a
217-basis-function molecular SCF; GPAW is doing plane-wave DFT on a periodic slab, with real
memory traffic per rank. So:

- a small working set → contention is negligible, and **packing wins** on throughput
- a bandwidth-hungry working set → idle cores are not wasted, they are **headroom**, and
  under-filling buys 8–18%

Neither is a rule about Graviton. Both are statements about a working set, which is why each has
to be measured rather than assumed — and why "cores busy" is the wrong thing to optimise in
either direction.

## The invariance that makes all of this legible

Every one of the nine runs returns **−219.541414 eV in 27 iterations**, with a measured energy
spread across the rank sweep of **0.000000 eV** against a 1e-4 tolerance. A plane-wave DFT
decomposed over 4, 8 and 16 ranks partitions plane waves and k-points differently, so this is a
real decomposition invariance, not a formality — and it is the reason a wall-time table means
anything. Without it, a faster rung could simply be a rung that did less work.

Each rung also asserts `gpaw.mpi.world.size` equals the rank count launched, because conda-forge
ships `nompi` builds at higher build numbers than the MPI ones: an unpinned solve under
`mpiexec -n 16` runs sixteen independent rank-0 calculations that all print the same energy, and
a naive "parallel agrees with serial" check passes vacuously
([rank-count guard](../../practices/mpi-rank-count.md)).

## Specs and raw output

`knee.task.json`; raw per-rung output in `results/` — `c{6,7,8,9}g-smoke-check.txt` (generations),
`rightsized-r{4,8}.txt` (box sized to ranks), `knee-samebox.txt` (all three rank counts on one
box). Cost per rung is on the recipe page, which prices each configuration on the box it actually
needs.
