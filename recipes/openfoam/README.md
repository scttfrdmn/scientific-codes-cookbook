---
tool: openfoam
tool_version: v2412
env: cfd
image: docker.io/opencfd/openfoam-default@sha256:b8b674f7d634a7b272efa00beabfe13eefdfe9d83b28281522544d06d47a17e4
spawn_version: 0.104.0
last_verified: 2026-09-12
---
# OpenFOAM — lid-driven cavity flow on Graviton

`icoFoam` solves the lid-driven cavity — the canonical incompressible-laminar CFD test — and writes a real velocity and pressure field, verified by mass conservation. For anyone who knows OpenFOAM and wants it on arm64.

> **What this covers.** OpenFOAM v2412 (`blockMesh` + `icoFoam`) running the shipped `cavity` tutorial (20×20 mesh, Re=10) to a converged field on Graviton4 — from the **upstream `opencfd/openfoam-default` multi-arch image**, the catalog's first recipe sourced from an official upstream container rather than an aarch.* env, and its first CFD code. Not a benchmark: the Reynolds number and mesh are the tutorial's, too coarse for a Ghia-type profile match (see Make it yours).

## Run it

```bash
# inside the pinned container; the tutorial ships in the image, nothing is staged
source /usr/lib/openfoam/openfoam2412/etc/bashrc
cp -r $FOAM_TUTORIALS/incompressible/icoFoam/cavity/cavity .
cd cavity
blockMesh          # generate the 20x20 cavity mesh
icoFoam            # solve incompressible laminar Navier-Stokes to t=0.5 s
```

One tool, one image — OpenFOAM bundles its own utilities (`blockMesh`, `icoFoam`, `postProcess`), so unlike the aarch.bio [one-tool-per-image](../../practices/container-path.md) chains this is a single task running several OpenFOAM commands.

## Make it yours

| In the recipe | Swap for | What to know |
|---|---|---|
| the `cavity` tutorial (20×20, Re=10) | your own case directory | staged through S3 as a tar — a case is a directory, so [flatten it](../../practices/container-path.md). The mesh and BCs are the fixture. |
| `icoFoam` (incompressible, laminar) | the solver your physics needs (`simpleFoam`, `pimpleFoam`, …) | each ships in the same image; the continuity identity below is solver-agnostic. |
| Re=10 (nu=0.01, U=1, L=0.1) | a higher Reynolds number | **the one number that limits the fixture:** at Re=10 the flow is a single viscous vortex — too coarse and viscous to match published benchmark data. |

**Leave the fixture** to prove OpenFOAM runs and conserves mass — the cavity is deterministic and hand-checkable. **Scale it** to reproduce a published result: a 129×129 mesh at Re=100/400/1000, run to steady state with `simpleFoam`, matches [Ghia et al. (1982)](https://doi.org/10.1016/0021-9991(82)90058-4) centerline velocities — the [reproduce-a-published-result](../../practices/reference-from-tests.md) move, a real validation rather than this working example.

## Shape, size, cost

One task, `c8g.large` (2 vCPU / 4 GiB), TTL 5m, cap $0.03. The solve is **0.03 s** on 400 cells; boot, Docker install, and the 0.38 GB image pull are the whole task. Recorded command window **57 s** (≈ $0.0013), so TTL retightened 8m → 5m from the real run ([why](../../practices/what-this-does-not-cover.md)). **These timings are not compute cost.**

**Sizing:** the tutorial is trivial and CPU-bound on one core; a real case scales with mesh — cells drive RAM and cores, and `decomposePar` splits the domain for MPI, where the [scaling knee](../../patterns/sizing.md) lives. No family question for the fixture; size a real mesh on its cell count.

<details>
<summary>As shipped: the conservation identity, pins, smoke check, run + verify</summary>

### Why mass continuity is the right check

icoFoam solves incompressible Navier–Stokes, so **mass is conserved exactly** — the solver reports a cumulative continuity error each step, and on a correct run it sits at machine-noise (~1e-18), not a tuned band. That is the CFD conservation identity, the [same class as](../../practices/cross-checks.md) salmon's TPM sum: it must hold for the solve to be physical at all, so it needs no headroom and can't go flaky. The lid boundary condition gives a second exact check — max |U| equals the imposed lid speed 1.0 — and a developed vortex shows in the pressure range.

Every observed value below was **bit-identical on Apple arm64 (local) and Graviton4 (the verifying run)** — the continuity error to the digit — so these are exact invariants of the pinned solve, not per-box bands.

### Pins (data tier: synthetic / in-image)

| | |
|---|---|
| image | `docker.io/opencfd/openfoam-default@sha256:b8b674…a17e4` (tag `2412`, OpenFOAM v2412, upstream ESI multi-arch — the `linux/arm64` manifest, platform `linuxARM64GccDPInt32Opt`) |
| input | the `cavity` tutorial shipped in the image (`$FOAM_TUTORIALS/incompressible/icoFoam/cavity/cavity`) — nothing staged |

`opencfd/openfoam-default:2412` is a multi-arch manifest list; this recipe pins the arm64 entry by digest so the pull can't drift to amd64 or emulate ([a manifest list is not evidence of arm64](../../practices/container-path.md)) — and the run asserts `readelf Machine=AArch64` from inside, so arm64 is verified, not trusted.

**Provenance — the catalog's first upstream, non-conda source.** Every other recipe pulls from aarch.bio/aarch.science (conda-built, cosign-signed) or is a pipeline of those; this one pulls an **official image published by OpenCFD**, the OpenFOAM project itself. The digest pin is reproducibility-of-record — the pull is byte-identical while that digest exists — but its **durability is Docker Hub's best-effort retention**, which we don't control, unlike aarch.* where GC-proof tag-keeping can be requested. Not cosign-verified (an upstream image). For perpetual reproducibility, mirror the digest. Same tier as any [moving-registry artifact](../../practices/what-this-does-not-cover.md): the bytes reproduce until the publisher's registry drops them.

### Smoke check (inside the task; measured before launch)

| observable | assertion | observed |
|---|---|---|
| solver completed | `End` in the icoFoam log | End reached |
| mass continuity | \|cumulative error\| < 1e-6 | 4.19e-18 |
| Courant stability | 0 < max Co < 1.0 | 0.852 |
| lid BC respected | max \|U\| ≈ 1.0 (lid speed) | 1.00000 |
| vortex developed | pressure spans past ±1 | [−4.37, 4.85] |

### Run + verify

```sh
make run RECIPE=openfoam
make ls  RECIPE=openfoam
```

The smoke check runs inside the task; the bucket listing is the second half ([exit 0 isn't proof](../../practices/container-path.md)). Expect three objects — `smoke-check.txt`, `log.icoFoam`, and `cavity-solution.tar` (the full case, tarred because a directory can't stage out). Re-run: `make run` launches a fresh task each time and overwrites this prefix — no spec edit needed.

</details>
