# Two DFT codes, one lattice constant: 0.0097 Å apart

> **SIESTA and GPAW agree on bulk Si's equilibrium lattice constant to 0.18%** — a localised numerical
> basis with a pseudopotential against plane waves with PAW. Their *total energies* differ by 204 eV,
> which is why the comparison has to be structural.

Bulk Si, diamond, LDA, 6×6×6 k-mesh, seven lattice constants from 5.25 to 5.61 Å, the same quadratic
fit applied to both scans.

| code | method | basis | **a₀** |
|---|---|---|---|
| SIESTA 5.4.2 | LCAO + Troullier-Martins pseudopotential | DZP, 200 Ry mesh | **5.4042 Å** |
| GPAW 25.7.0 | PAW + plane waves | 500 eV cutoff | **5.4139 Å** |
| | | **difference** | **0.0097 Å (0.18%)** |

Both land inside the LDA literature band for Si (5.37–5.44 Å; LDA underbinds relative to the 5.431 Å
experimental value, as expected).

## Why total energies are not compared

```text
a (Å)    SIESTA (eV)     GPAW (eV)
5.25     -215.545319     -11.788210
5.31     -215.603859     -11.850852
5.37     -215.630950     -11.882121
5.43     -215.629867     -11.885148
5.49     -215.603463     -11.862845
5.55     -215.554453     -11.817906
5.61     -215.485456     -11.752831
```

The curves are the same shape and ~204 eV apart. That offset is not an error in either code: a
pseudopotential total energy and a PAW total energy use different references for the core electrons,
so the absolute numbers are not the same quantity. **Only a structural observable — where the curve
turns over — is comparable at all**, which is the whole reason this check is a lattice constant and
not an energy.

## Matching the modes, and what sets the tolerance

Three things had to be matched before the remaining difference could be attributed to the basis:

- **XC functional.** SIESTA's `.fdf` specifies none, so it defaults to **LDA**. GPAW was set to LDA
  to match. Running GPAW at PBE — the more natural modern default — would have put a functional
  difference of ~0.03 Å into the comparison and made two correct codes look further apart than they
  are. This is the same trap as [PySCF versus Psi4's density fitting](../../practices/cross-checks.md):
  a method difference masquerading as a basis limit.
- **k-mesh.** 6×6×6 both sides, converged to under 0.005 Å.
- **The fit.** The same quadratic through the same seven points, in the same code. Two different
  fitting procedures would contribute their own disagreement.

What is left is the basis treatment, and that is what the tolerance budgets. **0.03 Å** is the
expected spread between a DZP localised basis and a converged plane-wave basis for a covalent
semiconductor. The observed 0.0097 Å clears it with 3× margin — tight enough to be a real
cross-validation of the numerics, loose enough that a basis detail cannot make it flaky. A tolerance
chosen from how close the two happened to land would have been a fudge.

**The tolerance is also not a claim about accuracy.** Neither code is "right" here: both are LDA, and
LDA is wrong about Si by ~0.03 Å against experiment. What the agreement establishes is that two
independent implementations of the same physics produce the same physics — which no single-code check
can show.

## Generation scaling differs by method, not just by domain

Both DFT codes measured across the same four chips, alongside the two MD codes:

| code | domain | Graviton2 → Graviton5 |
|---|---|---|
| GROMACS | MD, PME | **2.43×** |
| GPAW | DFT, plane wave | 2.33× |
| LAMMPS | MD, PPPM | 2.24× |
| **SIESTA** | DFT, LCAO | **1.86×** |

So "FP-heavy codes gain more" is too coarse. SIESTA gains the least of the four despite being DFT,
because a small localised-basis problem is dense linear algebra on modest matrices, while GPAW's
plane-wave work and both MD codes' force loops look much more like the throughput the newer chips
added. **Method matters more than domain.**

And in **both** DFT codes, Graviton3→Graviton4 is cost-neutral: GPAW $0.0773 against $0.0784 per SCF,
SIESTA $0.00145 against $0.00146 per run. Two codes agreeing makes that more than an n=1 oddity — if
you are on Graviton3 running DFT, the step worth paying for is Graviton5. Why the step is weak is not
established here; it would need a bandwidth measurement to attribute.

## Run it

```sh
export AWS_PROFILE=aws COOKBOOK_BUCKET=<your bucket>
make stage RECIPE=siesta                                        # Si.psf, version-matched
make run   RECIPE=siesta                                        # reference + 7-point scan
spawn task run --spec measurements/dft-crosscheck/gpaw-eos.task.json --wait
spawn task run --spec measurements/dft-crosscheck/crosscheck.task.json --wait
```

The cross-check is a task rather than prose, so the agreement is asserted and fails loudly if either
code moves.

## Caveats

n = 1 per lattice point; both scans are deterministic, so repetition buys nothing — a₀ came out
identical to four decimals on all four Graviton generations in SIESTA's case.

The two codes run from **different image digests** of the same `dft` env, because each recipe pins the
digest it was verified against. They are not the same build of the same libraries, which is if
anything a stronger test of agreement than a shared build would be.

This is one system, one functional, one property. It establishes that the two codes agree on Si's
equilibrium geometry under LDA; it does not establish agreement on magnetic systems, on surfaces, on
forces, or at other functionals.
