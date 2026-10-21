# CalculiX on a real workload — NAFEMS LE10 to 933k equations, three chips

> **The NAFEMS LE10 thick plate, refined to 933,248 equations, gives σyy(D) = −5.372 MPa against
> the published −5.38 on Graviton4, Intel and AMD, with load equilibrium closing to the printed
> digit on every rung.** The direct solve is a fixed point: all three chips print the same answer.
> AMD is fastest and cheapest per result, Graviton4 next, Intel last.

Workload: NAFEMS LE10, the standard
3D linear-elastic benchmark: a quarter elliptic plate 0.6 thick with an elliptic hole, 1 MPa on the
upper face, target σyy = −5.38 MPa at point D on the hole edge. Meshed here in 20-node hexahedra
(C3D20R) on a 5-rung ladder, base 6×3×1 elements multiplied by 1, 2, 4, 8 and 16. The finest rung
is **73,728 elements, 315,969 nodes, 933,248 equations**. Each rung is solved with SPOOLES (a
direct solver) and with CalculiX's ITERATIVE CHOLESKY. Rung 8 is also solved at
1 thread.

**Why this size.** A million-equation static solve on one box is the size a CalculiX user's
component model reaches, and at 16 threads it is what SPOOLES fits: the finest solve peaks at
**39.1 GiB**. The next rung (×2 in each direction, ~7.4 M equations) would not fit on this box:
SPOOLES grew 13× from rung 8 to rung 16 (3.0 → 39 GiB) for 7.6× the equations. Data tier:
synthetic against a published reference. The benchmark is defined by geometry, loads and a
target, so there is nothing to download. The suite's own cases are tiny (the recipe runs 497 in
137 s), so this ladder adds the solve size, not more cases.

## Run it

```sh
export AWS_PROFILE=aws B=<your bucket>
bash measurements/calculix-real/stage-inputs.sh "$B"     # le10.py + real.sh, sha256-pinned
for a in arm64 x86_64 amd; do
  spawn task run --spec "$(python3 scripts/spec_arch.py calculix $a "$B" r1 measurements/calculix-real/real.task.json)" --wait
done
bash measurements/calculix-real/collect.sh "$B" r1       # results/<chip>/ and the cross-chip comparison
```

`le10.py` writes each rung's `.inp` and runs `ccx` on it. The analysis step is plain CalculiX:

```text
*STATIC, SOLVER=SPOOLES          (or SOLVER=ITERATIVE CHOLESKY)
*DLOAD
<each upper-face element>, P2, 1.0
*NODE PRINT, NSET=NOUTMID, TOTALS=ONLY
RF
*NODE FILE
S
```

with `OMP_NUM_THREADS = CCX_NPROC_EQUATION_SOLVER = CCX_NPROC_STIFFNESS = 16`.

## Results

16 threads on a `4xlarge` (16 vCPU, 64 GiB) of each chip. CalculiX 2.23 from aarch.science's
`fem-cfd` image on Graviton4, from its linux-64 lock twin on both x86 chips.

| chip | instance | tool wall | finest solve (SPOOLES / iterative) | task window | peak mem | $/hr | compute $/result | billed $/result |
|---|---|---|---|---|---|---|---|---|
| Graviton4 | `m8g.4xlarge` | 697 s | 395 s / 217 s | 788 s | 39.4 GiB | 0.7181 | 0.139 | 0.157 |
| Xeon 6975P-C | `m8i.4xlarge` | 710 s | 442 s / 191 s | 767 s | 42.1 GiB | 0.8467 | 0.167 | 0.180 |
| **EPYC 9R45** | `m8a.4xlarge` | **458 s** | **255 s / 149 s** | 502 s | 42.1 GiB | 0.9738 | **0.124** | **0.136** |

A *result* is the whole ladder: 11 solves. *Compute* prices `le10.py`'s wall; *billed* is spawn's
`cost_estimate` over the task window.

- **AMD is 1.52× faster than Graviton4 and 11–14% cheaper per result**, despite a 36% dearer hour.
  Intel matches Graviton4's wall but costs 15–20% more. Intel's 16 vCPUs are 8 cores × 2 threads
  (`lscpu`, `results/*/host.txt`); Graviton4's and AMD's are 16 full cores.
- **SPOOLES is the memory.** The finest direct solve takes 39.1 GiB on every chip (ccx
  `ru_maxrss`). The iterative solve of the same system takes **2.1 GiB**, and it is faster
  (217 vs 395 s on Graviton4), but its answer is less exact (below). A 32 GiB box cannot hold
  the direct solve. The x86 cgroup peak is ~3 GiB higher because the installed env's page cache
  counts.
- **Threads pay at the top only.** Rung 8 (123k equations) takes 41 s at 1 thread and 11 s at 16
  on Graviton4: 3.7×. The finest rung was not run serially.
- **Overhead is 44–91 s** of each window. These timings are not compute cost
  ([layout](../../patterns/layout-and-effective-cost.md)).
- n = 1 per chip, plus one Graviton4 probe on `r8g.4xlarge` with the same ladder (finest SPOOLES
  391 s vs 395 s). σyy(D), u(D) and the reaction totals were identical in both runs; the 16-thread
  `.dat` files differ run to run only in round-off zeros, exactly as they do across chips.

## What held at scale, on all three chips

