# ASE → spglib → phonopy — a chained materials workflow on bulk silicon

One task, three tools, one chain. ASE builds a silicon crystal, spglib identifies its
symmetry, and phonopy uses that symmetry to reduce the displacement set and compute
phonons — and the smoke check confirms the space group and that the acoustic modes vanish
at Γ, an identity that validates the *whole chain*.

> **What this recipe does and does not cover.** It builds one crystal, finds its space
> group, and computes its Γ-point phonons — enough to prove ASE, spglib and phonopy work
> and hand off correctly on Graviton4. Forces come from a generic Lennard-Jones field, so
> the phonon *frequencies* are not silicon's real spectrum; the identities asserted don't
> depend on that (see below). Not a benchmark; no DFT forces, dispersion, or thermodynamics.

## Why a chain, not three co-located tools

The three tools are wired in series: **ASE** builds bulk Si → **spglib** identifies the
symmetry → **phonopy** uses that symmetry to cut the full displacement set down to the
symmetry-unique ones and build the force constants. So the final identity — acoustic modes
→ 0 at Γ — validates the whole chain: if spglib returned the wrong space group, phonopy's
symmetry reduction and force constants come out wrong and the acoustic modes don't vanish.
That's what makes this a workflow rather than three checks sharing a container (the same
distinction as `recipes/openmm-mdanalysis`).

Concretely, spglib's symmetry reduces the 128-atom supercell's displacement set to **one**
symmetry-unique displacement — the chain link made visible in the check.

## Two physical identities, no fitted bands

- **Space group is exact-or-wrong.** Silicon is `Fd-3m` (#227); there is no tolerance to
  argue about.
- **Acoustic modes → 0 at Γ is a conservation-class identity.** Translational invariance
  (the acoustic sum rule) forces the three acoustic branches to vanish at the zone center —
  the same class as `recipes/climate`'s constant-field regrid or `recipes/ambertools`' NVE:
  a construction where physics forces an exact answer. **Crucially, this holds for any
  translationally-invariant potential**, so the generic Lennard-Jones force field is
  sufficient to validate the chain and the sum rule — it is *not* a claim about silicon's
  real phonons. A wrong force-constant chain gives THz-scale nonzero acoustic modes.
- (Bonus symmetry identity: the three optical modes at Γ are triply degenerate — T₂g — to
  machine precision.)

## Pins

| | |
|---|---|
| image | `quay.io/aarchsci/dft@sha256:0740fab9721da533ce153cae3590b1c6822dd0decfa1838b0753e76ba4434a4e` |
| | tag `2026.09.04` / `s5cb0d94e928d`, ASE + spglib + phonopy (+ pymatgen), cosign-signed, `linux/arm64` |
| input | bulk-Si cell, **built in code** — nothing staged |

**Data tier: none / in-task.** Same `dft` image as `recipes/gpaw` and `recipes/nwchem`;
this recipe uses ASE, spglib and phonopy.

## Smoke check

Measured in this image, before any launch.

| observable | assertion | observed |
|---|---|---|
| ASE unit cell | exactly 2 atoms (Si diamond primitive) | 2 |
| **space group** | `Fd-3m (227)` (spglib) | Fd-3m (227) |
| symmetry-reduced displacements | exactly 1 (spglib cut the full set) | 1 |
| **acoustic modes at Γ** | max < 1e-2 THz (→ 0 by the sum rule) | 7.0e-7 |
| optical degeneracy | 3 optical modes' spread < 1e-3 THz (T₂g) | 3.6e-15 |

The acoustic band (1e-2 THz) is justified by the method — a residual from the 0.03 Å finite
displacement and floating-point precision, observed ~7e-7 — not a value picked to pass;
analytically the modes are exactly zero. The space group, displacement count and optical
degeneracy are exact.

## Resources, and what the timings mean

2 vCPU / 4 GiB, `c8g` (resolves to `c8g.large`), TTL 5m, cap $0.02. The build + symmetry +
phonon calculation is **sub-second**.

**These timings are not compute cost.** Boot, the Docker install, and pulling the
**~0.87 GB** `dft` image are the whole task. The recorded run's command window was **94s**
(02:15:04 → 02:16:38 UTC), space group Fd-3m (227) and acoustic modes 7e-7 THz. TTL was
**retightened from that first real run**: 10m → **5m**, `cost_limit` $0.03 → $0.02. A loose
TTL is a larger blast radius, not caution; the recorded run used the original 10m. Disk is
trivial.

## Running it

No `stage-inputs.sh` — the crystal is built in code.

```sh
spawn task run --spec recipes/ase-phonopy/01-phonons.task.json --wait
```

Then **check the bucket**, every time:

```sh
aws s3 ls s3://scicookbook-942542972736-us-east-1/runs/ase-phonopy/r1/
```

`--wait` exiting 0 does **not** prove the outputs exist (spore-host/spawn#561): the smoke
check runs *inside* the task, and the bucket listing is the second half of it. Expect one
object (`smoke-check.txt`).

**Re-running.** `task_id` is fixed; bump the `-r1` suffix in both `task_id` and the output
prefix to keep both records.

**Note on parallel launches.** A transient AWS `Invalid IAM Instance Profile name` on a
parallel launch is the IAM-propagation race (spore-host/spawn#572), not a recipe fault —
re-run.
