# PLUMED → GROMACS — collective variables computed live during an MD run

One task, two tools coupled. GROMACS runs a rigid-water MD with PLUMED attached
(`-plumed`), so PLUMED computes collective variables from GROMACS's coordinates at every
step. The smoke check confirms the CVs come out at the exact force-field geometry, which
validates the coupling — not the two tools in isolation.

> **What this recipe does and does not cover.** It runs a 50-step MD of 216 rigid water
> molecules with PLUMED computing a distance and an angle — enough to prove GROMACS and
> PLUMED are coupled correctly on Graviton4 and PLUMED's CV machinery is right. Not a
> benchmark; no metadynamics, free-energy sampling, or a biased CV (a restraint check
> would be sampling-dependent — the weakest shape; this uses a fixed-geometry CV instead).

## The chain, and why a fixed-geometry CV

GROMACS integrates and hands its coordinates to PLUMED every step; PLUMED evaluates the
CVs and writes `COLVAR`. So a CV that comes out right validates the **coupling** (GROMACS →
PLUMED coordinate passing) and PLUMED's CV code together — the same chain-validation logic
as `recipes/ase-phonopy`. Water is held rigid by constraints, so the intramolecular O-H
distance and H-O-H angle are **exact force-field constants** (TIP3P: 0.09572 nm, 104.52°),
invariant across the trajectory. That makes the check exact-geometry rather than
sampling-dependent: PLUMED must pass the coupling *and* compute the CV correctly to
reproduce a defined constant.

**One environment note worth recording:** GROMACS 2026.3's PLUMED integration requires
`PLUMED_KERNEL` to point at `libplumedKernel.so`, or `mdrun -plumed` aborts with "plumed …
not available". The recipe sets it (`/opt/conda/lib/libplumedKernel.so`); it is not set by
default in the image.

## Pins

| | |
|---|---|
| image | `quay.io/aarchsci/md@sha256:1ee941664add6f83b367c012d0cc670ffc837e83ee491125993de72e88c22ab9` |
| | tag `2026.09.04`, GROMACS 2026.3 (PLUMED-patched) + PLUMED 2.9.2, cosign-signed, `linux/arm64` |
| input | spc216 water + amber99sb-ildn/tip3p, **bundled in the gromacs package** — nothing staged |

**Data tier: bundled in the image.** Same `md` image as `recipes/gromacs`/`recipes/lammps`;
this recipe couples GROMACS and PLUMED.

## Smoke check

Measured in this image, before any launch.

| observable | assertion | observed |
|---|---|---|
| GROMACS+PLUMED ran | `Performance:` in mdrun log + COLVAR written | yes, 6 rows |
| COLVAR rows | exactly 6 (50 steps / stride 10 + t=0) | 6 |
| **DISTANCE CV** | 0.09572 nm ± 1e-4 (TIP3P O-H, via PLUMED) | 0.09572 |
| **ANGLE CV** | 1.82422 rad ± 2e-3 (104.52°, TIP3P H-O-H) | 1.82422 |
| distance invariant | spread < 1e-4 nm across frames (rigid) | 0.0 |
| angle invariant | spread < 1e-3 rad across frames (rigid) | 0.0 |

The distance and angle are the exact-geometry identities — defined force-field constants,
recovered through the full coupling — and their invariance across frames confirms the
rigid-water construction (a spread, not a sampled mean). No fitted bands.

## Resources, and what the timings mean

2 vCPU / 4 GiB, `c8g` (resolves to `c8g.large`), TTL 5m, cap $0.02. The MD + CV computation
is **~1 second**.

**These timings are not compute cost.** Boot, the Docker install, and pulling the
**1.19 GB** `md` image are the whole task. The recorded run's command window was **106s**
(02:33:15 → 02:35:01 UTC), the CVs at their exact TIP3P values. TTL was **retightened from
that first real run**: 10m → **5m**, `cost_limit` $0.03 → $0.02. A loose TTL is a larger
blast radius, not caution; the recorded run used the original 10m. Disk is trivial.

## Running it

No `stage-inputs.sh` — the system is bundled in the image.

```sh
spawn task run --spec recipes/plumed/01-cv.task.json --wait
```

Then **check the bucket**, every time:

```sh
aws s3 ls s3://scicookbook-942542972736-us-east-1/runs/plumed/r1/
```

`--wait` exiting 0 does **not** prove the outputs exist (spore-host/spawn#561): the smoke
check runs *inside* the task, and the bucket listing is the second half of it. Expect two
objects (`COLVAR`, `smoke-check.txt`).

**Re-running.** `task_id` is fixed; bump the `-r1` suffix in both `task_id` and the output
prefix to keep both records.

**Note on parallel launches.** A transient AWS `Invalid IAM Instance Profile name` on a
parallel launch is the IAM-propagation race (spore-host/spawn#572), not a recipe fault —
re-run.
