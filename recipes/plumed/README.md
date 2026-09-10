---
tool: plumed
tool_version: 2.9.2
env: md
image: quay.io/aarchsci/md@sha256:1ee941664add6f83b367c012d0cc670ffc837e83ee491125993de72e88c22ab9
spawn_version: 0.104.0
---
# PLUMED → GROMACS — collective variables computed live during an MD run

GROMACS runs a rigid-water MD with PLUMED attached (`-plumed`), so PLUMED computes collective variables from the coordinates at every step — the live-CV path under any biased-sampling run.

> **What this covers.** A 50-step MD of 216 rigid waters with PLUMED computing a distance and an angle — proof GROMACS and PLUMED are coupled correctly on Graviton4 and PLUMED's CV machinery is right. Not a benchmark; no metadynamics or biased sampling (a restraint check would be sampling-dependent — this uses a fixed-geometry CV instead).

## Run it

```bash
gmx_mpi mdrun -deffnm md -plumed plumed.dat    # GROMACS integrates, PLUMED reads coords every step
```

`plumed.dat` computes an O-H distance and an H-O-H angle and prints them to `COLVAR`. One task; GROMACS hands its coordinates to PLUMED in the same container. The input (spc216 water) is bundled in the gromacs package, so nothing is staged.

## Make it yours

| In the recipe | Swap for | What to know |
|---|---|---|
| a fixed-geometry CV (rigid-water O-H, H-O-H) | your own CVs / a biased simulation | rigid water makes the CV an *exact force-field constant* to check against; a biased CV would be sampling-dependent (weaker). |
| `PLUMED_KERNEL=/opt/conda/lib/libplumedKernel.so` | keep it — set before `mdrun` | **load-bearing:** GROMACS 2026.3's PLUMED integration aborts with "plumed … not available" unless this points at `libplumedKernel.so`; it is not set by default in the image. |

Deterministic — **nothing is determinism scaffolding**. **Leave the fixture:** the CV identities are defined constants recovered through the full coupling, exact at any run length. Leave-it.

## Shape, size, cost

One task, `c8g.large` (2 vCPU / 4 GiB), TTL 5m, cap $0.02. MD + CV computation is ~1 s. Recorded command window **106s** — boot, Docker install, and the 1.19 GB `md` image pull are the whole task ([why](../../practices/what-this-does-not-cover.md)). **These timings are not compute cost.**

<details>
<summary>As shipped: the coupling check, pins, smoke-check table, run + verify</summary>

### The check — a fixed-geometry CV validates the coupling

GROMACS integrates and hands its coordinates to PLUMED every step; PLUMED evaluates the CVs and writes `COLVAR`. A CV that comes out right validates the GROMACS → PLUMED coordinate passing *and* PLUMED's CV code together — the same chain-validation logic as [ase-phonopy](../ase-phonopy/README.md). Water is held rigid, so the intramolecular O-H distance and H-O-H angle are exact TIP3P constants (0.09572 nm, 104.52°), invariant across the trajectory — making the check exact-geometry rather than sampling-dependent.

### Pins (data tier: bundled in the image)

| | |
|---|---|
| image | `quay.io/aarchsci/md@sha256:1ee941664add6f83b367c012d0cc670ffc837e83ee491125993de72e88c22ab9` (tag `2026.09.04`, GROMACS 2026.3 PLUMED-patched + PLUMED 2.9.2, cosign-signed, `linux/arm64`) |
| input | spc216 water + amber99sb-ildn/tip3p, bundled in the gromacs package — nothing staged |

Same `md` image as [gromacs](../gromacs/README.md) / [lammps](../lammps/README.md).

### Smoke check (inside the task; measured before launch)

| observable | assertion | observed | catches |
|---|---|---|---|
| GROMACS+PLUMED ran | `Performance:` in log + COLVAR written | yes, 6 rows | coupling failed |
| COLVAR rows | exactly 6 (50 steps / stride 10 + t=0) | 6 | wrong stride |
| **DISTANCE CV** | 0.09572 nm ± 1e-4 (TIP3P O-H) | 0.09572 | CV computed wrong |
| **ANGLE CV** | 1.82422 rad ± 2e-3 (104.52°) | 1.82422 | CV computed wrong |
| distance invariant | spread < 1e-4 nm across frames | 0.0 | rigid constraint broke |
| angle invariant | spread < 1e-3 rad across frames | 0.0 | rigid constraint broke |

No fitted bands — the distance and angle are defined force-field constants recovered through the coupling, and their invariance confirms the rigid-water construction.

### Run + verify

```sh
make run RECIPE=plumed
make ls RECIPE=plumed
```

a completed run does **not** prove the outputs exist — an exit code says the command ran, never that its output is real; the smoke check runs *inside* the task, and the bucket listing is the second half of it. Expect two objects (`COLVAR`, `smoke-check.txt`). Re-run: bump the `-r1` suffix.

</details>
