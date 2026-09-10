---
tool: ambertools
tool_version: "26.0"
env: md
image: quay.io/aarchsci/md@sha256:1ee941664add6f83b367c012d0cc670ffc837e83ee491125993de72e88c22ab9
spawn_version: 0.104.0
---
# AmberTools — build a peptide, integrate it, conserve its energy

`tleap` builds a capped alanine dipeptide from the ff14SB force field; `sander` runs a short in-vacuo NVE trajectory. The check is that the total energy stays conserved — a physical law the integrator either obeys or doesn't.

> **What this covers.** `sander` (AmberTools' serial MD engine) on a 22-atom peptide for 20 steps — proof the ff14SB kernels and the Fortran integrator are numerically correct on Graviton4. Not `pmemd` (licence-gated, never in AmberTools), explicit solvent, or long trajectories.

## Run it

```bash
tleap -f build.in                                          # ACE-ALA-NME, ff14SB → parm7 + rst7
sander -O -i md.in -p sys.parm7 -c sys.rst7 -o md.out      # 20-step in-vacuo NVE
```

One task, two subcommands of the same suite. The ff14SB force field ships inside the ambertools package, so nothing is staged — the image digest is the only pin.

## Make it yours

| In the recipe | Swap for | What to know |
|---|---|---|
| ACE-ALA-NME (22 atoms), built inline | your own system (`.pdb` → `tleap`) | the peptide is a hand-checkable NVE system, not a limit on `sander`. |
| in-vacuo, no thermostat | explicit solvent + a thermostat | NVE (no thermostat) is what makes energy conservation an *exact* check; add a thermostat and you check temperature control instead. |

`sander` is deterministic here — **nothing is determinism scaffolding**. **Leave the fixture:** NVE conservation is exact-or-wrong for a correct integrator at any size, and 22 atoms make the energy budget hand-auditable. A bigger peptide is a longer run, not a more legible one. Leave-it.

## Shape, size, cost

One task, `c8g.large` (2 vCPU / 4 GiB), TTL 5m, cap $0.02. `tleap` + `sander` take ~1 s on 22 atoms; `sander` is serial, so cores and memory don't bear on correctness. Recorded command window **116s** — boot, Docker install, and the 1.19 GB `md` image pull are the whole task ([why](../../practices/what-this-does-not-cover.md)). **These timings are not compute cost.** See [sizing](../../patterns/sizing.md) for real MD.

<details>
<summary>As shipped: the conservation check, pins, smoke-check table, run + verify</summary>

### The check — a conservation law, not a threshold

In-vacuo NVE forces total energy to be conserved, so the recipe asserts drift ≈ 0 directly. This mirrors aarch.science's `md.smoke.py`, so the result is comparable to what they published for this image. It's cross-checked against `sander`'s own RMS fluctuation figure (computed on a separate code path); the parse stops before `sander`'s `A V E R A G E S` / `R M S FLUCTUATIONS` summary blocks, which reuse the `Etot =` format and would otherwise inflate the drift by ~13 kcal/mol.

This is the free half of AMBER: `pmemd` (the fast production engine) ships **only under the paid Amber licence** and is not in AmberTools at all — the env's own smoke test asserts its absence.

### Pins (data tier: bundled in the image)

| | |
|---|---|
| image | `quay.io/aarchsci/md@sha256:1ee941664add6f83b367c012d0cc670ffc837e83ee491125993de72e88c22ab9` (tag `2026.09.04`, AmberTools 26.0, cosign-signed, `linux/arm64`) |
| input | ff14SB force field, bundled in the ambertools package — nothing staged |

Same `md` image as [gromacs](../gromacs/README.md) and [lammps](../lammps/README.md), under the same digest.

### Smoke check (inside the task; measured before launch)

| observable | assertion | observed | catches |
|---|---|---|---|
| tleap wrote parm7 / rst7 | both non-empty | 19069 B / 814 B | build failure |
| atoms | exactly 22 (ACE-ALA-NME) | 22 | wrong system |
| per-step Etot values | ≥ 2 | 21 | run died early |
| **NVE conserved** | drift < 0.5 kcal/mol over 20 steps | **0.0164** | broken force/integrator |
| Etot negative | first-step total < 0 | −13.3341 | garbage energetics |
| sander's own RMS | Etot RMS fluctuation < 0.5 | 0.0044 | parser vs `sander` disagree |

### Run + verify

```sh
spawn task run --spec recipes/ambertools/01-md.task.json --wait
aws s3 ls s3://scicookbook-942542972736-us-east-1/runs/ambertools/r1/
```

`--wait` exiting 0 does **not** prove the outputs exist (spore-host/spawn#561): the smoke check runs *inside* the task, and the bucket listing is the second half of it. Expect three objects (`md.out`, `tleap.log`, `smoke-check.txt`). Re-run: bump the `-r1` suffix in `task_id` and the output prefix to keep both records.

</details>
