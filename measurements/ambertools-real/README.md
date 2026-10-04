# A serial MD integrator gains more from a new chip than anything else measured here

> **`sander`'s 100 ps solvated NVE run is 2.50× faster on Graviton5 than Graviton2** — the largest
> generational gain in this catalog, beating interval arithmetic (2.33×) and plane-wave DFT (1.86×).
> It settles an open question the wrong way round: **~1.9× is not a universal figure**, and a
> single-threaded Fortran integrator is where the chips have improved most.

AmberTools 26.0 `sander`, 2,101-atom solvated alanine dipeptide, PME, SHAKE, 100,000 steps at
dt = 1 fs. Same image digest, same in-task `tleap` build, 2 vCPU on every rung, nothing staged.

| generation | instance | NVE 100 ps | compute | billed | ns/day | $/hr | **compute $** | billed $ |
|---|---|---|---|---|---|---|---|---|
| Graviton2 | `c6g.large` | 1613 s | 1819 s | 1973 s | 5.36 | 0.0680 | **0.0344** | 0.0373 |
| Graviton3 | `c7g.large` | 1062 s | 1200 s | 1326 s | 8.14 | 0.0725 | **0.0242** | 0.0267 |
| Graviton4 | `c8g.large` | 914 s | 1033 s | 1186 s | 9.45 | 0.0798 | **0.0229** | 0.0263 |
| **Graviton5** | `c9g.large` | **644 s** | 728 s | 817 s | **13.42** | 0.0869 | **0.0176** | 0.0197 |

Per-step: **1.52×, 1.16×, 1.42×.** `$/hr` rises **27.8%** across the ladder while the cost of the
same 100 ps falls **49%**.

**This ladder is clean enough that both columns agree.** `billed/compute` is 1.08–1.15 on every
rung, because 12–30 minutes of work dwarfs the ~145 s of provisioning. That is the regime
[cost-per-result](../../patterns/cost-per-result.md) says to trust; the short-job ladders in this
directory have to be read on compute alone, because there billed is mostly boot.

## Why this recipe, and what it answers

The gains measured here previously clustered near 1.9× across instruction mixes that have nothing in
common — picard's JVM sort/hash **1.93×**, SIESTA's dense FP linear algebra **1.86×**, HMMER
**1.90×**, seqkit **1.88×**, with bedtools `genomecov` the outlier at **2.33×**. That clustering is
why "FP versus integer" was rejected as the discriminator and working-set behaviour proposed
instead, still unestablished.

`sander` is a genuinely different inner loop from all of them: single-threaded throughout, no thread
flag at all, dominated by PME FFTs and SHAKE constraint iteration. At **2.50×** it is now the
catalog's largest gain, which means the ~1.9× figure is a coincidence of the sample rather than a
property of the chips. **Do not quote a single generational multiplier as if it were the answer** —
the observed range across this catalog is now 1.86×–2.50×, a 1.34× spread, and the code decides
where in it you land.

**Graviton3→4 is again the weak step**: 1.16× for 10% more per hour, so 5% cheaper per result — the
narrowest margin on the ladder, bracketed by 1.52× and 1.42×. That matches the pattern recorded for
likelihood and matrix codes, and now holds for a serial MD integrator too.

## The free identity: the physics is the same on all four chips

Every assertion in the recipe passed on every rung, unchanged — the cross-code single point against
GROMACS at **0.0011 kcal/mol**, SHAKE holding 138,600 water O-H distances, NVE conservation under 1%
of *kT* per degree of freedom per ns, the exact atom and constraint counts. That is what makes n = 1
per generation defensible: each rung is a correctness check on the other three, so a wrong number
would have to be wrong identically four times.

## A pinned digest does not pin the numerics

This was not the point of the sweep and is the more useful result. The *trajectory* is chaotic, so
it is not expected to be reproducible — but it does not vary per machine. Across five machines there
are exactly **three** trajectories, and what predicts the grouping is the BLAS kernel the process
selects at load time:

| machine | CPU part | SVE | OpenBLAS corename | SHAKE max dev | NVE leak |
|---|---|---|---|---|---|
| Apple-Silicon laptop | — | none | `neoversen1` | 7.45e-06 Å | 5.68e-03 |
| Graviton2 `c6g` | 0xd0c | none | `neoversen1` | **7.45e-06** | **5.68e-03** |
| Graviton3 `c7g` | 0xd40 | SVE 32 B | `neoversev1` | 8.17e-06 | 2.73e-03 |
| Graviton4 `c8g` | 0xd4f | SVE2 16 B | `neoversev2` | 8.88e-06 | 4.54e-03 |
| Graviton5 `c9g` | **0xd84** | SVE2 16 B | `neoversev2` *(fallback)* | **8.88e-06** | **4.54e-03** |

Three corenames, three trajectories, one-to-one. The two rows that look like coincidences are the
evidence: a **laptop that is not an AWS instance at all** lands on the Graviton2 kernel and matches
it digit for digit, and **Graviton5 is different silicon** — CPU part `0xd84`, not Graviton4's
`0xd4f` — for which OpenBLAS 0.3.34 has no kernel, so it falls back to `neoversev2` and inherits
Graviton4's numbers exactly.

### The controlled test

Correlation over five machines is suggestive, so one variable was changed on one host. Forcing the
Graviton2 kernel **on a Graviton4 box**:

```sh
export OPENBLAS_CORETYPE=NEOVERSEN1     # same instance type, same image digest
```

| Graviton4 run | SHAKE max dev | NVE leak | drift over 100 ps | ns/day |
|---|---|---|---|---|
| native (`neoversev2`) | 8.88e-06 | 4.54e-03 | −1.9126 | 9.45 |
| **forced `NEOVERSEN1`** | **7.45e-06** | **5.68e-03** | **−2.3891** | 9.41 |

