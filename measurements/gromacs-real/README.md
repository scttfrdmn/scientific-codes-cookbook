# GROMACS decomposition leg — apparatus

> **The results live on the [gromacs recipe page](../../recipes/gromacs/README.md).** This
> directory is the apparatus that produced them, not a second account of them.

`decomp.task.json` is the decomposition leg — rank/thread splits at a fixed total core count,
which is a different question from the generation sweep whose rungs the page tabulates. Raw
per-rung output in `results/`.

**Why this is a pointer and not a write-up:** the page carries the generation table and the ns/day
figures already. This spec is the only record of how the decomposition rungs were run.

See also [nwchem-real](../nwchem-real/README.md) and [gpaw-real](../gpaw-real/README.md), which
ask the same question of two other codes and get opposite answers — packing is nearly free for a
small molecular SCF and actively costly for a bandwidth-bound plane-wave slab. Decomposition
results do not transfer between codes, which is the reason each one needs its own leg.
