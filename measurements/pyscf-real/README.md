# PySCF on every QM9 molecule up to six heavy atoms: Graviton4, Intel and AMD

> **PySCF reproduces QM9's published Gaussian 09 B3LYP energies for all 783 molecules to 5.1e-5 Ha
> (median 5.1e-6), and agrees with Psi4 on 783 RHF and MP2 and 175 CCSD(T) energies to 5e-9 Ha.
> Every PySCF check holds on all three chips; Psi4 runs on Graviton4 only, because its x86 build
> does not import.** It only matches once you use Gaussian's 6-31G(2df,p):
> PySCF's library basis of that name is a different basis, and misses by 0.3–8 mHa. AMD is fastest and
> cheapest ($0.51 per stratum billed, against $0.68 on Graviton4 and $0.83 on Intel).

Workload: QM9 (Ramakrishnan et al., *Sci. Data* 1, 140022, 2014), the standard benchmark for
labelling small molecules with DFT. The run reads all 133,885 records and takes **every
characterized molecule with at most six heavy atoms (C, N, O, F): 783 molecules.** That leaves out
13 of the 796 in that range, the ones QM9's `uncharacterized.txt` flags as failing its geometry check.

- **B3LYP/6-31G(2df,p)** at QM9's own geometries, in Gaussian 09's AO space and grid. The energy,
  HOMO, LUMO and dipole are compared with QM9's published values, plus QM9's five atomic reference
  energies (UB3LYP).
- **RHF → MP2 /cc-pVDZ** (exact integrals, frozen core) on all 783, and **CCSD(T)** on the 175 with
  at most five heavy atoms. [Psi4](../../recipes/psi4/README.md) then runs the same calculations on the
  same geometries, as a second task.

## Run it

```sh
B=$(make -s print-bucket)
# once: QM9 from figshare, fetched on AWS and checked against figshare's md5 before it is kept
spawn task run --spec "$(python3 scripts/spec_arch.py pyscf arm64 "$B" fetch measurements/pyscf-real/00-fetch.task.json)" --wait
bash measurements/pyscf-real/stage-inputs.sh "$B"
python3 measurements/pyscf-real/mkspec.py r2 16 6 5      # label, workers, max heavy atoms, CCSD(T) up to
for a in arm64 x86_64 amd; do
  spawn task run --spec "$(python3 scripts/spec_arch.py pyscf $a "$B" r2 measurements/pyscf-real/01-pyscf.task.json)" --wait
done
spawn task run --spec "$(python3 scripts/spec_arch.py psi4 arm64 "$B" r2 measurements/pyscf-real/02-psi4.task.json)" --wait
python3 measurements/pyscf-real/collect.py <dir holding arm64/ x86_64/ amd/ pyscf.jsonl, and arm64/psi4.jsonl>
```

`pyscf_run.py` runs one molecule per worker process, one BLAS thread each, and then runs every PySCF
check below. `psi4_run.py` reads that task's `pyscf.jsonl` from S3 and asserts the agreement. A check
that fails fails the task.

## Results

16 workers on an `m8*.4xlarge` (16 vCPU, 64 GiB) of each chip, the same `comp-chem` env
(aarch.science arm64; its linux-64 twin on both x86 chips), the same staged bytes. Psi4 runs on an
`r8g.4xlarge` (128 GiB), because its conventional MP2 and CC scratch goes in the half-RAM `/tmp`:

| chip | instance | tool wall | billed | peak RSS | $/hr | compute $/result | billed $/result |
|---|---|---|---|---|---|---|---|
| Graviton4 | `m8g.4xlarge` | 3280 s | 3432 s | 2.83 GiB | 0.7181 | 0.654 | 0.685 |
| Intel (Xeon 6975P-C) | `m8i.4xlarge` | 3461 s | 3527 s | 2.83 GiB | 0.8467 | 0.814 | 0.830 |
| **AMD (EPYC 9R45)** | `m8a.4xlarge` | **1830 s** | **1888 s** | 2.83 GiB | 0.9738 | **0.495** | **0.511** |
| Psi4 cross-check, Graviton4 | `r8g.4xlarge` | 1818 s | 1970 s | 2.33 GiB | 0.9426 | 0.476 | 0.516 |

A *result* is the whole stratum, 783 molecules, every check passed: $0.00087 per molecule billed on
Graviton4. *Tool wall* is `pyscf_run.py`. *Billed* is instance launch to stage-out. *Peak RSS* is the
largest single worker (`ru_maxrss`); cgroup `memory.peak`, which counts page cache, was 41.8–44.4 GiB.
Sixteen workers at up to 2.83 GiB each is more than a 32 GiB `c8g.4xlarge` holds, which is why the
box is `m8g`.

- **AMD is 1.79× faster than Graviton4 and 25% cheaper per stratum**, even at a 36% dearer hour.
  Intel's 16 vCPUs are 8 cores × 2 threads (`Thread(s) per core: 2`), and it runs only 5% slower
  than Graviton4's 16 full cores.
