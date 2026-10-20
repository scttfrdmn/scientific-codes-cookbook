# xtb on all of GMTKN55: Graviton4, Intel and AMD

> **GFN2-xTB energies are right on all three chips: the whole of GMTKN55 (2,462 structures, 1,505
> reactions) agrees to 1e-12 Eh across chips and reproduces 39 of 42 published GFN2-xTB subset errors
> to the printed digit. The analytic gradient is not right anywhere.** On Graviton4 it is wrong on every
> one of the 2,354 multi-atom structures (median 8.6× too large). On Intel and AMD it is wrong on 133 of
> them (5.6%), and xtb's own upstream release binary is wrong on the same ones. The whole benchmark
> takes 14–24 s, so billing is mostly boot: $0.025–0.034 per run.

Workload: **GMTKN55** (Goerigk et al., *PCCP* 19, 32184 (2017), doi:10.1039/C7CP04913G), the standard
benchmark for main-group thermochemistry, kinetics and noncovalent interactions. All 55 subsets, every
structure, every reaction, from the Grimme group's own repository at the commit that preserves the
publication. Each structure gets:

- a **GFN2-xTB single point** at xtb's defaults (`--etemp 300 --acc 1.0`, passed explicitly) for the
  benchmark energies;
- the **analytic gradient**, at `--acc 0.0001`;
- **four more single points** along a seeded random direction in 3N space, so a 5-point finite difference
  of xtb's own energies tests the gradient it prints.

## Run it

```sh
B=$(make -s print-bucket)
# once: GMTKN55 from GitHub at commit 8d485b37, fetched on AWS and sha256'd beside it in the bucket
spawn task run --spec "$(python3 scripts/spec_arch.py xtb arm64 "$B" fetch measurements/xtb-real/00-fetch.task.json)" --wait
bash measurements/xtb-real/stage-inputs.sh "$B"
python3 measurements/xtb-real/mkspec.py r2 16 c8g            # label, workers, family
for a in arm64 x86_64 amd; do
  spawn task run --spec "$(python3 scripts/spec_arch.py xtb $a "$B" r2 measurements/xtb-real/01-gmtkn55.task.json)" --wait
done
python3 measurements/xtb-real/collect.py <dir holding arm64/ x86_64/ amd/, each with xtb.jsonl.gz>
```

`xtb_run.py` runs one xtb process per worker with one thread each, then runs every check below. A check
that fails fails the task, **so all three runs exit 1, on the gradient.** That is the result, not a
bug in the harness. `diag.sh` (in `diag-*.task.json`) is the follow-up that sorts out which builds are wrong; `repro.sh` (`repro-x86_64.task.json`) is the clean-room reproduction with upstream's binary.

## Results

16 workers on a `c8*.4xlarge` (16 vCPU, 32 GiB) of each chip. The recipe's `comp-chem` image runs on
Graviton4, and its linux-64 twin on both x86 chips. Same staged bytes:

| chip | instance | tool wall | billed | peak RSS | $/hr | compute $/result | billed $/result |
|---|---|---|---|---|---|---|---|
| Graviton4 | `c8g.4xlarge` | 19 s | 169 s | 32 MiB | 0.6381 | 0.0034 | 0.0300 |
| Intel (Xeon 6975P-C) | `c8i.4xlarge` | 24 s | 165 s | 34 MiB | 0.7497 | 0.0050 | 0.0344 |
| **AMD (EPYC 9R45)** | `c8a.4xlarge` | **14 s** | **105 s** | 35 MiB | 0.8622 | **0.0034** | **0.0251** |

A *result* is the whole of GMTKN55 with every check run: 14,340 xtb processes. *Tool wall* is
`run.sh` from the first xtb process to the last check. *Billed* runs from instance launch to terminate.
*Peak RSS* is the largest single xtb process (`ru_maxrss`, C60ISO). cgroup `memory.peak` is
0.59 GiB on Graviton4 and 3.3 GiB on x86, where the env twin installs inside the task.

- **AMD is 1.4× faster than Graviton4 and 1.7× faster than Intel.** Intel's 16 vCPUs are 8 cores × 2
  threads (`Thread(s) per core: 2`); the other two are 16 full cores.
- **These timings are not compute cost.** Boot, Docker, the image pull and (on x86) the env install are
  85–89% of each billed window. At 14–24 s the benchmark is the cheap part.
- n = 1 per chip. Every energy is a converged SCC fixed point and agrees across chips to 1e-12 Eh
  (below), which is what makes one run per chip enough.

## Why this size

