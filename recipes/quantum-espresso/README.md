---
tool: quantum-espresso
tool_version: 7.5
env: dft
image: quay.io/aarchsci/dft@sha256:7574f6b15d6b0ec2d1bc27ebaabf97298e1ac619711754eb10da8b1f54600d03
spawn_version: 0.104.0
---
# Quantum ESPRESSO — the silicon lattice constant, cross-checked against GPAW

Quantum ESPRESSO computes bulk silicon's equilibrium lattice constant from a plane-wave PBE equation of state, and it agrees with GPAW — an independent plane-wave code in the same env — on the identical system. For anyone who runs QE and wants it on Graviton.

> **What this covers.** QE 7.5 (`pw.x`) and GPAW, both PBE, a 5-point equation of state on a 2-atom Si cell (SSSP ultrasoft pseudopotential vs PAW datasets), reproducing the all-electron reference a₀ and agreeing with each other. Not a benchmark; a small legible cell, not a convergence study.

## Run it

```bash
# pw.x SCF on bulk Si — PBE, SSSP pseudopotential bundled in the dft env
pw.x -in si.scf.in   # &system ibrav=2 celldm(1)=10.26 ecutwfc=40 … K_POINTS 8 8 8
```

The recipe drives five such SCFs across lattice constants (through ASE, so QE and GPAW see a byte-identical cell), fits the equation of state, and reads off a₀ — for each code.

## Make it yours

| In the recipe | Swap for | What to know |
|---|---|---|
| 2-atom Si diamond cell | your structure | built in-code by ASE, so QE and GPAW get identical geometry — the cross-check depends on that. |
| PBE + SSSP `Si.pbe-n-rrkjus` | your functional / pseudopotential | **PBE is forced by the bundled SSSP** (no LDA Si in-env); GPAW is run in PBE to match, since a cross-check must compare like with like ([cross-checks](../../practices/cross-checks.md)). |
| `ecutwfc=40 Ry`, `8×8×8` k-points | your convergence | converged for Si a₀ here; a real study sweeps them. |

**Leave the fixture:** Si is the canonical DFT test case and a₀ is a real physical number with a published reference. **Scale it** to your material — the two-code, one-a₀ pattern travels; the cell doesn't need to be bigger to be legible.

## Shape, size, cost

One task, `c8g.large` (2 vCPU / 4 GiB — the two vCPUs are the two MPI ranks), TTL 8m, cap $0.04. Each QE SCF is ~3 s; the ten EOS points (five per code) are ~1 min. Boot and the 1.25 GB `dft` image pull are the rest. **These timings are not compute cost.**

**Sizing:** the 2-atom cell is CPU-light; a real cell scales with atoms × k-points, and QE's plane-wave SCF is MPI-parallel — the [scaling knee](../../patterns/sizing.md) is where more ranks stop paying. Size a real system on its cell, not this one.

<details>
<summary>As shipped: the two-code cross-check, why energy isn't compared, pins, smoke check, run + verify</summary>

### Why the lattice constant, not the energy

QE and GPAW both do plane-wave PBE DFT, but their **total energies are not comparable**: QE's ultrasoft pseudopotential and GPAW's PAW datasets carry different energy zeros. Measured here, the same Si cell gives **−310.7 eV** (QE) and **−10.6 eV** (GPAW) — not a disagreement, a different reference point. Comparing them would be the DF-vs-exact trap that once made two correct quantum-chemistry codes look 2.4e-5 Ha apart ([cross-checks](../../practices/cross-checks.md)). The **equilibrium lattice constant a₀** is formalism-independent — it's the minimum of E(V), where the constant offset differentiates away — so it is what the comparison can honestly claim.

### Two agreements, two justifications (they are not the same band)

- **QE reproduces a published reference** — its own leg, checkable alone: a₀(QE) = **5.4697 Å** against the all-electron PBE reference **5.468 Å** (the Δ-project value the SSSP protocol validates against) — a **0.0017 Å** deviation, B₀ = 88.9 GPa vs 88.8. The band (0.015 Å) is what the SSSP-efficiency Si pseudopotential's own verified EOS supports. [reproduce a published number](../../practices/reference-from-tests.md).
- **QE agrees with GPAW** — the cross-code check: |a₀(QE) − a₀(GPAW)| = **0.0059 Å** (GPAW 5.4756 Å). This band (0.02 Å) comes from the *shared problem's* precision — the ultrasoft-vs-PAW formalism difference plus finite cutoff and k-mesh — and is deliberately looser than the reproduction band above; the two justify differently, and the looser one is not the precision of the tighter. The published reference is what guards against both codes converging to the same wrong place by a shared choice.

### QE's own identities

SCF converges; `pw.x` reports **2 processor cores**, asserted from its output so a serial build can't masquerade as parallel ([rank-count guard](../../practices/mpi-rank-count.md)); and the diamond-Si symmetry gives **zero force** exactly.

### Pins (data tier: in-env pseudopotential + in-code cell)

| | |
|---|---|
| image | `quay.io/aarchsci/dft@sha256:7574f6b1…` (`dft` env — QE 7.5 + GPAW 25.7 + SSSP 1.1.2, cosign-signed, `linux/arm64`) |
| pseudopotential | SSSP efficiency `Si.pbe-n-rrkjus_psl.1.0.0.UPF` (PBE), bundled in the env at `/opt/conda/share/sssp/` — nothing staged |
| structure | 2-atom Si diamond, built in-code by ASE — nothing staged |

### Smoke check (inside the task; measured before launch)

| observable | assertion | observed |
|---|---|---|
| qe_mpi_ranks | exactly 2 | 2 |
| qe zero force | max \|F\| < 1e-3 eV/Å (symmetry) | 0.0 |
| QE a₀ vs AE-PBE reference | \|a₀ − 5.468\| < 0.015 Å (reproduction) | 0.0017 |
| QE B₀ | 80–95 GPa (ref 88.8) | 88.9 |
| QE↔GPAW a₀ | \|Δa₀\| < 0.02 Å (cross-code) | 0.0059 |

### Run + verify

```sh
make run RECIPE=quantum-espresso
make ls  RECIPE=quantum-espresso
```

The smoke check runs inside the task; the bucket listing is the second half ([exit 0 isn't proof](../../practices/container-path.md)). Expect `smoke-check.txt` and `espresso.pwo` (the QE output, showing the rank line and the SCF). Re-run: `make run` launches a fresh task and overwrites this prefix — no spec edit needed.

</details>
