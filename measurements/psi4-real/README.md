# Psi4 SAPT0 on the whole S66 set, reproducing DESRES's published components: Graviton4, Intel and AMD

> **Psi4 reproduces all 924 published numbers in DESRES's DESS66 on all three chips: 10 SAPT0/aVTZ
> columns and 4 counterpoise-corrected DF-HF and DF-MP2/aVTZ columns, for each of the 66 dimers.
> The worst miss is 1.24e-3 kcal/mol, and the median is about 2e-5.** Running Psi4 with its own
> SAPT0 defaults misses the same columns by 0.33–2.55 kcal/mol. AMD is fastest and cheapest ($1.42
> per set billed), then Intel ($1.50), then Graviton4 ($1.94).

Workload: **DESS66**, the S66 noncovalent benchmark as DESRES computed it for the DES370K paper
(Donchev et al., *Sci. Data* 8, 55 (2021); Zenodo
[10.5281/zenodo.5676284](https://doi.org/10.5281/zenodo.5676284) v1.0.0). It has 66 dimers of 6 to 34
atoms: hydrogen-bonded, dispersion-bound and mixed. DESRES ran its SAPT0, HF and MP2 columns in Psi4
through its own plugin, [sns-mp2](https://github.com/DEShawResearch/sns-mp2). `psi4_s66.py` makes
the same Psi4 calls that plugin makes, on every dimer, and compares the result column by column.

## Run it

```sh
export AWS_PROFILE=aws B=<your bucket>
# once: DESS66 from Zenodo, fetched on AWS and checked against Zenodo's md5 before it is kept
spawn task run --wait --spec "$(python3 scripts/spec_arch.py psi4 arm64 "$B" fetch measurements/psi4-real/00-fetch.task.json)"
bash measurements/psi4-real/stage-inputs.sh "$B"
# the x86 specs are written for x86 already (mkspec.py), so the `arm64` pass only fills the bucket and a nonce
for s in real real-x86_64 real-amd; do
  spawn task run --wait --spec "$(python3 scripts/spec_arch.py psi4 arm64 "$B" "$(date +%s)" measurements/psi4-real/$s.task.json)"
done
python3 measurements/psi4-real/collect.py <dir holding arm64/ x86_64/ amd/ psi4.jsonl>
```

Per dimer, the calculation is:

```python
psi4.set_options({"basis": "aug-cc-pvtz", "df_basis_scf": "aug-cc-pvtz-jkfit",
                  "df_basis_sapt": "aug-cc-pvtz-ri", "df_basis_elst": "aug-cc-pvtz-ri",
                  "df_basis_mp2": "aug-cc-pvtz-ri", "freeze_core": True,
                  "e_convergence": 1e-10, "d_convergence": 1e-10, "sapt__cphf_r_convergence": 1e-10})
psi4.energy("sapt0", sapt_basis="monomer")      # DESRES's SAPT: monomers in their own basis
for m in (dimer, A_with_ghost_B, B_with_ghost_A):
    psi4.energy("mp2", molecule=m)               # CP-corrected DF-HF and DF-MP2
```

Each dimer runs in its own Python process. The checks run inside the task, and any failure fails it.
`mkspec.py` writes the specs and pins every staged script by sha256.

## Results

`r8g/r8i/r8a.4xlarge` (16 vCPU, 128 GiB). Four workers, each one Psi4 process with 4 threads and 28 GB.
Same staged bytes, same thread count:

| chip | instance | cores × threads | tool wall | billed | peak RSS (process / box) | $/hr | compute $/result | billed $/result |
|---|---|---|---|---|---|---|---|---|
| Graviton4 | `r8g.4xlarge` | 16 × 1 | 7253 s | 7426 s | 18.2 / 96.1 GiB | 0.9426 | 1.899 | 1.944 |
| Xeon 6975P-C | `r8i.4xlarge` | 8 × 2 | 4683 s | 4845 s | 18.3 / 92.6 GiB | 1.1114 | 1.446 | 1.496 |
| **EPYC 9R45** | `r8a.4xlarge` | 16 × 1 | **3840 s** | **4002 s** | 18.3 / 93.3 GiB | 1.2781 | **1.363** | **1.421** |

A *result* is the whole set, all 66 dimers with every check passed: $0.022 per dimer billed on AMD.
The set also includes the 12 control jobs below. *Tool wall* is `psi4_s66.py`. *Billed* is EC2 launch
to termination, at spawn's printed price. *Process* is the largest single Psi4 `ru_maxrss`. *Box* is
the cgroup's `memory.peak`, which also counts page cache.

- **AMD is 1.89× faster than Graviton4, and 27% cheaper per set billed.** Intel is 1.55× faster and
  23% cheaper, on 8 physical cores. SAPT0 and the MP2 calls split the CPU about evenly on every chip
  (51–54% SAPT).
- **This is not the same build on each chip.** conda-forge's linux-64 psi4 1.11 depends on MKL, and the
  linux-aarch64 build links OpenBLAS (`neoversev2`). So the x86 numbers are MKL on x86 against
  OpenBLAS on Graviton4. That is what conda-forge's Psi4 is on each chip, not a chip-only comparison.
- **Threads were chosen from a probe, not assumed** (`results/probe/`). On a 6-atom dimer, 16 threads
  took 40 s and 1 thread 8 s. On the median dimer, 4 threads were 2.6× faster than 1. A first try at
  16 single-threaded workers with 6 GB each pushed the DF integrals out of core. It filled the 100 GB
  root disk, and 24 of 78 jobs died with `PSIO_ERROR`. The box averaged 6.3 busy cores out of 16
  (`results/probe/r1-*`). With 4 workers × 28 GB the integrals stay in memory, and the box averages
  12.0–13.6 busy cores.
- **These timings are not compute cost in general.** Boot, Docker, the image pull and staging take
  2.7–2.9 minutes of each bill. n = 1 per chip. Every column is a converged fixed point, and the three chips agree to
  ≤ 2.5e-5 kcal/mol (below).

## Why this size

S66 is the standard set for benchmarking a noncovalent method. DESS66 is all of it, run at the
published level of theory on DESRES's published geometries. Per dimer this is the full calculation
that dataset made, not a cut-down one. Its 8-point dissociation curves, DESS66x8 (528 geometries, in
the same Zenodo record), would cost about 8× as much at the same per-geometry price. The aVQZ columns
(`qz_*`, `cbs_*`) and DESRES's CCSD(T) columns were not run.

## What held at scale, on all three chips

| assertion | bound | observed (worst chip) |
|---|---|---|
| DESS66.zip matches Zenodo's md5 and its pinned sha256 | exact | yes |
| 66 rows, 66 distinct `geom_id` and system names | exact | yes |
| every calculation finishes (66 + 6 rotated + 6 controls) | 78 of 78 | 78 |
| published: `sapt_all` == sum of its 8 terms; `tz_MP2_all` == HF + os + ss | ≤ 6e-5 (print rounding) | 2.0e-5, 1.0e-5 |
| Psi4: SAPT0 total == sum of its 8 terms; MP2 interaction == HF + os + ss | ≤ 1e-9 | 7.1e-15, 6.7e-11 |
| **every SAPT0 column == DESS66, every dimer** (es, ex, exs2, ind, exind, disp, exdisp os/ss, δHF, total) | ≤ 1e-2 kcal/mol | 5.31e-4 (exch10, S66 row 19) |
| **every CP DF-HF/MP2 aVTZ column == DESS66, every dimer** (HF, MP2 os, ss, total) | ≤ 1e-2 kcal/mol | 1.24e-3 (MP2 ss, row 59) |
| SAPT0 terms unchanged under a rigid rotation + translation (6 dimers) | ≤ 1e-5 | 4.16e-7 |
| **control: Psi4's default SAPT0 misses DESS66** (6 dimers) | > 1e-2 on some column | ≥ 0.332 (ind) |

All energies are in kcal/mol. The median miss on each column is 3.5e-6 to 3.8e-5.

**The bound is set by DESRES's convergence, not ours.** sns-mp2 calls Psi4's `scf_helper` directly,
so DESRES's SCFs ran at the SCF module's default, 1e-6. Ours run at 1e-10. The probe converged the
same three dimers at both settings. The columns moved by up to 2.35e-3 kcal/mol (MP2 total), and by
2.8e-4 for SAPT0 (`results/probe/compare.txt`). So DESRES's own values carry that much convergence
noise. The bound of 1e-2 is 4× the largest shift measured, and 8× the largest miss observed. It sits
33× below the smallest control miss.

**The control is what shows the bound can fail.** Psi4's defaults for `energy("sapt0")` put the
monomers in the dimer-centred basis, and fit electrostatics with the JK basis. DESRES used monomer-
centred bases and the RI fit. With Psi4's defaults, induction moves by 0.33–2.55 kcal/mol and
exchange-induction by 0.25–1.78 on the six smallest dimers, while the SAPT0 total moves only
0.006–0.17 (`check.txt`). The control changes both settings at once, so it does not show which one
moves which term. A user who compares their own default Psi4 SAPT0 components with DES370K-family data
will see differences of this size. They come from the basis choice, not an error. One consequence
of the monomer basis: DESS66's `sapt_delta_HF` sits on a **non**-counterpoise HF interaction energy,
while its `tz_HF` is counterpoise-corrected. The two HF numbers differ by the basis-set superposition
error (BSSE).

Copying sns-mp2's options into Psi4 1.11 verbatim raises. 1.10 renamed SAPT `D_CONVERGENCE` to
`CPHF_R_CONVERGENCE` and removed SAPT `E_CONVERGENCE`.

## Fixed point or path

- **Every column is a converged fixed point, and the three chips agree far inside the bound.**
  Intel against Graviton4: SAPT0 total ≤ 1.29e-5 kcal/mol, CP HF ≤ 4.4e-6, MP2 correlation ≤ 2.5e-7.
  AMD against Graviton4: ≤ 2.13e-5, 5.7e-6, 3.7e-7. The largest spread is in the HF-derived terms
  (`δHF`, `tz_HF`). These are differences of total energies of −300 to −660 Eh, and the worst,
  2.5e-5 kcal/mol, is 4e-8 Eh, below 1e-10 relative. Electrostatics agree to 8e-6. The exchange,
  induction and dispersion terms agree to 1e-7–1e-9.
- **No column is bit-identical across any pair of chips, Intel and AMD included** (`results/crosschip.txt`).
  Both run the same x86 image, and differ by up to 2.5e-5. A thread-count change on one Graviton4
  box moved the same columns by up to 8.7e-7 (probe), so the multithreaded DF and MKL reductions are
  not bit-reproducible at this level. Whether two runs on one chip agree bit for bit was not measured.
- Every chip misses DESS66 by the same amount to three digits (worst 5.31e-4 / 1.24e-3 on all three).
  The miss is DESRES's convergence, not the chip.

## x86: Psi4's own image, not the recipe's env twin

The [psi4 recipe](../../recipes/psi4/README.md) runs on x86 through the `dft` env's linux-64 twin
(`envs-x86_64/dft-c103c83e2eb2`). That twin's `psi4 1.11 py314hb58b17a_1` does not import:
`undefined symbol: _ZN7einsums13random_engineE`. That was measured on Intel on 2026-10-17 (recipe
page) and on AMD in [pyscf-real](../pyscf-real/README.md). Re-locking the twin would be a pin swap,
and it was not done. The x86 legs here instead run **psi4's own upstream image**, `psi4/psi4:1.11.0`
by its `linux/amd64` digest. It has the same Psi4 version, built `py312h42f99be_0`, with `einsums
1.1.3`, and it imports. It differs from the arm64 env in Python (3.12 vs 3.14), numpy (2.5.0 vs
2.5.2) and OpenBLAS (0.3.33 vs 0.3.34). It also carries MKL, which linux-64 psi4 depends on. No
assertion here moved between the two.

## What this does not cover

- DESS66x8, the aVQZ and CBS columns, and DESRES's CCSD(T) columns.
- How good SAPT0/aVTZ is. Against DESS66's silver-standard reference, the SAPT0/aVTZ total has an
  MAE of 1.12 kcal/mol and overbinds every dimer. CP DF-MP2/aVTZ has an MAE of 0.49 kcal/mol. These
  are reported, not asserted. Psi4's manual recommends SAPT0 in jun-cc-pVDZ and advises against
  large bases like aug-cc-pVTZ (Parker et al., *JCP* 140, 094106 (2014)). In sns-mp2, the aVTZ
  SAPT0 terms are inputs to the SNS-MP2 model.

## Pins

| | |
|---|---|
| input | DESS66, Zenodo [10.5281/zenodo.5676284](https://doi.org/10.5281/zenodo.5676284) v1.0.0: `DESS66.zip` md5 `48930e4d…cc16b` (Zenodo), sha256 `99e23bbc…bfc8f65e` (80,074 bytes). `00-fetch.task.json` downloads it and `DESS66x8.zip` on AWS, and checks each against Zenodo's md5 before keeping it. `dess66.sha256` sits beside them in the bucket, and every run re-checks the zip it reads. |
| arm64 image | the psi4 recipe's `quay.io/aarchsci/dft@sha256:b3564993…` (`psi4 1.11 py314h2e14d3a_1`, OpenBLAS 0.3.34) |
| x86 image | `docker.io/psi4/psi4@sha256:8c4c13f5e4e11b7506d2fd9f76751a7b45f41cb9fbb169ab520904d1d1caacb1` (`1.11.0`, `linux/amd64`; `psi4 1.11 py312h42f99be_0`) |
| harness read | sns-mp2 `master` (2020-12-01): `snsmp2.py`, `wavefunctioncache.py`, `desbasis.json`, `frozencore.py`. For H, C, N and O, DESAVTZ is aug-cc-pVTZ, with aug-cc-pVTZ-JKFIT and -RI fits, and the frozen core is 1 orbital per heavy atom. |
| scripts | `run.sh`, `psi4_s66.py` and `check_s66.py`, pinned by sha256 in each spec (`mkspec.py`). `collect.py` and `probe_compare.py` run locally on the outputs. |

**Data tier: a durable public id.** Zenodo record with a DOI, cited by the paper; no RODA copy.
Per-chip `check.txt`, `host.txt`, `footprint.txt` and `run.log` are in `results/`. The cross-chip
comparison is `results/crosschip.txt`, and the probes are in `results/probe/`. The per-dimer `psi4.jsonl`
and every Psi4 output are in the bucket under `runs*/psi4-real/real/`.
