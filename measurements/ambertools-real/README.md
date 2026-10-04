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

## A reproducibility finding that was not the point of the sweep

The *trajectory* is chaotic, so it is not expected to be reproducible — and it isn't. But it does not
vary per machine. Across five machines there are exactly **three** trajectories, and they group:

| machines | SHAKE max dev | NVE leak | vector path |
|---|---|---|---|
| Apple-Silicon laptop, **Graviton2** | 7.45e-06 Å | 5.68e-03 | NEON only |
| **Graviton3** | 8.17e-06 Å | 2.73e-03 | SVE (256-bit) |
| **Graviton4 and Graviton5** | **8.88e-06 Å** | **4.54e-03** | SVE2 |

Graviton4 and Graviton5 agree to every printed digit, and Graviton2 agrees to every printed digit
with a laptop that is not an AWS instance at all. Two independent coincidences on a chaotic
trajectory is not a coincidence.

**The grouping is the measurement; the mechanism is a hypothesis.** The obvious candidate is
runtime kernel dispatch: `sander`'s PME goes through FFTW, which selects SIMD kernels by detected
CPU features, so a different vector width changes the summation order in the transform and 100,000
MD steps amplify the last bits. That is consistent with all five observations and **not established
here** — confirming it needs an FFTW wisdom or kernel dump per rung, which this sweep did not
collect.

Either way it refines what the recipe's page should claim. "The trajectory is not reproducible
across machines" is too strong: it is bit-reproducible wherever the vector path matches, which is
also why a recipe must never assert a remembered trajectory value — you cannot tell from the
number which group you are in.

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
