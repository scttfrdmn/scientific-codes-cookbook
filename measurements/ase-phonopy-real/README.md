# phonopy with real DFT forces: bulk Si, Graviton4, Intel and AMD

> **AMD EPYC runs the whole calculation 2.5x faster than Graviton4 and is 44% cheaper per phonon
> calculation; Intel is 1.3x faster and 11% cheaper.** On all three chips, every identity
> phonopy has to satisfy holds, and every converged number agrees: frequencies within 1e-4 THz,
> energies within 4e-7 eV.

Workload: bulk silicon phonons from **real PBE forces**, the full phonopy workflow on the
standard frozen-phonon setup. GPAW 25.7.0 computes forces on **phonopy's own committed displaced
supercells** (`example/Si-QE`, 64-atom conventional 2×2×2; `example/Si`, 16-atom primitive 2×2×2,
at `v4.4.0`). Phonopy 4.4.0 then turns four force sets into force constants, a band path, a 31³
tetrahedron DOS and thermal properties to 1000 K. The four sets are GPAW on each supercell, plus
the **QE and VASP forces upstream committed** on the same bytes.

## Run it

```sh
B=$(make -s print-bucket)
measurements/ase-phonopy-real/stage-inputs.sh "$B"      # phonopy's examples + scripts, ~140 KB, sha256-pinned
for a in arm64 x86_64 amd; do
  spawn task run --spec "$(python3 scripts/spec_arch.py ase-phonopy $a $B r2 measurements/ase-phonopy-real/real.task.json)" --wait
done
python3 measurements/ase-phonopy-real/collect.py <dir holding each chip's out/ as arm64/ x86_64/ amd/>
```

Inside the task, after the forces, it is the ordinary phonopy API run: `phonopy.load(phonopy_disp.yaml)`,
then `forces`, `produce_force_constants`, `run_band_structure`, `run_mesh([31,31,31])`,
`run_total_dos(use_tetrahedron_method=True)` and `run_thermal_properties`.

## Results

16 MPI ranks × 1 thread on a `4xlarge` (16 vCPU) of each chip. The same `dft` env runs on all
three (aarch.science arm64; its linux-64 twin on both x86 chips), on the same staged bytes:

| chip | instance | tool wall | billed | peak mem | $/hr | compute $/result | billed $/result |
|---|---|---|---|---|---|---|---|
| Graviton4 | `c8g.4xlarge` | 1,078 s | 1,174 s | 10.8 GiB | 0.6381 | 0.191 | 0.208 |
| Intel (Xeon 6975P-C) | `c8i.4xlarge` | 840 s | 894 s | 15.5 GiB | 0.7497 | 0.175 | 0.186 |
| **AMD (EPYC 9R45)** | `c8a.4xlarge` | **439 s** | **484 s** | 15.5 GiB | 0.8622 | **0.105** | **0.116** |

A *result* is the complete calculation: both GPAW force sets plus the phonopy analysis of all
four. *Tool wall* is GPAW plus phonopy. *Billed* is spawn's task window, wrapper start to stage-out done.

- **Almost all of the cost is the DFT forces, not phonopy.** The 64-atom SCF (42 iterations) is
  1,009 / 769 / 398 s. The 16-atom one is 32 / 31 / 18 s. Phonopy's whole analysis of four force
  sets is 33 / 34 / 18 s. Size a phonon study on its force calculator.
- **The AMD lead is in GPAW's dense linear algebra.** Davidson's own time (the plane-wave
  subspace work in BLAS) is 368 s on Graviton4 and 109 s on AMD (3.4x). Its matrix step is 121 s
  against 34 s. The x86 twin's OpenBLAS reports `Cooperlake` on both x86 chips and `neoversev2`
  on Graviton4. Intel's 16 vCPUs are 8 cores × 2 threads (`Thread(s) per core: 2`), while AMD's
  and Graviton4's are 16 full cores. That rental is like-for-like; a per-core comparison it is not.
