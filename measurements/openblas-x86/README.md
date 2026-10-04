# x86 picks five different BLAS kernels and computes the same answer on all of them

> **x86 kernel dispatch is as varied as arm64's — four microarchitectures, four different
> OpenBLAS kernels, including AMD Zen 4 running an *Intel-named* one. And the numbers do not
> move: bit-identical on all four.** That is a negative result, and it narrows the
> [digest-doesn't-pin-numerics finding](../ambertools-real/README.md) rather than extending it.

Same `linux/amd64` image digest on every x86 rung, so only the host microarchitecture differs.
2 vCPU / 4 GiB each. The probe computes two things through OpenBLAS: a **fixed point** (dominant
eigenvalue by power iteration, cross-checked against LAPACK's own eigendecomposition) and an
attempted **path** (a logistic map at r = 3.9 applied between `dgemv` calls, so rounding should
amplify).

| instance | CPU | OpenBLAS kernel | converged eigenvalue | chaotic final x₁ |
|---|---|---|---|---|
| `c6i.large` | Xeon Platinum 8375C (Ice Lake) | **`SkylakeX`** | −4.026552552113374e+01 | −2.405351530260683e-01 |
| `c6a.large` | EPYC 7R13 (Zen 3) | **`Zen`** | −4.026552552113374e+01 | −2.405351530260683e-01 |
| `c7i.large` | Xeon Platinum 8488C (Sapphire Rapids) | **`SapphireRapids`** | −4.026552552113374e+01 | −2.405351530260683e-01 |
| `c7a.large` | EPYC 9R14 (Zen 4) | **`Cooperlake`** | −4.026552552113374e+01 | −2.405351530260683e-01 |
| `c8g.large` | Graviton4 (Neoverse V2) | `neoversev2` | −4.026552552113374e+01 | −2.405351530260**680**e-01 |
| laptop | Apple M-series | `neoversen1` | −4.026552552113**377**e+01 | −2.405351530260**677**e-01 |

## Two things worth keeping

**AMD Zen 4 runs `Cooperlake`.** OpenBLAS 0.3.34 has no Zen 4 target, Zen 4 supports AVX-512, so
the dispatcher falls back to an Intel-named AVX-512 kernel on AMD silicon. That is the exact shape
of Graviton5 (CPU part `0xd84`) falling back to `neoversev2` — a newer core inheriting an older
core's kernel — and here it crosses *vendors*. If you were reasoning about which kernel your job
uses from the instance family name, you would get this one wrong.

**Four kernels, one answer.** Every x86 rung agrees to all 16 digits on both quantities. Within
arm64 the two kernels differ by 1 ulp in the most sensitive quantity (`…680` on `neoversev2` vs
`…677` on `neoversen1`), and the cross-architecture difference is uninformative because amd64 and
arm64 are different builds from different compilers.

## What this does and does not say

It **does not** overturn the ambertools result, because that one is a controlled experiment:
`OPENBLAS_CORETYPE=NEOVERSEN1` on a Graviton4 box, same image, one variable changed, and the MD
trajectory flipped to the Graviton2/laptop group exactly. Nothing but kernel selection can explain
that.

What it does say is that kernel dispatch is **not** uniformly dangerous. A 200×200 `dgemv` is
bit-identical across five kernels, so the risk is specific to particular routines and problem
sizes — large blocked GEMM, where the dispatcher actually reaches kernel-specific code — rather
than to "anything that touches a BLAS". That is a narrowing, and a reassuring one: it means the
[standing rule](../../CLAUDE.md)'s fixed-point-versus-path line needs a third condition, *and the
call has to be big enough to dispatch differently in the first place*.

**The honest limitation is the probe, and it took three attempts to see it.** Version 1 measured a
"path" that was a power iteration — which *converges*, so its 20,000-term sum was ~20000 × λ, a
fixed point wearing a path's clothes, and it came back identical everywhere for a reason that had
nothing to do with kernels. Version 2 added a logistic nonlinearity to make it genuinely chaotic.
It still barely moves, and the likeliest reason is the renormalisation each step plus a matrix far
too small to leave the common code path. So: **suggestive that x86 is more numerically uniform
across microarchitectures than arm64, not established.** The experiment that would settle it is the
one still blocked — AmberTools 26.0 on x86, same protocol as the arm64 ladder, which needs a
version-matched image (conda-forge ships `ambertools 26.0` for `linux-64`, so it is buildable; the
biocontainers images are 20.4 and 21.10).

## Reproducing it

```sh
B=$(make -s print-bucket)
for f in c6i c6a c7i c7a c8g; do
  sed "s|\${COOKBOOK_BUCKET}|$B|g" measurements/openblas-x86/chaos-$f.task.json > /tmp/ch-$f.json
  spawn task run --spec /tmp/ch-$f.json --wait     # serially, per truffle#175
done
```

The probe reads the kernel with `ctypes` rather than R's `.Call`: `openblas_get_corename` and
`openblas_get_config` are plain C symbols returning `char*`, not registered R routines, and
`.Call` on them kills the script — which is how the first batch of five rungs was lost.

Raw per-rung output and the table as TSV are in [`results/`](results/); the probe scripts are
[`probe.R`](probe.R) (fixed point) and [`chaos.R`](chaos.R) (attempted path).