The size is the dataset. Benchmarking a method on GMTKN55 means running all of it, and that is what the
g-xTB and OrbNet Denali papers report, so a subset would understate the job. It is fast because GFN2 is
fast, which is the point of using it. The finite-difference check multiplies the work by five and still
finishes in under half a minute. Larger single systems (proteins, MOFs) would test throughput, not
correctness. GMTKN55 already runs from single atoms to 81 atoms, with elements from H to Bi.

## What held at scale, on all three chips

| assertion | bound | observed (every chip) |
|---|---|---|
| whole release read | 2,462 structures; 1,505 reactions in 55 subsets | as bound |
| SCC converges at xtb's defaults | all but the 4 named | 2,458; `CARBHB12/1N`, `G21IP/b+`, `be+`, `c+` exit non-zero with xtb's own "did not converge" |
| **per-subset MAE == Table IV of arXiv:2107.00299v2 (GFN2-xTB column)** | at its printed 0.01 kcal/mol, every subset except the 3 named | **39 of 42 identical**; CARBHB12 1.14 vs 1.79, NBPRC 10.53 vs 10.51, WATER27 3.15 vs 3.05 |
| translational invariance, Σ g = 0 | < 1e-8 | 1.3e-14 |
| finite-difference truncation, central(2h)/central(h) error | reported (O(h²) predicts 4) | 4.00 |
| **analytic gradient == 5-point FD of xtb's own energy** | \|Δ\| ≤ 1e-7 + 1e-5 \|FD\| | **FAIL** on all three: Graviton4 2,354 of 2,354, Intel 133, AMD 133 |
| **rotational invariance, Σ r × g = 0** | ≤ 1e-7 Eh | **FAIL**: Graviton4 1,552 of 2,458, Intel 136, AMD 136 |

**The published comparison is exact where it can be.** Table IV prints GFN2-xTB's MAE against GMTKN55's
references for the 42 subsets that contain only singlets. 39 come back to the printed digit. The other
three are named in the assertion. CARBHB12 averages 11 of 12 reactions, because `1N` does not converge
here. WATER27 and NBPRC are not a convergence effect: `--acc 0.0001` gives the same MAE to 1e-4. The
paper does not give its xtb version, so a difference between releases is not ruled out. Table IV's
WTMAD-2 of 18.89 is reported, not asserted. GMTKN55's own formula applied to *the table's own MAEs*
gives 20.17 (20.11 here), so the paper weighted the subsets some other way.

**The FD bound is set by the FD's own error, not by the failures.** The stencil is h = 1e-3 Bohr at
`--acc 0.0001`. Its truncation is O(h⁴), and the measured central-difference ratio of 4.00 shows it
behaving. On x86, 2,152 of the 2,354 structures agree with their FD to ≤ 1e-9 Eh/Bohr absolute
(median 8.3e-8 relative). That is the FD's noise floor. The bound sits 100× above it. The rotation check needs no FD at all. GFN2's energy is invariant under
rotation, so its true gradient carries zero torque. The FD derivatives confirm the invariance across
chips to 7.5e-10, and the correct gradients reach 1e-10 or below.

## The gradient: what is wrong where

Measured on the same structures, with the same script and fresh conda-forge solves (`results/diag/`).
`diag.py` compares the full 3N gradient with a 5-point finite difference of the same build's energies:

