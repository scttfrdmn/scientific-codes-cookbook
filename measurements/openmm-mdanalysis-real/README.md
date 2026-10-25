# OpenMM on a real workload — the Amber20 benchmark suite's DHFR and STMV, Graviton4, Intel and AMD

> **Graviton4 runs 1 ns of DHFR 1.30× faster than same-generation Intel and 1.20× faster than AMD,
> and is 34–38% cheaper per nanosecond.** On all three chips OpenMM matches `sander` term by term
> on the same bytes, to 1e-10 for Lennard-Jones on a million atoms. Along the way it caught a bug in
> `sander`'s default long-range vdW correction, which is wrong by 21,605 kcal/mol on STMV.

Workload: the [Amber20 benchmark suite](https://ambermd.org/Amber20_Benchmark_Suite.tar.gz), the
archive OpenMM's own `examples/benchmarks/benchmark.py` downloads for its `amber20-*` tests, run by
the suite's own production protocol: 4 fs steps (hydrogen-mass-repartitioned topologies), SHAKE on
bonds to H, a 9 Å cutoff, PME, NVE, starting from the suite's equilibrated restarts.

- **JAC** — DHFR in TIP3P, **23,558 atoms**, for **250,000 steps = 1 ns**. That is the suite's
  production length (`mdin.GPU`, `nstlim=250000`).
- **STMV** — satellite tobacco mosaic virus in water, **1,067,095 atoms**, for **1,000 steps**.
  That is 10× the suite's CPU length (`mdin.CPU`, `nstlim=100`).

## Run it

```sh
B=$(make -s print-bucket)
bash measurements/openmm-mdanalysis-real/stage-inputs.sh "$B"   # fetches the suite ON AWS, pins it
for a in arm64 x86_64 amd; do                                    # serially
  spawn task run --wait --spec "$(python3 scripts/spec_arch.py ambertools $a $B r1 measurements/openmm-mdanalysis-real/01-sander.task.json)"
  spawn task run --wait --spec "$(python3 scripts/spec_arch.py openmm-mdanalysis $a $B r1 measurements/openmm-mdanalysis-real/real.task.json)"
done
```

`01-sander.task.json` runs the [ambertools](../../recipes/ambertools/README.md) recipe's pinned
`sander` for single-point references. `real.task.json` is OpenMM. Inside it, the JAC run is just
this:

```python
prm = app.AmberPrmtopFile("JAC.prmtop"); crd = app.AmberInpcrdFile("JAC.inpcrd")
system = prm.createSystem(nonbondedMethod=app.PME, nonbondedCutoff=0.9*unit.nanometer, constraints=app.HBonds)
integ = mm.VerletIntegrator(4*unit.femtosecond); integ.setConstraintTolerance(1e-6)
ctx = mm.Context(system, integ, mm.Platform.getPlatformByName("CPU"), {"Threads": "32"})
ctx.setPositions(crd.positions); ctx.setVelocities(crd.velocities); integ.step(250000)
```

## Results

32 threads on an `8xlarge` (32 vCPU, 64 GiB) of each chip. Same OpenMM 8.6.1.dev-b399af4: the
aarch.science `comp-chem` image on arm64, and its linux-64 twin on both x86 chips. Same staged bytes.

| chip | instance | JAC 1 ns | JAC ns/day | STMV 1,000 steps | STMV ns/day | tool wall | billed | peak mem | $/hr | compute $/result | billed $/result |
|---|---|---|---|---|---|---|---|---|---|---|---|
| **Graviton4** | `c8g.8xlarge` | **1300 s** | **66.5** | **204 s** | **1.70** | 1986 s | 2147 s | 11.72 GiB | 1.2762 | **0.704** | **0.761** |
| Intel (Xeon 6975P-C) | `c8i.8xlarge` | 1683 s | 51.3 | 355 s | 0.97 | 2508 s | 2626 s | 14.45 GiB | 1.4994 | 1.045 | 1.094 |
| AMD (EPYC 9R45) | `c8a.8xlarge` | 1561 s | 55.3 | 249 s | 1.39 | 2179 s | 2263 s | 14.48 GiB | 1.7243 | 1.044 | 1.084 |

A *result* is the whole task. That is every single point (CPU, the double-precision Reference
platform, and the split by term), 1 ns of JAC, and 1,000 steps of STMV. *Tool wall* is `real.py`
start to finish; *billed* runs from EC2 launch to terminate.

- **Per nanosecond of DHFR**, in compute dollars: **Graviton4 $0.46**, Intel $0.70, AMD $0.75.
  Graviton4 is 1.30× faster than Intel and 1.20× faster than AMD, and **34% / 38% cheaper per ns**.
  AMD is 8% faster than Intel but dearer per ns, because its hour costs 15% more.
- **Per vCPU, which is what is rented.** `c8g` and `c8a` vCPUs are full cores
  (`Thread(s) per core: 1`). `c8i`'s are 16 cores × 2 hyperthreads, so 32 OpenMM threads on Intel
  share cores pairwise. This compares like-for-like rentals, not cores.
- **STMV ranks the chips the same way** (Graviton4 1.22× AMD, 1.75× Intel). Treat its absolute
  timing as indicative. Two Graviton4 runs of the same spec gave 1.33 and 1.70 ns/day, while the
  two JAC runs agreed to 1% (67.20 and 66.46).
- **Thread scaling, Graviton4** (sizing probe, 200-step windows): JAC 32.8 / 51.2 / 67.0 ns/day at
  8 / 16 / 32 threads (1.56×, then 1.31× per doubling). STMV 0.65 / 1.07 / 1.60. The knee is past
  16 threads for DHFR. 32 was used everywhere.
- **Compute-dominated:** 84–161 s of overhead (boot, Docker, image pull or the x86 env install,
  staging) against 33–42 minutes of work. The x86 peak memory is higher because that route
  installs the env into the container at start, and the cgroup counts its page cache. Python's
  own peak RSS is 11.4 GiB on all three.
- Prices are the on-demand us-west-2 rates spawn prints. **n = 1 per chip for the table** (Graviton4
  ran twice; the second run is tabulated). The trajectories are paths, so the timings describe one
  computation each, not a distribution.

## Why this size

DHFR/JAC is *the* standard biomolecular MD benchmark. 23,558 atoms is an ordinary
protein-in-water system, and 1 ns is the suite's own production run. MD cost is linear in steps once
the rate is steady, so a 100 ns campaign is this run 100 times longer, at the $/ns above. STMV, at
a million atoms, is the size where CPU OpenMM stops being comfortable (1–2 ns/day here). It is what
the cross-code check runs at, so that check covers production scale rather than only a toy system.

## What held at scale, on all three chips

| assertion | bound | Graviton4 | Intel | AMD |
|---|---|---|---|---|
| platform; threads, from the context | `CPU`; `Threads` == 32 | CPU, 32 | CPU, 32 | CPU, 32 |
| atoms; constraints | suite README: 23,558 / 1,067,095 | 23,558 / 1,067,095; 22,290 / 978,008 | same | same |
| **OpenMM == sander**, bond / angle / dihedral (Reference platform, both double) | ≤ 1e-6 rel | worst 1.9e-7 (JAC dihedral) | same | same |
| **OpenMM == sander, Lennard-Jones** (`vdwmeth=0` / no dispersion correction) | ≤ 1e-7 rel | **6.5e-9** JAC, **1.4e-10** STMV | same | same |
| **OpenMM == sander, electrostatics**, after the Coulomb-constant ratio | ≤ 1e-5 rel (sander's `dsum_tol`) | −1.2e-6 JAC, −1.3e-6 STMV | same | same |
| OpenMM's dispersion correction == its closed form from the prmtop's A/B and type counts | ≤ 1e-7 rel | 2.0e-10 / 2.5e-10 | same | same |
| CPU platform (single) == Reference platform (double), each term | ≤ 1e-6 rel | ≤ 4.1e-8 | ≤ 1.0e-7 | ≤ 6.3e-8 |
| **JAC NVE conserved over 1 ns** | leak < 5% of *kT* per DoF per ns | **0.029%** | 0.031% | 0.037% |
| JAC temperature, 1 ns NVE | mean 290–310 K | 295.99 K | 296.14 K | 295.94 K |
| SHAKE at the last step (JAC / STMV) | max \|d−d₀\|/d₀ < 1e-5 | 2.0e-7 / 4.1e-7 | 3.6e-7 / 6.1e-7 | 2.4e-7 / 5.9e-7 |
| **MDAnalysis reads OpenMM's DCD against the Amber topology**: atoms, 100 frames, box, and every constrained distance in every frame | < 1e-4 rel (float32 DCD) | **1.54e-5** over 2,229,000 | 1.68e-5 | 1.78e-5 |

**How the cross-code check was made like-for-like.** OpenMM is given sander's own Ewald coefficient
(`erfc(βr_c)/r_c = 1e-5`, by sander's own bisection: 0.30767639 Å⁻¹, printed 0.30768) and its grid
(64³; 240×225×225). sander runs at `order=5`, because OpenMM's PME uses 5th-order B-splines and
sander defaults to 4. Two differences are units, not physics:

- **The Coulomb constant.** Amber's charges carry 18.2223² = 332.0522 kcal Å/mol e², while OpenMM
  uses 1/4πε₀ = 332.0637. That makes OpenMM's electrostatics **3.46e-5** larger on any system. The
  raw ratio was 3.34e-5 on JAC and 3.33e-5 on STMV. After dividing out the constant, what is left
  (1.2–1.3e-6) is PME.
- **OpenMM's dispersion correction averages over N(N+1)/2 pairs including self-pairs**
  (`NonbondedForceImpl::calcDispersionCorrection`). It is therefore O(1/N) off the textbook N² form:
  7.4e-5 on JAC, 1.5e-6 on STMV. That is OpenMM's documented form, so it is asserted against that
  form, to 2e-10.

Leaving OpenMM's PME at its defaults (α = 0.29203 Å⁻¹ from `ewaldErrorTolerance` 5e-4) moves the
nonbonded energy by 0.68 kcal/mol on JAC and 10.2 on STMV. That is the size of the mismatch you get
if the modes are not matched first.

### sander's default long-range vdW correction is wrong on STMV

With its default `vdwmeth=1`, sander's VDWAALS for STMV is **21,605 kcal/mol** higher than OpenMM's
— 5.6% of the LJ energy. JAC agrees. The `vdwmeth=0` runs isolate it: sander's correction is
**−3595.1791** kcal/mol, where the closed form from the prmtop's own coefficients gives
**−25,200.29**. Evaluating that closed form with each n_i·n_j wrapped to a signed 32-bit integer
gives **−3595.1791**, matching sander to every printed digit.

The overflow needs a type with more than 46,340 atoms (√2³¹). STMV has 300,053 water oxygens; JAC's
largest LJ-bearing type has 7,023. The same −3595.1791 appears on all three chips, so it is in the
code, not the build. Any sander run with `vdwmeth=1` (the default) on a box of more than 46,340
waters — about 140,000 atoms of TIP3P — is affected. It is
`nvdwclas(i)*nvdwclas(j)` in default integers (`sander/ew_setup.F90:2289`, AmberTools 26).
Upstream reports pmemd gives the same wrong number, and the fix is in msander and cpptraj 7.12.0 but
not yet in sander or pmemd ([AmberClassic#6](https://github.com/Amber-MD/AmberClassic/issues/6)). This measurement asserts its cross-check with the correction off on both sides, and
reports sander's value rather than asserting it.

## Fixed point or path

| | Graviton4 | Intel | AMD | kind |
|---|---|---|---|---|
| Reference platform (double) single points, every term, both systems | JAC nonbonded −75813.1839827480 | …7481 | …7481 (== Intel, bit for bit) | fixed point: Intel == AMD identical, arm64 within 1–2 ulp (≤ 1.6e-15) |
| term split for the sander check (Reference) | `elec` JAC −84907.1204236190 | identical | identical | fixed point: JAC bit-identical on all three |
| CPU platform (single) single point, nonbonded | −75813.181813 | −75813.186699 | −75813.183837 | single-precision kernels: 2.7–6.4e-8 between chips, and **Intel ≠ AMD on the same binary** |
| JAC Etot at 10 ps / 1 ns | −58468.57 / −58471.06 | −58476.89 / −58462.71 | −58477.04 / −58470.44 | path, reported |
| JAC conservation leak; mean T | 0.029%; 295.99 K | 0.031%; 296.14 K | 0.037%; 295.94 K | statistics of a path: agree |

- **Every assertion above holds on every chip; every trajectory differs.** The double-precision
  Reference platform is the fixed point: bit-identical between the two x86 chips, which run one
  binary, and within the last bit or two on arm64, which is a separate build. The CPU platform
  computes nonbonded forces in single precision. Its step-0 energy differs between chips at a few
  1e-8, including between Intel and AMD, which run the same binary. So it selects code by CPU at run
  time; which code path is not established here. The trajectory amplifies the difference, and the
  paths separate from step 0.
- **The CPU platform is not quite reproducible run to run on one chip, either.** Across three
  Graviton4 runs the JAC single point was identical, and the STMV one moved by 1e-9 relative in one
  of them. That is 20–60× smaller than the spread between chips, so the chip dominates. The env's
  OpenBLAS reports `neoversev2` on arm64 and `Cooperlake` on both x86 chips; whether OpenMM's force
  path touches it was not checked.
- So assert energies at a single evaluation and conservation laws over the run. Compare
  trajectories only as statistics (leak, temperature), never value by value.

## Caveats

- **STMV's first 0.4 ps is a transient.** KE falls 4.9% and Etot rises ~4,100 kcal/mol between step
  0 and step 100, then drifts ~900 kcal/mol over the remaining 3.6 ps. This happens identically on all three
  chips. Projecting the restart onto OpenMM's constraints first moves Etot by only 0.10 kcal/mol, so
  constraint mismatch does not explain it. Not diagnosed. A 4 ps window cannot support a
  conservation claim on a million atoms, so STMV's dynamics are reported (`nve_slope (4 ps)`), not
  asserted. The conservation claim is JAC's, over 1 ns. JAC shows the same shape, much smaller (KE
  −1.7% by 10 ps).
- `getPMEParametersInContext` raised `std::bad_cast` on this build (arm64), so OpenMM's default PME
  parameters come from its documented formulas instead.

## Pins

| | |
|---|---|
| suite | `Amber20_Benchmark_Suite.tar.gz` from ambermd.org, sha256 `7fee3a02…16f401` (`inputs.sha256`), re-checked in every task |
| OpenMM image | arm64 `quay.io/aarchsci/comp-chem@sha256:b5c1b1ec…`; x86 the env's linux-64 twin `envs-x86_64/comp-chem-1243f25ebfec`, the recipe's pins |
| sander image | arm64 `quay.io/aarchsci/md@sha256:28537712…`; x86 `envs-x86_64/md-f7ed8789d24e`, the ambertools recipe's pins. AmberTools 26.0 |
| scripts | `run.sh`, `real.py`, `check.py`, `sander.sh`, each pinned by sha256 in its spec (`mkspec.py` writes them) |

**Data tier: a versioned public release.** The suite has no DOI. It is the archive OpenMM's own
benchmark script fetches, it has not changed since 2020-05-23 (`Last-Modified`), and it is pinned by
sha256. `00-fetch.task.json` downloaded it on AWS. `probe.task.json` is the sizing probe; it ran an
earlier revision of `real.py` with the same dynamics. `compare.py` and `collect.py` produce the
tables above from `results/` (per-chip check, results, energies, and the sander mdouts).