- **B3LYP is 72–77% of the CPU.** It is the integral and grid work, where Graviton4 beats Intel
  (37,818 against 42,379 worker-seconds). CCSD(T) is dense BLAS, and there Graviton4 is slowest:
  9,204 worker-seconds against Intel's 6,586 and AMD's 3,426. OpenBLAS picks `neoversev2` on
  Graviton4 and `Cooperlake` on both x86 chips.
- **These timings are not compute cost.** Boot, Docker and the 0.62 GB image pull are 58–152 s of
  each window. On x86 the env twin installs inside the command.
- n = 1 per chip. Every molecular energy asserted below is a converged SCF or CC fixed point and
  agrees across chips to ≤ 1.1e-9 Ha (below), which is what makes one run per chip enough.

## Why this size

Labelling a molecule set with DFT, and checking it against a published one, is what QM9 itself is and
how the datasets that extend it are built. The stratum is every QM9 molecule up to six heavy atoms, not a sample.
Per molecule it is the full-size calculation: the same method and basis at the published geometry.
B3LYP takes 56 s per six-heavy-atom molecule on Graviton4 and grows steeply with size: 147 s at
seven heavy atoms, 275–415 s at nine (probes). All of QM9 is 167× the molecules and, from those
per-size times, about 1,100× this run's B3LYP CPU: roughly $500 on this box. CCSD(T) stops at five
heavy atoms because PySCF's costs 270–580 s per six-heavy-atom molecule (measured), which would have
multiplied the bill about five times without changing what is checked.

## What held at scale, on all three chips

| assertion | bound | observed (worst chip) |
|---|---|---|
| read the whole release | 133,885 records, 3,054 uncharacterized | as bound |
| every SCF converges: B3LYP, RHF | 783 of 783 | 783, 783 |
| every CCSD converges | 175 of 175 | 175 |
| **QM9 atomic references, UB3LYP (H, C, N, O, F)** | ≤ 1e-4 Ha | 1.6e-6 (Graviton4 1.2e-6) |
| **B3LYP energy == QM9 U0 − ZPVE, every molecule** | ≤ 1e-4 Ha | max 5.14e-5, median 5.1e-6 |
| B3LYP HOMO, LUMO == QM9 (printed to 1e-4) | ≤ 1e-4 Ha | 5.82e-5, 6.67e-5 |
| B3LYP dipole == QM9 | ≤ 1e-3 D | 4.11e-4 |
| **control: PySCF's library 6-31G(2df,p) misses QM9** | > 1e-4 Ha on every one of 31 | least 1.49e-3, median 4.12e-3 |
| RHF invariant under a random rotation + translation (31 molecules) | ≤ 1e-8 Ha | 6.9e-12 |
| **PySCF == Psi4, RHF** (783) | ≤ 1e-8 Ha | 2.78e-10, median 1.1e-10 |
| **PySCF == Psi4, MP2 correlation** (783) | ≤ 1e-7 Ha | 5.00e-9 |
| **PySCF == Psi4, CCSD and (T) correlation** (175) | ≤ 1e-7 Ha | 3.24e-9, 1.24e-9 |

**The B3LYP bounds are set by quadrature.** Both codes integrate the same functional in the same AO
space on a (75,302) grid. Two different (75,302) grids, though, as the radial scheme and pruning differ.
A probe measured both errors on 52 molecules. PySCF's (75,302) energy moves by up to 2.1e-5 Ha
against a (150,974) unpruned grid. On the converged grid PySCF is within 2.8e-5 of QM9, which is
Gaussian's own grid error plus QM9's 1e-6 print rounding (`results/probe/p2-grid.tsv`). The energy bound sits
above that scale and below the smallest library-basis error seen (3.3e-4, NH₃). The orbital
bound is QM9's print rounding (5e-5) plus the same quadrature. A first guess of 2e-5 failed 17 of 255
probe molecules, the worst at 5.9e-5, within the sum of the two measured quadrature errors.

**The Psi4 bounds are set by convergence.** Both codes use exact integrals (Psi4 `scf_type pk`,
`mp2_type conv`, `cc_type conv`) and the same frozen core. They converge RHF to 1e-11 Ha. RHF is
variational, so what is left is second order, and the bound is 1e-8. Correlation energies are first order in
the residual orbital and amplitude errors (PySCF gradient 1e-8, CC residual 1e-7; Psi4 density 1e-9,
residual 1e-8), so their bound is 1e-7. Observed headroom is 20–80×. The recipe's H₂ check agreed to
3e-7 with a value printed to six decimals. Here two codes computed every value, and they agree to 3e-10.

## The basis that has the same name

QM9 was computed with Gaussian 09's `6-31G(2df,p)`. **PySCF's `6-31g(2df,p)` and the Basis Set
Exchange's are not that basis.** They take carbon's 2d exponents from 6-311G(2df), 1.252/0.313.
Gaussian builds them as 2× and ½× the 6-31G\* d exponent, 1.6/0.4, on every first-row atom. Gaussian
also uses Cartesian d with spherical f (6D/7F), which PySCF cannot select per shell. `qm9.py` builds
both. It runs the SCF inside span(6D/7F) by handing PySCF's `check_linear_dependency` an orthonormal
basis of that subspace, and PySCF diagonalises and takes DIIS errors in it.