| build | Graviton4 | Intel |
|---|---|---|
| conda-forge xtb 6.7.1 build 5 (what `comp-chem` ships: dftd4 4.2, tblite 0.6, multicharge 0.5) | wrong on 9 of 9 | wrong on 4 of 9 |
| conda-forge xtb 6.7.1 build 4 (dftd4 3.7, tblite 0.4, multicharge 0.3) | identical to build 5 | identical to build 5 |
| build 5 with multicharge 0.5.0 build 1 ([xtb#1422](https://github.com/grimme-lab/xtb/issues/1422)'s ABI fix) | identical | identical |
| conda-forge xtb 6.6.1 | — | identical to 6.7.1 |
| **upstream's own release binary**, `xtb-6.7.1-linux-x86_64.tar.xz` (sha256 `62a8d187…`) | — | **identical wrong 4 of 9** |
| **tblite 0.4.0's GFN2**, its CLI or `xtb --tblite` | **right on 9 of 9** (≤ 2.2e-7 rel) | **right on 9 of 9** |

So there are **two separate faults**:

1. **Graviton4: every structure.** Every linux-aarch64 build is affected; feedstock#50 already records
   seven of them as bit-identical. Here it is at scale: wrong on 2,354 of 2,354, `|g_arm64| / |g_x86|`
   median 8.6 (p10 2.9, p90 20.1, max 408). The energy is right, though. Graviton4's FD derivative
   agrees with x86's to 7.5e-10, and with x86's *analytic* gradient to 1.2e-7 wherever x86 passes. A
   researcher on ARM can get correct forces from their own energies; they cannot get them from `--grad`.
2. **x86: 133 structures, in xtb's own GFN2 gradient code.** Intel and AMD fail the identical 133, with
   bit-identical gradients. Upstream's ifort-built release does too, so this is not conda-forge. It
   concentrates in PNICO23 (17/69), YBDE18 (14/29), BH76 (13/80), HEAVY28 (13/38), BHPERI (12/61),
   IL16 (12/42), PX13 (12/29), BUT14DIOL (10/65) and ACONF (8/18). By element it is worst with Te
   (6/11), Sb (4/10), Bi, Pb and P. Water, which the recipe's unit-test check uses, is not among them.
   That is why the recipe passed on x86. Butane is: `ACONF/B_G` has a directional derivative of
   −0.00351 analytic against −0.00164 by FD, and a torque of 0.028 Eh. tblite's reimplementation of
   GFN2 differs from xtb's by ~7e-9 Eh and gets every one of the nine right, on both chips.

**Clean-room, with upstream's binary** (`repro.sh`, `results/diag/repro-upstream-x86_64.txt`). Every
call ran `xtb coord --gfn 2 [--grad] --norestart` in its own fresh `mktemp -d` holding only `coord`, and
no `xtbrestart` was written. On `ACONF/B_G`, C1's dE/dx is −5.970e-3 analytic against **+1.669e-3** by
central difference (h = 1e-3 Bohr). Its y and z components agree to ≤ 6e-8, and the torque is
(0, 0, 2.755e-2) Eh. With `--tblite` the same binary gives +1.6695e-3 and a torque of 1e-11. Working-dir
state (xtb#1378) is ruled out.

**`tblite` 0.6.0's CLI segfaults in this env on every structure, on both architectures**
(`GOMP_parallel`, `results/*/tblite-probe.txt`). tblite 0.4.0 runs, so the cross-code check it would have
given is missing from the pinned image.

## Fixed point or path

- **Energies are fixed points.** Intel and AMD are bit-identical on all 2,458 converged structures. Both
  run the same linux-64 twin, and OpenBLAS picks `Cooperlake` on both. Graviton4 (`neoversev2`) is within
  **1.0e-12 Eh** of x86 on every structure, and bit-identical on 2,432 benchmark and 2,438 tight energies.
  All 1,501 computable reaction energies (the other 4 need a non-converged species) and all 55 MAEs
  are identical across the three chips at every printed digit.
- **The 5-point FD derivatives agree across chips to 7.5e-10 Eh/Bohr.** Those are energies again.
- **The analytic gradient is not a path.** It differs by 9× between architectures, which is a different
  function, not round-off. On x86 it is bit-identical between Intel and AMD on 2,342 of 2,354
  structures, and within 4e-12 relative on the rest.
- **The same four SCCs fail on every chip.** Between `--acc 1` and `--acc 0.0001`, the `Si` atom (in
  W4-11, G21IP and G21EA) moves by 3.0e-3 Eh: an open-shell atom landing on a different solution, the
  same one on all three chips. Every other structure moves by ≤ 3.7e-8 Eh.

## Pins

| | |
|---|---|
| dataset | `grimme-lab/GMTKN55` commit `8d485b37a1ca8837e395042671ca5ba4e0714691` (branch `v1`, the 2017 publication; CC-BY-4.0). Archive checked to carry that commit id, kept as `gmtkn55-v1.tar.gz`, sha256 `ee134a03a7c146931f10988965dad52a4d6bdde1d9f1fed5281934b0d7b7dfd1` |
| published GFN2 values | arXiv:2107.00299v2 (Christensen et al., OrbNet Denali), Table IV, GFN2-xTB column; transcribed into `xtb_run.py` |
| image | the recipe's `quay.io/aarchsci/comp-chem@sha256:f22ef7a3…` (xtb 6.7.1 `edcfbbe`, build `gfortran_hcd2ce30_5`), and `envs-x86_64/comp-chem-823f1ef5e11e` on x86 (build `gfortran_ha02e54d_5`) |
| diagnostics | `mambaorg/micromamba` 2.9.0, the per-arch digests `recipes/fsl` pins; conda-forge solved at run time (`results/diag/` lists what each solve picked) |