- **Peak memory** is cgroup `memory.peak`. GPAW's own summed `ru_maxrss` is 11.8–12.1 GiB on all
  three. The x86 cgroup figure is higher because that route installs the env into the container
  at start, and the cgroup counts the page cache.
- **Boot is small here:** 45–96 s of boot, image pull or env install, and staging.
- n = 1 per chip. The SCF is deterministic: the same 42 and 30 iterations on every chip, and
  identical converged numbers (below). The timings are therefore of one computation per chip.

## Why this size

This is the setup phonopy's own documentation uses for Si, and the one a phonon study of a
two-atom crystal actually runs: a 2×2×2 conventional supercell (64 atoms), PBE, a 4×4×4 k-mesh,
one symmetry-reduced displacement. The 16-atom primitive supercell is the cheaper common choice,
and running both gives a supercell-convergence check for free. A crystal with more atoms or
lower symmetry needs more displacements. Each is another SCF of this size, so cost scales by the
displacement count at the per-SCF rates above.

## What held at scale, on all three chips

| assertion (per force set unless stated) | bound | Graviton4 | Intel | AMD |
|---|---|---|---|---|
| space group of both committed cells (spglib) | `Fd-3m (227)` | yes | yes | yes |
| symmetry-reduced displacements, committed and regenerated | exactly 1 | 1 | 1 | 1 |
| GPAW MPI ranks, from `gpaw.mpi.world.size` | == 16 launched | 16 | 16 | 16 |
| GPAW net force / max force (Newton's third law) | < 1e-2 | ≤ 1.3e-6 | ≤ 1.3e-6 | ≤ 1.3e-6 |
| **acoustic sum rule**: Γ acoustic modes, *unsymmetrised* force constants | < 1e-2 THz | ≤ 3.4e-7 | ≤ 3.9e-7 | ≤ 3.9e-7 |
| six symmetry-equivalent X points give identical spectra | < 1e-8 THz | ≤ 1.4e-14 | ≤ 1.6e-14 | ≤ 1.6e-14 |
| tetrahedron DOS integrates to 3N = 6 modes | ± 1e-2 | 5.9988–6.0014 | same | same |
| `F(T=0)` equals ½Σhν over the same mesh | 1e-9 relative | equal to 10 figures | same | same |
| S = −dF/dT at 300 K, central-difference error ratio h=20→10 K | 4 (O(h²)), 3.5–4.5 | 3.998 | 3.998 | 3.998 |
| no imaginary modes along Γ–X–U \| K–Γ–L | > −0.05 THz | yes | yes | yes |
| **every converged number vs Graviton4** (`collect.py`) | E < 1e-4 eV, F < 1e-4 eV/Å, ν < 1e-3 THz | — | ≤ 9e-9 eV, 5e-9 eV/Å, 6e-6 THz | ≤ 3.7e-7 eV, 1.5e-7 eV/Å, 1.0e-4 THz |

The sum rule is checked before phonopy symmetrises the force constants. Afterwards it holds by
construction, so it would test nothing. The O(h²) ratio tests that phonopy's entropy really is
the temperature derivative of its free energy, which no single-temperature band could. The
cross-chip bounds are 10x GPAW's own convergence criteria (forces 1e-5 eV/Å, density 1e-6), and
were fixed before the x86 runs.

## Fixed point or path

Every quantity here is a fixed point (a converged SCF, then deterministic linear algebra), and
all of them agree across chips.

- **Phonopy on the committed QE and VASP forces is identical on all three chips** to every printed
  digit: frequencies to 1e-6 THz and `F(0)` to 10 figures. Only the analytically zero residuals
  move, which are rounding noise: Γ acoustic 2.7e-7 to 3.9e-7 THz, X-star spread ~1e-14.
- **GPAW: Intel lands on Graviton4's numbers to 9e-9 eV. AMD lands 3.7e-7 eV away** (64-atom
  total energy −345.57468611 vs …648), and its frequencies shift by up to 1e-4 THz. That is
  inside the SCF tolerance, so it is a different point on the same convergence plateau, not a
  different answer. Both x86 chips run the same binary and report the same OpenBLAS kernel, yet
  AMD sits further from Graviton4 than Intel does. The source is chip-dependent code selection
  somewhere below GPAW, and this measurement does not identify which library.