`results/probe/p1-library-basis.tsv` has the library basis: 3.3e-4 to 8.0e-3 Ha off QM9 (up to 5
kcal/mol), and dipoles up to 0.06 D off, in any AO space and on any grid. It is not the functional:
PySCF 2.14's `b3lyp` and `b3lypg` both parse to libxc's `HYB_GGA_XC_B3LYP` and give the same
energies. With Gaussian's exponents and 6D/7F, all five atomic references land within 1.6e-6.
A dataset built with PySCF's basis of the same name would be shifted against QM9 by several mHa,
and nothing would fail.

## Fixed point or path

- **Every molecular energy is a fixed point.** B3LYP against Graviton4: Intel within 2.05e-12 Ha and AMD
  within 2.39e-12 (54 and 37 of 783 bit-identical). RHF within 1.15e-11, MP2 within 9.4e-11,
  CCSD within 1.13e-9 and (T) within 9.5e-11. The CCSD spread is its amplitude convergence threshold.
- **Intel and AMD are bit-identical on RHF and MP2** (783 of 783, and all 31 rotated RHF). Both run
  the same linux-64 twin, and OpenBLAS picks `Cooperlake` on both.
- **In B3LYP, Intel alone takes a different SCF path.** Its HOMO and LUMO differ from both other
  chips by up to 1.65e-7 Ha and its dipoles by 4.6e-7 D, while AMD's orbital energies agree with
  Graviton4's to 2.2e-13. Orbital energies are converged only to the orbital-gradient threshold
  (√conv_tol = 1e-5), so this is a path inside the tolerance. Energies stay within 2e-12. Same build
  and same BLAS kernel as AMD, so it is not the OpenBLAS dispatch. What else differs on that chip
  was not isolated.
- **The open-shell atoms are paths.** C and O (triplets) and F (doublet) have partly filled,
  degenerate p shells, and UKS lands on a slightly different symmetry-broken solution per chip. O
  spans 6.1e-7 Ha across the three (−75.0645780 on Graviton4, −75.0645774 on AMD). H and N
  (spherical) are bit-identical. All stay within 1.6e-6 of QM9.
- Every chip's PySCF agrees with Graviton4's Psi4 to the same 2.78e-10 / 5.00e-9 / 3.24e-9 / 1.24e-9.

## What this does not cover

- **Psi4 on x86.** The `dft` env's linux-64 twin pins `psi4 1.11 py314hb58b17a_1` with `einsums
  1.1.6`, and that build was linked against 1.1.5's ABI. `import psi4` fails with `undefined symbol:
  _ZN7einsums13random_engineE` on AMD (`results/amd/psi4-does-not-import/`), as the
  [psi4 recipe](../../recipes/psi4/README.md) records. conda-forge's later builds (`_3`, 2026-10-12)
  depend on einsums ≥ 1.1.6. The cross-code check is therefore Graviton4's. The x86 PySCF energies
  equal Graviton4's to ≤ 1.1e-9, so they meet the same Psi4 bounds.
- Seven to nine heavy atoms (132,089 of QM9's records), QM9's polarizability, ⟨R²⟩ and
  thermochemistry, and any open-shell molecule beyond the five atoms.

## Pins

| | |
|---|---|
| input | QM9, figshare collection [10.6084/m9.figshare.c.978904](https://doi.org/10.6084/m9.figshare.c.978904) (v5): `dsgdb9nsd.xyz.tar.bz2` sha256 `3a63848a…aa8360c` (86,144,227 bytes), `uncharacterized.txt` `3aa5115d…02e24`, plus `atomref.txt`, `validation.txt`, `readme.txt` and the C₇H₁₀O₂ set. `00-fetch.task.json` downloads them on AWS and checks each against the md5 figshare publishes before keeping it. `qm9.sha256` sits beside them in the bucket, and every run re-checks the two it reads. |
| images | arm64 PySCF: the recipe's `quay.io/aarchsci/comp-chem@sha256:b5c1b1ec…`. Psi4: the [psi4 recipe](../../recipes/psi4/README.md)'s `quay.io/aarchsci/dft@sha256:b3564993…`. x86: the comp-chem env's linux-64 twin `envs-x86_64/comp-chem-1243f25ebfec`. |
| versions | pyscf 2.14.0 and numpy 2.5.4 on all chips; psi4 reports 1.11 (Graviton4) |
| scripts | `run.sh`, `qm9.py`, `pyscf_run.py` and `psi4_run.py`, each pinned by sha256 in the spec (`mkspec.py` writes it). `collect.py` runs locally on the outputs. |

**Data tier: a durable public id.** QM9 is a versioned figshare collection with a DOI, which the paper
cites. There is no RODA copy. Per-chip `check.txt`, `crosscheck.txt`, `host.txt`, `footprint.txt` and
`atoms.json` are in `results/`, the cross-chip comparison is `results/crosschip.txt`, and the probe
tables are in `results/probe/`. The per-molecule `pyscf.jsonl` and `psi4.jsonl` are in the bucket
under `runs*/pyscf-real/r2/`.
