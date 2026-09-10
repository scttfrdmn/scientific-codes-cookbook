---
tool: gromacs
tool_version: 2026.3
env: md
image: quay.io/aarchsci/md@sha256:1ee941664add6f83b367c012d0cc670ffc837e83ee491125993de72e88c22ab9
spawn_version: 0.104.0
---
# GROMACS — molecular dynamics of a 216-water box, reproducing a published energy

Run a short MD integration and land on the potential energy aarch.science published for this image — proof GROMACS 2026.3 is numerically correct on Graviton4.

> **What this covers.** 216 SPC waters, 40 fs (20 steps), on data GROMACS ships. Not a benchmark; no PME, membrane/protein, or multi-node. The binary is a NEON build (no SVE on Graviton, a conda-forge choice), so no timing here is GROMACS's ceiling on ARM.

## Run it

```bash
gmx_mpi grompp -f md.mdp -c spc216.gro -p topol.top -o md.tpr
gmx_mpi mdrun  -s md.tpr -ntomp 2          # → potential energy -9627.87 kJ/mol
```

One task: `grompp` builds the run input, `mdrun` integrates. `spc216.gro` and the force field ship inside the gromacs package, so nothing is staged.

## Make it yours

| In the recipe | Swap for | What to know |
|---|---|---|
| `spc216.gro` (216 SPC waters, bundled) | your own `.gro` + topology | staged through S3 for a real system; the bundled box is used because aarch.science published *its* energy to reproduce. |
| the 6-line `.mdp` (20 steps, no velocity generation) | your run parameters | with no velocities generated the run is deterministic and bit-stable across thread counts — which is why the energy is asserted exactly. |
| `-ntomp 2` | scale threads to your cores | a single-point energy is decomposition-invariant, so scale freely for speed; [assert the rank count](../../practices/mpi-rank-count.md) if you claim MPI. |

Nothing is determinism scaffolding (deterministic without velocity generation). **Leave the fixture:** 216 waters reproduces a published cohesive energy exactly and *is* the correctness proof; a bigger system is a longer run, not a more legible one, and sizing lives on the [sizing page](../../patterns/sizing.md). Leave-it.

## Shape, size, cost

One task, `c8g.large` (2 vCPU / 4 GiB — the MD is sub-second, so cores and memory don't bind correctness), TTL 5m, cap $0.02. Recorded command window 103s. **These timings are not compute cost** — boot and the 1.19 GB image pull are the whole task ([why](../../practices/what-this-does-not-cover.md)).

<details>
<summary>As shipped: the published-energy reproduction, sizing, pins, smoke check, run + verify</summary>

**A reproduction, not a plausible number.** aarch.science's `md.smoke.py` reported **−9627.9 kJ/mol** (−44.6/water) for 216 SPC waters on its build host; Graviton4 gives **−9627.87** — the same deterministic code on two hosts agreeing to five significant figures ([reproduce a published number](../../practices/reference-from-tests.md)). The ±1.5 kJ/mol band is only because the published figure was rounded; the run is bit-stable across `-ntomp` 1/2/4. Per-water energy (~−44 kJ/mol) is a physics floor a mis-built topology can't hit; `mdrun`'s `Performance:` summary prints only on clean completion.

| observable | assertion | observed |
|---|---|---|
| atoms | exactly 648 (216×3) | 648 |
| **potential energy** | −9629 … −9626 kJ/mol (published −9627.9) | −9627.87 |
| per-water energy | −46 … −43 kJ/mol | −44.573 |
| mdrun completed | `Performance:` summary present | yes |

**Sizing is a separate question from correctness.** Measured on benchMEM (81,743 atoms, 8→192 cores) on the [sizing page](../../patterns/sizing.md): the *launch* dominates — `mpirun`'s default binding pins all threads to one core (a **7× loss**) before any ranks-vs-threads tuning matters; it scales to 192 cores (11× throughput) but cost-per-result rises past the NUMA knee. The single-point energy never moves with core count, unlike an [assembler](../flye/README.md).

**Pins** (data tier: bundled — the input ships in the pinned gromacs package):

| | |
|---|---|
| image | `quay.io/aarchsci/md@sha256:1ee941…` (tag `2026.09.04`, GROMACS 2026.3-conda_forge, ARM_NEON_ASIMD, cosign-signed, `linux/arm64`) |
| input | `spc216.gro` + `amber99sb-ildn.ff`, bundled — nothing staged |

The `md` env carries both engines; [lammps](../lammps/README.md) runs the other in the same image.

**Run + verify.**
```sh
make run RECIPE=gromacs
make ls RECIPE=gromacs
```
Smoke check runs inside the task; the bucket listing is the second half ([exit 0 isn't proof](../../practices/container-path.md)). Re-running: bump the `-r1` suffix.

</details>
