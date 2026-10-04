---
tool: ambertools
tool_version: "26.0"
env: md
image: quay.io/aarchsci/md@sha256:1ee941664add6f83b367c012d0cc670ffc837e83ee491125993de72e88c22ab9
spawn_version: 0.115.0
last_verified: 2026-10-03
---
# AmberTools — 100 ps of solvated NVE, and ff14SB checked against GROMACS

`tleap` builds a solvated peptide, `sander` runs a 100 ps NVE production trajectory, and the same force field is cross-checked against a second MD engine. For anyone running Amber force fields on ARM.

## Run it

```bash
spawn task run --spec "$(make -s spec RECIPE=ambertools)" --wait   # ~17 min; nothing staged
```

```bash
tleap -f l.in                       # ACE-ALA-NME + solvateoct TIP3P 10.0 -> 2101 atoms
sander -O -i nve.in -p sol.parm7 -c heat.rst -o nve.out -x nve.nc   # irest=1 is load-bearing
```

## Make it yours

| In the recipe | Swap for | What to know |
|---|---|---|
| ACE-ALA-NME in a TIP3P octahedron | your own system | `solvateoct` has no RNG, so the atom count is reproducible and asserted exactly — a different solute needs its own count. |
| `nstlim=100000, dt=0.001` | longer, or `dt=0.002` | the conservation check normalises per ns, so it survives a length change; a larger timestep legitimately leaks more. |
| ff14SB / TIP3P | ff19SB, OPC, … | ff19SB adds CMAP, which **breaks the ParmEd → GROMACS conversion** the cross-check depends on. |
| `irest=1, ntx=5` | — | **do not drop these.** Without them sander discards the equilibrated velocities and starts from rest, so the run is not NVE at temperature at all. |

**Leave the system size.** `sander` is serial (and `pmemd`, the scalable engine, is licence-gated and
never in AmberTools), so 2,101 atoms with PME is about the largest system reaching 100 ps inside a
sane TTL — and the conservation check sharpens with trajectory *length*, not atom count.

## Which box

`c8g.large` (2 vCPU / 4 GiB) — `sander` is single-threaded, so cores buy nothing. Measured on
Graviton4: minimise **18 s**, heat **101 s**, 100 ps NVE **914 s** = **9.45 ns/day**.