It reproduces the laptop/Graviton2 trajectory **exactly**, including a drift that matches the
laptop's to four decimal places — on Graviton4 hardware, from the same digest. That is a
demonstrated cause, not a grouping.

It also costs **0.4%** (918 s vs 914 s), so on this workload bit-reproducibility across machines is
effectively free. That will not generalise — the whole point of DYNAMIC_ARCH is that the newer
kernels are usually faster — but it is worth measuring before assuming the trade is expensive.

### What to take from it

- **A pinned container digest does not pin floating-point results.** The cookbook pins every image
  by `@sha256:`, and that is still necessary — but OpenBLAS is built `DYNAMIC_ARCH` and re-selects
  kernels from the *host's* CPU at load time, so the same digest is a different computation on a
  different box. Reproducibility is per kernel target, not per image.
- **It is one environment variable away** when you need it: `OPENBLAS_CORETYPE`. Recipes here
  deliberately do **not** set it, because the catalog's job is to report what a normal run does on
  each chip; a paper reproducing an exact trajectory should set it.
- **This is why none of the recipe's assertions is a remembered value.** You cannot tell from a
  trajectory number which kernel produced it, so every check is a conservation law, a geometric
  constraint, or a cross-code identity — and all of them held on all four generations plus the
  forced-kernel run.
- An earlier draft of this page blamed FFTW's SIMD dispatch. `sander` does link `libfftw3`, so it
  was plausible, but the mechanism is the BLAS: `liblapack.so.3 → libopenblasp-r0.3.34.so`, and the
  corename predicts the grouping where vector-ISA presence alone does not.

### Does it invalidate any assertion already in the catalog? No — and here is the dividing line

The obvious worry is that several recipes assert exact floating-point values a BLAS touched —
nwchem (−625.538048 ± 1e-5 Ha), siesta reproducing a published −214.377236 eV, gpaw −11.703689 eV,
psi4 and pyscf textbook energies — and the `dft` env carries the same `DYNAMIC_ARCH` OpenBLAS
0.3.34. So the same controlled test was run on nwchem: `OPENBLAS_CORETYPE=NEOVERSEN1` on a
Graviton4 box, 217 basis functions, B3LYP/6-31G\*.

| quantity | native `neoversev2` | forced `neoversen1` | change |
|---|---|---|---|
| ambertools NVE drift / 100 ps | −1.9126 kcal/mol | −2.3891 kcal/mol | **25%** |
| **nwchem DFT energy** | −625.538048227205 Ha | −625.538048227**199** Ha | **6e-12 Ha** |
| nwchem serial == 4-rank | 4.55e-08 | 4.54e-08 | — |

**Twelve digits agree on the energy.** Against the recipe's 1e-5 Ha tolerance that is roughly 2e6×
of headroom, and the wall times moved 128→127 s and 68→69 s, i.e. not at all.

So the sensitivity is not a property of the code or of the BLAS — it is a property of **what you are
asserting**. An SCF or variational result is a *fixed point*: kernel choice changes the path taken
to it and cannot move the answer past convergence. A trajectory is a *path*, and 100,000 steps
amplify the last bits into the first. That is why the catalog's exact-FP assertions are safe, and
why the quantity it refuses to assert — a remembered trajectory value — is exactly the one that
would not be.

Worth stating because the over-cautious conclusion is also wrong: this is **not** a reason to stop
reproducing published numbers. It is a reason to know which kind of number you have.

### The x86 question

The same mechanism exists there — OpenBLAS DYNAMIC_ARCH picks `HASWELL`/`SKYLAKEX`/`ZEN` and so on —
so the prediction is that x86 trajectories group by corename too, and that `OPENBLAS_CORETYPE` pins
them the same way. **Untested here**, and it needs an image that does not exist yet: biocontainers
carries AmberTools only at 20.4 and 21.10, while this recipe is 26.0. conda-forge *does* ship
`ambertools 26.0` for `linux-64` as well as `linux-aarch64`, so a version-matched x86 image is
buildable from the same recipe — which is exactly the case for an x86 sibling registry, where the
comparison is attributable by construction because only the chip differs.

Note what an x86 run would and would not show. A *different* trajectory there proves nothing about
the mechanism, because it would be a different compiler and build — not the same binary on a
different host, which is what makes the arm64 evidence clean. The informative x86 experiment is the
same-image-across-x86-microarchitectures one: `c6a` (Zen 3) against `c7a` (Zen 4) against `c7i`
(Sapphire Rapids), where a grouping by corename would confirm the mechanism on the other
architecture.

## Reproducing it

```sh
B=$(make -s print-bucket)
for f in c6g c7g c9g; do
  sed "s|\${COOKBOOK_BUCKET}|$B|g" measurements/ambertools-real/gen-$f.task.json > /tmp/gen-$f.json
  spawn task run --spec /tmp/gen-$f.json --wait      # serially — see below
done
```

Graviton4 needs no run: the recipe's own verified run **is** that rung, same spec and digest.

**Launch the rungs serially.** truffle's static price fallback has no Graviton coverage
([truffle#175](https://github.com/spore-host/truffle/issues/175)), so a throttled Price List call
refuses a Graviton launch that carries a cost limit — and concurrent launches are what trigger the
throttle. TTL is per rung rather than uniform (50m/40m/30m, caps $0.07/$0.06/$0.05), scaled from the
measured Graviton4 wall by the worst slowdown this catalog had seen; the actual worst rung used 1973 s
of its 3000 s.

Raw smoke-check output per rung and the ladder as TSV are in [`results/`](results/).
