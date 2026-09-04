# AmberTools — build a capped alanine, integrate it, and conserve its energy

One task. `tleap` builds a capped alanine dipeptide from the ff14SB force field and
`sander` runs a short in-vacuo NVE trajectory, and the smoke check confirms the total
energy is conserved.

> **What this recipe does and does not cover.** It runs `sander` (AmberTools' serial MD
> engine) on a 22-atom peptide for 20 steps — enough to prove the ff14SB force field
> and the Fortran integrator are numerically correct on Graviton4. It is not a
> benchmark and does not exercise `pmemd` (which is licence-gated and **never** in
> AmberTools), explicit solvent, or long trajectories.

## Why one task, and why nothing is staged

AmberTools is one tool (a suite sharing one image); this is one `tleap` + one `sander`
invocation in sequence. The ff14SB force field ships **inside** the ambertools package
(`dat/leap/cmd/leaprc.protein.ff14SB`), and the system is built from a three-residue
sequence written inline, so there is **no input to stage** and no `stage-inputs.sh`; the
image digest is the only pin.

## The check: a conservation law, not a threshold

In-vacuo NVE — no thermostat, no barostat — means the total energy **must** be
conserved: that is a physical law the integrator either obeys or does not. So the
recipe asserts conservation directly (energy drift over the trajectory ≈ 0), which is
a much stronger statement than "it ran" — it says the ff14SB force kernels and the
Verlet integrator are numerically correct on aarch64, not merely that they loaded. This
mirrors aarch.science's `md.smoke.py`, so the result is comparable to what they
published for this image.

## Pins

| | |
|---|---|
| image | `quay.io/aarchsci/md@sha256:1ee941664add6f83b367c012d0cc670ffc837e83ee491125993de72e88c22ab9` |
| | tag `2026.09.04`, AmberTools 26.0 (`sander`, `tleap`; **no** `pmemd`), cosign-signed, `linux/arm64` |
| input | ff14SB force field, **bundled in the image** — nothing staged |

**Data tier: bundled in the image.** ff14SB ships inside the pinned ambertools package,
so the image digest is the input pin.

The image is aarch.science's curated `md` env (gromacs + lammps + ambertools) — the same
image `recipes/gromacs` and `recipes/lammps` use, under the same digest. This recipe
invokes only `tleap` and `sander`.

## Smoke check

Measured in this image, on this input, before any launch. The conservation check is a
physical law; the rest are exact shape.

| observable | assertion | observed |
|---|---|---|
| tleap wrote parm7 / rst7 | both non-empty | 19069 B / 814 B |
| atoms in system | exactly 22 (ACE-ALA-NME in ff14SB) | 22 |
| per-step Etot values | ≥ 2 | 21 |
| **NVE conserved** | **energy drift < 0.5 kcal/mol over 20 steps** | **0.0164** |
| Etot negative | first-step total energy < 0 | −13.3341 |
| sander's own RMS | Etot RMS fluctuation < 0.5 (sander's independent figure) | 0.0044 |

Two of these earn their place:

**NVE conservation is the headline** — drift of 0.016 kcal/mol over 20 fs on a total of
−13.3 kcal/mol is conservation to better than one part in 800, which says the force and
integration kernels are sound. A broken force term shows up as drift long before it
shows up as a wrong absolute energy.

**Cross-checked against sander's own RMS fluctuation.** sander independently reports the
RMS fluctuation of the total energy (0.0044), computed separately from our parse of the
per-step values. Two figures for the same physical quantity, from different code paths,
both small — if they disagreed, the parser would be the first suspect. (The parse
deliberately stops before sander's trailing `A V E R A G E S` / `R M S FLUCTUATIONS`
summary blocks, which are in the same `Etot =` format and would otherwise inflate the
drift by ~13 kcal/mol — an artifact, not physics.)

## The half-complete row, stated plainly

This recipe can only ever be half of AMBER. `pmemd`, Amber's fast production MD engine,
ships **only under the paid Amber licence** and is not in AmberTools at all — the smoke
test in the source env even asserts its absence so a future package that bundled it
would be noticed. `sander` here is serial and small. So this proves the free half of
AMBER runs correctly on Graviton4; the licensed engine is out of scope for a public
image.

## Resources, and what the timings mean

2 vCPU / 4 GiB, `c8g` (resolves to `c8g.large`), TTL 5m, cap $0.02. `tleap` + `sander`
take **~1 second** on 22 atoms; `sander` is serial here, so the cores and memory are
irrelevant to correctness and `c8g.large` is the smallest compute box.

**These timings are not compute cost.** Boot, the Docker install, and pulling the
**1.19 GB** `md` image are the whole task; the science is ~1s. The recorded run's
command window was **116s** (20:07:35 → 20:09:31 UTC), and the NVE energy drift came back
0.0164 kcal/mol — bit-identical to the local run. TTL was **retightened from that first
real run**: 10m → **5m** (~2.3× the instance life), `cost_limit` $0.03 → $0.02. A loose
TTL is a larger blast radius, not caution; the recorded run used the original 10m. Disk
is trivial.

## Running it

No `stage-inputs.sh` — the force field is in the image.

```sh
spawn task run --spec recipes/ambertools/01-md.task.json --wait
```

Then **check the bucket**, every time:

```sh
aws s3 ls s3://scicookbook-942542972736-us-east-1/runs/ambertools/r1/
```

`--wait` exiting 0 does **not** prove the outputs exist (spore-host/spawn#561): the
smoke check runs *inside* the task, and the bucket listing is the second half of it.
Expect three objects (`md.out`, `tleap.log`, `smoke-check.txt`).

**Re-running.** `task_id` is fixed, so a re-run overwrites the previous records. Bump
the `-r1` suffix in both `task_id` and the output prefix to keep both. sander overwrites
its own output, so there is no checkpoint guard to defeat.