**Generation matters more here than for any other code in this catalog: 2.50× Gv2→Gv5** (5.36 →
13.42 ns/day), the same 100 ps costing **49% less** even though `$/hr` rises 27.8% —
[full ladder](../../measurements/ambertools-real/README.md). And **the local sizing run was 1.6×
optimistic**, the dangerous direction for a TTL: 14.95 ns/day on a laptop against 9.45 here, so 2× the
local number leaves 1.45× margin, not 2×. TTL 28m, cap $0.05
([why that's a cost cap](../../patterns/layout-and-effective-cost.md)).

<details>
<summary>As shipped: a cross-engine force-field identity, a conservation law on a thermal scale, and two metrics that were wrong first</summary>

### The checks

| observable | assertion | observed |
|---|---|---|
| atoms, vacuum | exactly 22 (ACE-ALA-NME) | 22 |
| atoms, solvated | exactly 2101 (+693 TIP3P) | 2101 |
| water count consistent | (sol − vac)/3 = 693 | 693 |
| **sander vs GROMACS** | **< 0.01 kcal/mol on identical bytes** | **0.0011** |
| energy terms matched | 7/7 by name in both outputs | 7/7 |
| NVE frames | ≥ 100 | 100 |
| started at temperature | T(frame 0) > 200 K (`irest=1` worked) | **306.3 K** |
| mean temperature | 240–360 K | 305.2 K |
| trajectory decodes | mdtraj reads sander's NetCDF + prmtop | 100 × 2101 |
| water constraints | 1386 O-H + 693 H-H (3 per rigid water) | 1386 + 693 |
| **SHAKE holds water** | **max\|d(O-H) − 0.9572\| < 1e-3 Å** | **8.88e-06** over 138,600 |
| water stays rigid | H-H spread < 1e-3 Å | 1.31e-05 over 69,300 |
| **NVE conserved** | **leak < 5% of *kT* per DoF per ns** | **0.76%** |

### Two codes, one force field, the same coordinates

ParmEd converts the ff14SB prmtop to GROMACS format and `gmx mdrun -nsteps 0` recomputes the
energy. Two unrelated codebases agree to **0.0011 kcal/mol** — and the tolerance is *print
precision*, not how close they landed: sander prints 4 decimals on each of 7 terms (±0.0004) and
GROMACS 6 significant figures on a −335 kJ term (±0.0001). Worst single term 0.0029 kcal/mol
(`EEL`). ff14SB has no CMAP and uniform 1-4 scaling, so the conversion is lossless.

Getting a meaningful comparison took two fixes, both of which look like a force-field disagreement
([the rule](../../practices/cross-checks.md)):

- **Match the modes.** Modern GROMACS has no infinite-cutoff mode, so sander's in-vacuo `cut=9999`
  is reproduced with a 10 nm box and a 4 nm cutoff — every pair inside, every periodic image
  outside — plus `coulomb-modifier = none` and `vdw-modifier = none` to remove GROMACS' default
  potential shift, which sander does not apply.
- **`.gro` writes 3 decimals in nm = 0.01 Å.** That rounding alone moved the bond energy 6×
  (0.0206 → 0.1406 kcal/mol) and made two correct codes look **0.29 kcal/mol** apart. Writing
  coordinates at `precision=8` fixed it. A file-format artifact, wearing a physics result's
  clothes.

### Conservation, on a scale that means something

Energy conservation is a law, so the only question is what the integrator's discretisation costs —
and the meaningful scale is thermal, not a fraction of the total. The run leaks **4.54e-03 kcal/mol
per degree of freedom per ns**, which is **0.76% of *kT*** at 300 K. The 4,209 degrees of freedom
are computed from the constraint count mdtraj reports (3 × 2101 − 2091 − 3), not assumed. For
reference, `|drift|/|Etot| = 3.77e-04` — a number that sounds reassuring and says nothing.

**SHAKE is a geometric constraint, so it is exact.** TIP3P's O-H length is 0.9572 Å and `ntc=2`
holds it every step: observed `max|d − 0.9572| = 8.88e-06 Å` across **138,600 measurements** (693
waters × 2 bonds × 100 frames). Two things had to be right first:

- **AmberTools constrains *three* distances per rigid water, not two** — the two O-H bonds and the
  H-H distance, which is how the HOH angle is held. A "all bonds inside water" filter picks up
  3 × 693 = 2079 pairs, and the H-H ones sit at 1.514 Å, failing a 0.9572 Å assertion for a reason
  that has nothing to do with SHAKE. They are split by element and each asserted for what it is;
  the H-H *value* follows from the prmtop's angle, so what is asserted there is that it does not
  move (spread 1.31e-05 Å, implying 104.491°).
- **cpptraj's `distance` over multi-atom masks is a centre-of-mass distance.**
  `distance :WAT&@O :WAT&@H1` measures the separation of two 693-atom centroids — about 0.008 Å —
  not per-water bond lengths. It reported `max|d − 0.9572| = 0.949 Å` and looked like catastrophic
  SHAKE failure. cpptraj keeps the peptide RMS (1.045 Å max); mdtraj does the bond lengths.

### What is reproducible here, and what is not

The single-point energies are identical run to run and machine to machine, because they are one
evaluation on coordinates `tleap` writes deterministically. **The trajectory is not** — the same
pinned image gave `Etot(0) = −5071.2472` kcal/mol on Graviton4 and `−5092.6754` on a laptop, because
the minimiser's last floating-point bits differ and 100,000 MD steps amplify that.

But "it varies by machine" turned out to be too strong, and the reason is worth knowing if you care
about reproducibility at all. The [generation ladder](../../measurements/ambertools-real/README.md)
put this same digest on five machines and got exactly **three** trajectories, predicted one-to-one by
the **OpenBLAS kernel the process picks from the host CPU at load time** (`liblapack.so.3` here is
`libopenblasp-r0.3.34.so`, built `DYNAMIC_ARCH`). Confirmed by changing one variable on one host:
`OPENBLAS_CORETYPE=NEOVERSEN1` on **Graviton4** reproduces the laptop's trajectory exactly — drift
−2.3891 kcal/mol, matching to four decimals — for a 0.4% slowdown.

**So a pinned image digest does not pin the numerics.** Pinning is still necessary; it just isn't
sufficient. This recipe deliberately does not set `OPENBLAS_CORETYPE`, because its job is to report
what a normal run does on each chip — set it if you need an exact trajectory back.

Which is the sharper argument for how this recipe is checked: you cannot tell from a trajectory
number *which group you are in*, so every assertion above is a conservation law, a geometric
constraint, or a cross-code identity, and none is a remembered value. All of them held on all four
chips. Asserting the drift itself would be
[exact one run and different the next](../flye/README.md).

### Pins

| | data tier |
|---|---|
| AmberTools 26.0 + GROMACS | `quay.io/aarchsci/md@sha256:1ee941664add…` (`linux/arm64`, cosign-signed) |
| ff14SB, TIP3P, ParmEd 4.3.1, mdtraj | in the image; **nothing is staged** |

The whole system is built in-task by `tleap`, so there is no `stage-inputs.sh`.

### Run + verify

```sh
spawn task run --spec "$(make -s spec RECIPE=ambertools)" --wait
make ls RECIPE=ambertools
```

The smoke check runs inside the task and the bucket listing is the second half of it
([exit 0 isn't proof](../../practices/container-path.md)). Expect `smoke-check.txt`,
`sander-single-point.out`, `gromacs-single-point.log`, `nve.out`, `tleap.log`, `cpptraj.log`.

</details>
