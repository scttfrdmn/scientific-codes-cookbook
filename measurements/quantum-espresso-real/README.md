# Quantum ESPRESSO on AUSURF112 — Graviton4, Intel and AMD

> **AMD EPYC converges the 112-atom gold slab 3.7× faster than Graviton4 and is 63% cheaper per
> result billed. Intel is 1.7× faster than Graviton4 and 30% cheaper.** On all three chips the
> converged total energy, all 336 force components and the Fermi level are identical to every printed
> digit, and so is the iteration count. The Graviton4 deficit is in dense complex linear algebra, not
> in the FFTs.

Workload: **AUSURF112**, the QE Foundation's own `pw.x` benchmark: a 112-atom Au surface slab,
800 Kohn–Sham bands, 1,232 electrons, ultrasoft PBE, 25/200 Ry cutoffs, 2 k-points, Marzari–Vanderbilt
smearing. It is run to a converged SCF with forces, as a researcher runs it, twice on each box: once
with `pw.x` defaults and once with two k-point pools (`-nk 2`).

**Data tier: a durable public id.** [`QEF/benchmarks`](https://github.com/QEF/benchmarks) at commit
`eff15189`, `AUSURF112/ausurf.in` and `Au.pbe-nd-van.UPF` (322 KB), pinned by
[`inputs.sha256`](inputs.sha256). [`stage-inputs.sh`](stage-inputs.sh) streams them into the bucket
and re-checks them there; the task checks them again.

## Run it

```sh
export AWS_PROFILE=aws B=<your bucket>
bash measurements/quantum-espresso-real/stage-inputs.sh "$B"
for a in arm64 x86_64 amd; do
  spawn task run --spec "$(python3 scripts/spec_arch.py quantum-espresso $a "$B" "$(date +%s)" \
    measurements/quantum-espresso-real/real.task.json)" --wait
done
bash measurements/quantum-espresso-real/fetch-results.sh "$B" r2   # results/<chip>/, results/crosschip.txt
```

[`run.sh`](run.sh) makes two edits to the benchmark input, recorded in each run's
`input-edits.diff`: `electron_maxstep` 2 → 100 (the benchmark stops after two iterations to time
them) and `conv_thr = 1.d-8`. It then runs, per decomposition:

```sh
mpirun --use-hwthread-cpus -n 16 pw.x [-nk 2] -in ausurf.in
```

## Results

16 MPI ranks × 1 thread on a `4xlarge` (16 vCPU, 32 GiB) of each chip. The recipe's `dft` image runs
on Graviton4, and its linux-64 twin on both x86 chips, on the same staged bytes:

| chip | instance | tool wall | billed | peak memory | $/hr | compute $/result | billed $/result |
|---|---|---|---|---|---|---|---|
| Graviton4 | `c8g.4xlarge` | 6,271 s | 6,467 s | 23.4 GiB | 0.6381 | 1.112 | 1.146 |
| Xeon 6975P-C | `c8i.4xlarge` | 3,684 s | 3,826 s | 28.0 GiB | 0.7497 | 0.767 | 0.797 |
| **EPYC 9R45** | `c8a.4xlarge` | **1,667 s** | **1,782 s** | 28.0 GiB | 0.8622 | **0.399** | **0.427** |

A *result* is the whole verified run: two converged SCFs with forces, and the checks. *Billed* is
launch to terminate. *Peak memory* is the cgroup's `memory.peak`, which on x86 includes the page
cache of the env install. The largest rank's `ru_maxrss` is 1.37–1.55 GiB on all three chips, and
`pw.x` estimates 20.8 GB in total.

| per SCF, defaults (27 iterations) | Graviton4 | Xeon | EPYC |
|---|---|---|---|
| wall | 2,941 s | 1,784 s | 795 s |
| per iteration | 109 s | 66 s | 29 s |
| compute $ | 0.521 | 0.372 | 0.190 |

- **The gap is in the dense linear algebra.** `pw.x`'s own timers, Graviton4 against EPYC:
  `calbec`, `s_psi` and `add_vuspsi` (ZGEMM against the projectors) are 4.4–4.6× slower; the
  Davidson solver `cegterg` 3.9×; the FFTs (`fftw`) 2.7×; `vloc_psi` 2.7×. Against Intel the same
  ZGEMM timers are 2.0× and the FFTs 1.2×. All three link conda-forge's OpenBLAS 0.3.34: `neoversev2`
  on Graviton4, `Cooperlake` on both x86 chips. Whether another BLAS closes the gap was not measured.
- **Intel's 16 vCPU are 8 cores × 2 threads** (`Thread(s) per core: 2`). AMD's and Graviton4's are 16
  full cores. The rental is like-for-like; a per-core comparison it is not.
- **Two pools are slower here, not faster:** `-nk 2` took 2 more iterations (29) and 6–13% more wall
  on every chip. On one 16-rank node, plane-wave parallelism over 16 ranks beats 2 × 8.
- **Overhead is 115–196 s per bill**, small against the SCFs.
- n = 1 per x86 chip and n = 2 on Graviton4: an earlier run on a previous version of the checks gave
  the same energies, forces and iteration counts, and A's wall was 2,941.9 s against 2,941.6 s. The SCF is deterministic, so each cell times one
  computation.

## Why this size

AUSURF112 is the input the QE developers chose to represent a production `pw.x` run. It is a metal
surface of the size surface-science and catalysis studies use, with enough bands that the subspace
diagonalisation is distributed (ELPA, 4 × 4 ranks). It fits one 32 GiB node, and one SCF is
13–49 minutes, so a result costs under $1.20 on every chip. The benchmark's other inputs either need
many nodes (`CNT10POR8`, 1,532 atoms on 800 processes in its reference output) or, like `GRIR443`, are
the same kind of run at larger scale.

## What held at scale, on all three chips

Asserted inside the task by [`check.py`](check.py); a failure fails the run.

| claim | why it must hold | Graviton4 | Xeon | EPYC |
|---|---|---|---|---|
| QE 7.5, 16 MPI processes reported by `pw.x` | the build is parallel ([rank-count guard](../../practices/mpi-rank-count.md)) | 16 / 16 | 16 / 16 | 16 / 16 |
| `npool` = 1 and 2 | the second decomposition happened | 1, 2 | 1, 2 | 1, 2 |
| 112 atoms, 800 bands, 1,232 electrons | the benchmark system | ✓ | ✓ | ✓ |
| SCF converged, estimated accuracy < 1e-8 Ry | the fixed point was reached | 8.3e-9 / 5.4e-9 | same | same |
| **\|E(defaults) − E(-nk 2)\| < 1e-6 Ry** | **a converged energy is a fixed point** | **3.0e-8** | **3.0e-8** | **3.0e-8** |
| **max \|ΔF\| ≤ the two runs' SCF corrections** | **forces are first order in the density error** | **3.1e-5 ≤ 1.0e-3** | same | same |

**Why these bounds.** QE's "estimated scf accuracy" is an estimate, not a bound: the two
decompositions land 3.0e-8 Ry apart with estimates of 8.3e-9 and 5.4e-9. So the energy bound is
100 × `conv_thr`. That is 1e-10 relative on −11,427 Ry and 1.4e-5 eV across 112 atoms; a wrong
pseudopotential, cutoff or k-mesh moves the energy by orders more. A force is not variational, so its
bound is QE's own estimate of each run's force error from non-self-consistency ("Total SCF
correction", 5.8e-4 and 4.5e-4 Ry/au), summed. Reported, not asserted: the net force on the slab is
≤ 3e-8 Ry/au, and the Fermi level (0.7845 eV) is the same under both decompositions.

## Across chips: fixed point and path

[`collect.py`](collect.py) asserts the same bounds between chips, against Graviton4. **Every check is
met at zero:** the total energy (−11427.09402180 Ry with defaults, −11427.09402183 with pools), all
336 force components and the Fermi level are identical to the printed digit on all three chips,
for both decompositions ([`results/crosschip.txt`](results/crosschip.txt)).

The path agrees too, which was not required: the same 27 and 29 iterations, and every per-iteration
energy and accuracy estimate identical except one. Iteration 4's energy differs in its last printed
digit (1e-8 Ry) between the arm64 and x86 builds, in both decompositions. Intel and AMD agree in
every printed energy, accuracy estimate, force and Fermi level. They run the same linux-64 build
under the same OpenBLAS core (`Cooperlake`; 0.3.34 has no Zen 5 kernel). So nothing here varies by
vendor, and the one
cross-build difference is in the last digit of an intermediate iterate. These are print-precision
statements: the benchmark's `disk_io = 'none'` writes no XML with the full-precision values.

## Caveats

- One system, one rank count (16), two decompositions. No rank sweep or multi-node run was measured.
  The [GPAW](../gpaw-real/README.md) sweep is the model if a knee is needed.
- The specs carry a 4 h TTL as the backstop and a `cost_limit` of $2.00 on Graviton4 (spec_arch.py
  scales it to $2.60 and $3.20 on x86). Prices are spawn's on-demand figures for us-west-2.

## Spend

Four launches, **$3.53**: the three verifying runs ($2.37) and a first Graviton4 run ($1.16). That
run's science passed; its checks read an XML file the benchmark never writes, so they were rewritten
to read `pw.x`'s output and all three chips were run on the new spec.