| claim | why it must hold | all three chips |
|---|---|---|
| total z reaction == 1 MPa × meshed upper area | the only z constraint carries no load, so it must react all of it | every rung, worst 8.4e-8 relative (bound 1e-6: the `.dat` prints 7 digits) |
| x and y reactions cancel | no load has an in-plane component | every rung, ≤ 9e-15 of Σ\|RF\| |
| σyy(D) at the finest rung within 1% of −5.38 | the published target, given to 3 figures | **−5.37221**, 0.145% off |
| each refinement changes σyy(D) less | convergence | changes 0.218, 0.054, 0.048, 0.023 |
| 1 thread == 16 threads at rung 8 | a direct static solve is a fixed point | all 11,346 printed numbers within 6e-14 of their block's largest |
| `ccx -v` == 2.23 | the version the recipe's suite references come from | 2.23 |

The meshed upper area is computed in `le10.py` by 3×3 Gauss on the same 8-node faces. It
converges to the exact ellipse annulus (5.448699758570) at h⁴, and the finest rung is 8e-10 from
it.

| rung | elements | equations | σyy(D), MPa |
|---|---|---|---|
| 1 | 18 | 419 | −5.27941 |
| 2 | 144 | 2,500 | −5.49695 |
| 4 | 1,152 | 16,880 | −5.44325 |
| 8 | 9,216 | 122,752 | −5.39544 |
| 16 | 73,728 | 933,248 | −5.37221 |

**What this does not settle: the third figure.** The last refinement still moved σyy(D) by 0.43%,
three times its distance from −5.38. The last three rungs give an observed order of 1.04 and a
Richardson estimate of −5.350, but the changes (0.054, 0.048, 0.023) are not yet in a clean
asymptotic range, so the estimate is reported, not asserted. The ladder agrees with −5.38 to 1%;
it does not confirm or refute the published value beyond that.

## Across chips: fixed points and paths

**SPOOLES is a fixed point on all three chips.** σyy(D), u(D) and every reaction total are
identical in every printed digit, on every rung. Of the 70,398 numbers in the six SPOOLES `.dat`
files per chip, the ones that differ are round-off zeros: reactions in unconstrained directions, ~1e-16,
worst 8.0e-14 of their block's largest value. In the finest stress field (315,969 nodes × 6
components, 6 significant figures), 2,712 nodes (Intel) and 2,717 (AMD) differ from Graviton4, by
at most 1e-8 MPa on a field whose largest component is 42.5 MPa.

**ITERATIVE CHOLESKY is a path.** Its stopping rule is built in, and the equilibrium check
measures where it stops: the iterative solves leave **5.9e-6 to 1.7e-4 of the applied load
unbalanced**, against 4–8e-8 for SPOOLES. What remains of the error then depends on the chip:

| rung | SPOOLES σyy(D) | iterative, Graviton4 | iterative, Intel and AMD | iterative minus SPOOLES, as a share of the rung's discretisation change |
|---|---|---|---|---|
| 8 | −5.39544 | −5.40073 | −5.38338 | 11% (Graviton4), 25% (x86) |
| 16 | −5.37221 | −5.38196 | −5.37462 | 42% (Graviton4), 10% (x86) |

- Intel and AMD give byte-identical iterative `.dat` files on every rung. Graviton4 differs from
  both, visibly in σyy(D) from rung 8 up. The split is by ISA. Both x86 chips run OpenBLAS's `Cooperlake` kernel and Graviton4
  `neoversev2`, so this run cannot separate the instruction set from the BLAS kernel. Not
  attributed further.
- At the finest rung the iterative answer is off from the direct one by up to 42% of what the
  refinement itself changed. **For a mesh-convergence study, use the direct solver**; the
  iterative one adds an error comparable to the one being measured, and that error moves with
  the chip.
- OpenBLAS `openblas_get_corename()`: `neoversev2` on Graviton4, `Cooperlake` on both x86 chips
  (0.3.34 has no Zen 5 target).

## Pins (data tier: synthetic, against a published reference)

| | |
|---|---|
| reference | NAFEMS LE10, σyy(D) = −5.38 MPa (NAFEMS, *The Standard NAFEMS Benchmarks*, rev. 3, 1990) |
| data | none: geometry, loads and constraints are the benchmark's, meshed in `le10.py` |
| image | arm64 `quay.io/aarchsci/fem-cfd@sha256:dd863838…`; x86 the `fem-cfd-1d4e4b5ee82a` lock twin (the recipe's frontmatter) |
| task | `le10.py` and `real.sh`, pinned in `pins.sha256` and re-checked inside the task before anything runs |

## Caveats

- The mesh is not NAFEMS's. The benchmark specifies a coarse reference mesh; this ladder uses
  equal parametric angle and radial spacing, uniform through the thickness, with every boundary
  node on its ellipse. σyy(D) is the frd nodal value: CalculiX's extrapolation from the
  integration points, averaged over the elements sharing D.
- Prices are on-demand us-west-2: spawn's `cost_estimate` over the task window. `$/hr` is that
  estimate over the window.
- Peak memory is the container's cgroup `memory.peak`; the per-solve figures are `ccx`'s own
  `ru_maxrss`, from `wait4`.
- The direct solver is SPOOLES, named in the input; `ccx` links no PaStiX library here (`ldd`,
  `results/*/host.txt`). The memory ceiling above belongs to SPOOLES, not to CalculiX.
