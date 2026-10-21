# GROMACS decomposition leg — apparatus

> **The results live on the [gromacs recipe page](../../recipes/gromacs/README.md).** This
> directory is the apparatus that produced them, not a second account of them.

`decomp.task.json` is the decomposition leg — rank/thread splits at a fixed total core count,
which is a different question from the generation sweep whose rungs the page tabulates. Raw
per-rung output in `results/`.

**Why this is a pointer and not a write-up:** the page carries the generation table and the ns/day
figures already. This spec is the only record of how the decomposition rungs were run.

## The decomposition leg on Intel and AMD

Same `decomp.sh`, one 16-vCPU box per chip, the `md` env's linux-64 twin (`envs-x86_64/md-f7ed8789d24e`,
GROMACS 2026.3, MKL where arm64 has OpenBLAS). The Graviton4 leg ran on the previous `md` digest
(`1ee941664add…`, also GROMACS 2026.3). The twin is of the current one.

| ranks × threads | Graviton4 `c8g.4xlarge` | Intel `c8i.4xlarge` (8 cores × 2) | AMD `c8a.4xlarge` (16 cores) |
|---|---|---|---|
| 16 × 1 | **25.538** | 28.981 | **58.432** |
| 8 × 2 | 24.860 | 28.440 | 52.582 |
| 4 × 4 | 24.168 | 27.044 | 46.435 |
| 2 × 8 | 23.292 | 26.272 | 41.323 |
| 1 × 16 | 25.432 | **29.093** | 47.461 |
| best ÷ worst | 1.096 | 1.107 | **1.414** |
| $/ns at the best split | 0.5997 | 0.6185 | **0.3541** |

ns/day. $/ns is from the hourly price spawn printed ($0.6381, $0.7497, $0.8622).

- **Checks hold on both**: all five splits run, and `ranks_used` equals the ranks launched in every
  row. The mean temperature prints 301.998 K in all fifteen rows on three chips. MD is a path, so
  that is recorded, not asserted ([why](../../recipes/gromacs/README.md)).
- **"Decomposition barely matters" is a Graviton4 and Intel result, not a GROMACS one.** On AMD the
  splits span **41%**, and pure MPI (16 × 1) is 23% faster than pure OpenMP (1 × 16). On Graviton4
  and Intel those two are within 0.4%. Tune the split on AMD. Leaving it alone is safe on the other two.
- **AMD at 16 × 1 is 2.29× Graviton4 and 2.0× Intel per day**, and the cheapest nanosecond of the
  three by 41%. Intel's 16 vCPUs are 8 hyperthreaded cores and the AVX2 build ([page](../../recipes/gromacs/README.md)).
  Intel and AMD run the same build, so their gap is the chips. The ARM/x86 gap includes the build too.

Spend for the two legs: $0.138 (windows 382 s and 242 s). Not re-run on x86: the generation ladder
(Graviton by definition). `decomp.task.json` stages its script as `decomp.sh` (sha256 in
`pins.sha256`, with a diagnostic-only `host.sh`): spawn 0.139.1's own wrapper leaves under 1 KB of
EC2's 16 KiB user data (spawn#1246). `decomp.sh` is the inline script byte for byte. The x86 legs are
`python3 scripts/spec_arch.py gromacs {x86_64,amd} <bucket> <nonce> measurements/gromacs-real/decomp.task.json`.
Raw: `results/decomp-{x86_64,amd}.txt`, `results/host-{x86_64,amd}.txt`.

See also [nwchem-real](../nwchem-real/README.md) and [gpaw-real](../gpaw-real/README.md), which
ask the same question of two other codes and get opposite answers — packing is nearly free for a
small molecular SCF and actively costly for a bandwidth-bound plane-wave slab. Decomposition
results do not transfer between codes, which is the reason each one needs its own leg.