## Cross-code: GPAW against the committed QE and VASP forces (reported)

All frequencies in THz. Each GPAW force set ran on the reference's identical displaced supercell
**with that reference's own k-mesh**: QE's `4 4 4 1 1 1` shifted mesh, and the Γ-centred 4×4×4
in `vasprun.xml`'s `kpointlist`.

| | Γ optical | X TA | L TA | worst mode vs its reference |
|---|---|---|---|---|
| QE (64 atoms, upstream) | 15.095 | 4.519 | 3.503 | — |
| **GPAW, 64 atoms** | 15.163 | 4.347 | 3.309 | 5.5% (L TA) |
| VASP (16 atoms, upstream) | 15.111 | 4.389 | 3.333 | — |
| **GPAW, 16 atoms** | 15.174 | 4.330 | 3.294 | 1.35% |
| experiment (Raman, 520.7 cm⁻¹) | 15.61 | | | |

- **GPAW's two supercells agree to 0.45%** at Γ/X/L. These are different cells, displacements
  and k-meshes, and commensurate points are exact in both, so this is the supercell-convergence
  check.
- **The Γ optical mode agrees across all three codes to 0.45%**, and all sit ~3% below the Raman
  value, as PBE does for Si.
- The soft transverse-acoustic modes spread most. QE's sit 3–5% above both GPAW and VASP, which
  agree with each other to ~1%. That residue is the pseudopotential / PAW dataset, which no
  setting matches across codes, so it is reported, not asserted. A bound written before the run
  ("GPAW no further from each reference than QE is from VASP", 5.1%) failed at 5.5% on the
  64-atom cell. The bound measured where the QE reference sits, not whether GPAW is right.
- **Match the k-mesh, or the comparison is wrong.** A shifted Monkhorst-Pack mesh on the 16-atom
  FCC supercell keeps 2 of its symmetries. GPAW then puts ~2e-3 eV/Å of force on atoms whose VASP
  force is ~1e-5, and the Γ optical mode lands at 16.50 THz, 9% off. With VASP's own Γ-centred
  mesh it is 15.17. Every phonopy identity above passed with the wrong mesh. The like-with-like
  comparison is what caught it ([cross-checks](../../practices/cross-checks.md)).

## Pins

| | |
|---|---|
| inputs | `phonopy/phonopy` @ `v4.4.0` (the tag matching the packaged phonopy): `example/Si-QE/{phonopy_disp.yaml, supercell-001.out}`, `example/Si/{phonopy_disp.yaml, vasprun.xml}`, sha256 in `inputs.sha256`, re-checked in-task |
| image | arm64 `quay.io/aarchsci/dft@sha256:0740fab9…` (the recipe's pin); x86 the env's linux-64 twin `envs-x86_64/dft-5cb0d94e928d` |
| versions (all chips) | GPAW 25.7.0, phonopy 4.4.0, ASE 3.29.0, spglib 2.7.0, numpy 2.5.2 |
| scripts | `run.sh`, `forces.py`, `analyse.py`, `inputs.sha256`, each pinned by sha256 in the spec (`mkspec.py` writes it); `collect.py` runs locally on the three outputs |

**Data tier: a versioned release.** Phonopy's examples ship in its repository at each tag, with
the reference codes' force outputs beside them. No RODA or DOI-archived DFT phonon input exists
for Si. GPAW's PAW setups come from the image. Raw per-chip output, including GPAW's logs and
force tables, is in `results/`, and the cross-chip comparison is in `results/crosschip.txt`.
