---
tool: su2
tool_version: 8.5.0
env: cfd-fv
image: quay.io/aarchsci/cfd-fv@sha256:830eaf94cc9a6b36d6307b08a702610f16ac9212235a984047fb8e329092ae46
spawn_version: 0.104.0
---
# SU2 — finite-volume Euler flow, verified by free-stream preservation

SU2 solves a finite-volume Euler flow on Graviton4 and proves the scheme right where it's hardest to fake — **free-stream preservation**: uniform flow held to machine zero on a deliberately skewed mesh. The catalog's second CFD recipe (finite-volume compressible, distinct from [OpenFOAM](../openfoam/README.md)'s incompressible), for anyone doing CFD.

> **What this covers.** SU2 8.5.0 (`SU2_CFD`) solving subsonic inviscid (Euler) flow, serial and over 2 MPI ranks, on a mesh the recipe generates itself. Honest scope: conda-forge's `su2` ships **no meshes, no tutorial cases, and no `pysu2` binding**, so the recipe writes its own SU2-format mesh and drives the CLI binary — a mesh is geometry we can legitimately fabricate, unlike bundled reference data. No cross-tool check: SU2 is the only finite-volume code here and OpenFOAM solves a different regime, so this is reproduction against the *analytic* uniform solution.

## Run it

```bash
SU2_CFD euler.cfg                 # Euler solve; uniform Mach 0.5 is the exact solution
mpirun -n 2 SU2_CFD euler.cfg     # same case, domain-decomposed across 2 ranks (ParMETIS)
```

The recipe writes the config and a distorted channel mesh, runs both legs, and checks that the uniform flow is preserved to machine zero — the finite-volume analog of a patch test.

## Make it yours

| In the recipe | Swap for | What to know |
|---|---|---|
| self-generated 24×12 distorted channel mesh | your mesh (`.su2`, or convert from CGNS/Gmsh) | written in-code; interior nodes are sinusoidally skewed *on purpose* (below) — a Cartesian grid would make the check trivial. |
| Euler (inviscid) freestream case | your solver + BCs (RANS, incompressible, …) | SU2 ships `SU2_CFD`/`SU2_DEF`/`SU2_SOL`; free-stream preservation is solver-agnostic, a real case swaps the physics. |
| the free-stream check | your case's own identity | uniform flow is the case that needs *no* reference data — a real study brings its own known answer. |

**Leave the fixture:** free-stream preservation on a skewed mesh is a genuine correctness test with an exact answer and no external data. **Scale it** to your geometry and physics — the mesh format and CLI drive are the same.

## Shape, size, cost

One task, `c8g.large` (2 vCPU / 4 GiB — the two vCPUs are the two MPI ranks), TTL 8m, cap $0.05. Two Euler solves (250 iterations each, serial + 2-rank) on a ~288-cell mesh run in seconds; boot and the 0.29 GB `cfd-fv` image pull are the rest. **These timings are not compute cost.**

**Sizing:** the channel mesh is tiny; a real case scales with cell count and moves to a Krylov solver across ranks (SU2 is MPI-parallel via ParMETIS) — the [scaling knee](../../patterns/sizing.md) is where more ranks stop paying. Size a real model on its mesh.

<details>
<summary>As shipped: why free-stream on a distorted mesh, the MPI check, arm64, pins, smoke check, run + verify</summary>

### Why free-stream preservation, on a *distorted* mesh

Uniform flow (constant Mach 0.5) is an exact solution of the Euler equations, so a correct finite-volume scheme must hold it unchanged — the RMS density residual falls to machine zero. The test only means something on a **non-orthogonal** mesh: *any* scheme preserves freestream on an axis-aligned grid, but a scheme with wrong metric/geometric terms drifts on skewed cells. So the recipe sinusoidally distorts the interior nodes (boundaries stay straight, so the four markers are unchanged) and asserts the final RMS density residual reaches **log10 ≤ −12** — observed **−14.46** serial, **−14.43** on 2 ranks. It's the finite-volume analog of a patch test: exact-or-wrong, and the distortion is what makes it a real check of the metric terms.

### MPI decomposition — real, not nominal

The 2-rank run reaches the same machine-zero residual **and** calls ParMETIS to partition the mesh (`graph partitioning complete`), which the **serial run does not** — so it's genuine domain decomposition, not a nominal rank count that a serial fallback could fake ([rank-count guard](../../practices/mpi-rank-count.md)).

### arm64 from inside

`SU2_CFD`'s ELF header reports `e_machine` **183 (AArch64)**, asserted directly from the binary — genuinely arm64, not trusted from an image tag.

### Pins (data tier: synthetic / in-code)

| | |
|---|---|
| image | `quay.io/aarchsci/cfd-fv@sha256:830eaf94…` (`cfd-fv` env — SU2 8.5.0 + OpenMPI, cosign-signed, `linux/arm64`) |
| input | none — the SU2-format mesh and the Euler config are written in-code (conda `su2` ships no meshes) |

### Smoke check (inside the task; measured before launch)

| observable | assertion | observed |
|---|---|---|
| su2_aarch64 | `SU2_CFD` ELF e_machine == 183 | 183 |
| serial_freestream | final log10(RMS ρ) ≤ −12 | −14.46 |
| mpi2_freestream | final log10(RMS ρ) ≤ −12 | −14.43 |
| mpi2_decomposed | ParMETIS partitions in 2-rank, absent in serial | ✓ / ✓ |

### Run + verify

```sh
make run RECIPE=su2
make ls  RECIPE=su2
```

The smoke check runs inside the task; the bucket listing is the second half ([exit 0 isn't proof](../../practices/container-path.md)). Expect `smoke-check.txt` with both residuals and the decomposition result. Re-run: `make run` launches a fresh task and overwrites this prefix — no spec edit needed.

</details>
