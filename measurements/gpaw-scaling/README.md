# GPAW on 64-atom Si: 1 → 48 ranks, one energy

> **Wall falls from 618.0 s on one rank to 22.3 s on 48 — 27.7×, 58% parallel efficiency — and
> the extrapolated energy is −380.305329 eV on five of seven rungs and −380.305360 on the other
> two, 3.1e-5 eV apart.** More ranks change the speed, not the answer.

GPAW 25.7.0 (aarchsci `dft` env), ASE 3.29.0, libxc 7.1.2. Bulk Si, 2×2×2 supercell of the
8-atom conventional cell (64 atoms), PW 400 eV, LDA, 4×4×4 Monkhorst-Pack, `OMP_NUM_THREADS=1`.
Graviton4, 2026-09-09, us-east-1. This is the data behind the GPAW shape on
[How many cores should I ask for?](../../patterns/sizing.md). The Pt-slab workload in
[gpaw-real](../gpaw-real/README.md) is a different system and a different question.

| ranks | wall | speedup | efficiency | energy (eV) | file |
|---|---|---|---|---|---|
| 1 | 618.0 s | 1.00× | 100% | −380.305329 | `sweep/n1` |
| 2 | 320.5 s | 1.93× | 96% | −380.305329 | `sweep/n2` |
| 4 | 177.5 s | 3.48× | 87% | −380.305329 | `sweep/n4` |
| 8 | 108.4 s | 5.70× | 71% | −380.305360 | `sweep/n8` |
| 16 | 53.8 s | 11.49× | 72% | −380.305329 | `knee/n16` |
| 32 | 32.1 s | 19.25× | 60% | −380.305360 | `knee/n32` |
| **48** | **22.3 s** | **27.71×** | **58%** | −380.305329 | `knee/n48` |

`results/legible-r5/` is the 8-rank recipe run of 2026-09-07 on this input, kept because it is
the only artifact here with the rank-count guard: `gpaw.mpi.world.size` reads back 4 and 8, and its
`gpaw-8.log` shows the parallelisation GPAW chose (4 over k-points × 2 domains, 158 bands,
666 MiB) and 109.1 s total.

## What these artifacts do not show

- **The 64- and 96-rank failure.** The [sizing page](../../patterns/sizing.md) and the session
  record both say n = 64 and n = 96 failed, with the cell unable to be divided further. No artifact
  from either run survives in the bucket, so that "wall" is a claim with no file behind it here.
  Re-running two rungs on a `c8g.16xlarge` and a `c8g.24xlarge` is a few minutes each.
- **The rank count of the sweep rungs.** `timing.txt` records `RANKS` (the count launched) and,
  for 16–48, `OK True`. The script that wrote them was not kept, so it is not known whether
  `RANKS` was read back from `gpaw.mpi.world.size` or only echoed.
- **Which box each rung ran on.** Not recorded in `timing.txt`. The session record says `c8g`
  throughout. No $ is derived here for that reason.
- **The specs and the input script.** Written to `/tmp` in the session and not kept. The system is
  fully described by `legible-r5/gpaw-8.log`.

Image: the [gpaw recipe](../../recipes/gpaw/README.md)'s pin on the run date was
`quay.io/aarchsci/dft@sha256:0740fab9721d…`. The logs record the GPAW version, not the digest.
Artifacts copied verbatim from `s3://scicookbook-942542972736-us-east-1/runs/{gpaw-sweep,
gpaw-knee,gpaw-legible/r5}/`.
