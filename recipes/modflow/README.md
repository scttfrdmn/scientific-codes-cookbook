---
tool: modflow6
tool_version: "6.8.1"
env: geoscience
image: quay.io/aarchsci/geoscience@sha256:f0f72f5b0fe3119ecd1578b5d2e475c438bf716e01c0033adfca89a066ebcb70
spawn_version: 0.126.1
last_verified: 2026-10-09
---
# MODFLOW 6 — groundwater flow on Graviton, against its own closed-form answer

Solves 1-D confined groundwater flow on Graviton4 and checks the heads against the analytical solution, the through-flow against Darcy's law, and the discretisation against its theoretical order. For anyone running groundwater models on ARM.

## Run it

```bash
make stage RECIPE=modflow      # once: the flopy model builder (there is no data to stage)
spawn task run --spec "$(make -s spec RECIPE=modflow)" --wait
make ls RECIPE=modflow

# the model is built in flopy, then solved by the mf6 binary:
flopy.mf6.ModflowGwfdis(gwf, nlay=1, nrow=1, ncol=101, delr=10.0, delc=1.0, top=20.0, botm=0.0)
flopy.mf6.ModflowGwfnpf(gwf, icelltype=0, k=10.0)                  # icelltype 0 = confined
flopy.mf6.ModflowGwfchd(gwf, stress_period_data=[[(0,0,0), 10.0], [(0,0,100), 2.0]])
sim.write_simulation(); sim.run_simulation()                       # -> mf6 6.8.1
```

## Make it yours

| In the recipe | Swap for | What to know |
|---|---|---|
| the 1-D confined model | your own grid | **this geometry is chosen because its answer is known.** Constant transmissivity with no sources gives a linear head profile, which the finite-difference stencil reproduces *exactly* — so the check is a reference, not a tolerance. |
| `icelltype=0` | `1` (convertible) | confined keeps transmissivity head-independent, which is what makes the closed form exact. Unconfined makes the equation non-linear and the analytical comparison no longer applies. |
| `outer_dvclose=1e-11` | your tolerance | **keep it far below what you assert.** At 1e-11 the solver error cannot be mistaken for discretisation error — the same separation the [PETSc ladder](../petsc/README.md) needs. |
| the recharge ladder | your own refinement | assert the measured **order**, not an error magnitude: a magnitude depends on your grid, the order is the method's property. |
| `ModflowGwfrch` (list) | `ModflowGwfrcha` (array) | the list package takes a per-cell **rate**; MODFLOW multiplies by cell area itself. |
| nothing staged | your model files | **modflow6's conda package ships no example problems** — only `mf6`, `libmf6.so` and `get-modflow`, confirmed by probing the image. Build with flopy or stage your own. |

**Leave the fixture.** 101 cells is 25 unknowns' worth of science, and the point is that the answer is known in closed form — no larger grid gives you that. **Scale it** once it passes; this env also carries obspy, and `get-modflow` fetches the USGS example suite if you want published benchmarks.

## Shape, size, cost

One task on `m8g.large` (2 vCPU / 8 GiB), TTL 30m as a **backstop** with `cost_limit` $0.10 as the real guard. Five 1-D solves of at most 201 cells are milliseconds; the recorded **58 s** is boot, Docker install and image pull, so **these timings are not compute cost** ([layout](../../patterns/layout-and-effective-cost.md)).

<details>
<summary>As shipped: an exact analytical reference, Darcy's law to machine precision, conservation from the binary budget, and a measured second-order rate</summary>

### The checks

| observable | assertion | observed |
|---|---|---|
| staged bytes | match pins | model.py matches |
| mf6 / flopy | read from the running binary, not the env lock | **6.8.1** / 3.11.0 |
| fixed-head cells hold | structural | 10.000000 / 2.000000 |
| **heads vs analytical line** | **max abs error < 1e-8** | **1.510e-14** |
| **through-flow vs Darcy** | **relative error < 1e-9** | **0.000e+00** (1.600000 m³/d both) |
| budget records present | else the conservation check is vacuous | `FLOW-JA-FACE`, `CHD` |
| gross boundary flow | > 0 | 3.200000 m³/d |
| **water conserved** | **net/gross < 1e-10, from the binary budget** | **2.776e-15** |
| mf6's printed discrepancy | \|%\| < 1e-6 | -0.000e+00 |
| **completion markers** | **listing carries both closing markers** | present |
| **convergence order** | **1.8 < p < 2.2 on every rung** | **1.998, 2.000, 2.000** |
| conservation at every rung | net/gross < 1e-10 | 1.6e-15 … 1.3e-14 |

### Why this geometry: the answer is known, so there is nothing to calibrate

With constant transmissivity and no sources, the governing equation is `d²h/dx² = 0`, so the head
is **linear** between the two fixed-head cells. The three-point finite-difference stencil
reproduces a linear function exactly — this is not "accurate to within a tolerance", it is an
identity, and MODFLOW must return the analytical line to solver precision. It does, to
**1.510e-14** over a 101-cell grid.

The flux follows in closed form too. `Q = T(h_L − h_R)/L = 200 × 8 / 1000 = 1.6 m³/d`, and mf6's
own through-flow is **1.600000** — relative error **exactly zero**. Two independent consequences
of Darcy's law, both reproduced rather than bounded.

### Conservation is read from the binary budget, not the printed one

A groundwater solve must conserve water, and mf6 reports a discrepancy itself — but the listing
renders `PERCENT DISCREPANCY` to a couple of decimals, so an error below roughly 1e-4 is
**invisible** in the printed figure. The identity therefore reads the binary cell-budget file,
where every boundary flow carries full precision, and sums them: inflow and outflow net to
**2.776e-15 of 3.200000 gross**.

`FLOW-JA-FACE` is internal cell-to-cell flow and cancels on its own, so it is summed separately
and reported rather than mixed into the boundary total — otherwise a real boundary imbalance
could be masked by the internal terms. The gross flow is asserted positive first, because a
model with no flow at all conserves water trivially and would prove nothing.

### Assert the order, not the error

A linear profile being exact is a strong correctness check that says **nothing about the
discretisation** — any consistent scheme gets it right. So the second case drives the same
solver with sinusoidal recharge, whose closed form is not a polynomial:

```text
T d²h/dx² = -R₀ sin(πx/L)   ->   h = h_linear + (R₀L²)/(Tπ²) · sin(πx/L)
```

with the sine vanishing at both fixed-head cells. The error must then fall as O(h²):

```text
dx 40.000  max|err| 6.659e-04
dx 20.000  max|err| 1.667e-04      order 1.998
dx 10.000  max|err| 4.167e-05      order 2.000
dx  5.000  max|err| 1.042e-05      order 2.000
```

A correct-but-first-order implementation passes any single-resolution tolerance you pick and
fails this. The amplitude is **0.507 m** on an 8 m head drop — deliberately modest: the solve is
confined, so transmissivity is head-independent and a larger perturbation would still be
numerically valid, it would just describe an aquifer pressurised far above its own top. The
problem is linear in the recharge rate, so the measured order is unaffected either way.

### Completion sentinels, and the one that does not exist

mf6 must be shown to have *finished*, not merely to have left a parseable head file. Three
independent signals, each measured present rather than assumed:

1. flopy's own success flag — mf6's verdict, relayed; the run aborts on `not ok`.
2. `TOTAL SIMULATION TIME` in the listing, written in the closing timing block.
3. `PERCENT DISCREPANCY` in the listing, written only once the budget is closed out.

**The obvious sentinel is not available.** An earlier version asserted the phrase
`Normal termination of simulation` in the listing. In 6.8.1 that phrase is not in the listing at
all, and flopy returns an **empty stdout buffer** under `silent=True`, so it was not reachable
that way either — the check failed a run that was entirely correct. Asserting a string the tool
never emits is the mirror of a check that passes for the wrong reason, and costs just as much.

### Pins

| | |
|---|---|
| image | `quay.io/aarchsci/geoscience@sha256:f0f72f5b…` — mf6 6.8.1, flopy 3.11.0, obspy 1.5.1, numpy 2.5.3, python 3.14.8 |
| model | built in flopy at run time; `model.py` is staged and pinned by sha256 |

**Nothing scientific is staged, because there is nothing to stage.** The conda package installs
only `bin/mf6`, `lib/libmf6.so` and `get-modflow` — no example problems — established by probing
the image before designing the recipe, since what a package installs is a property of the build.
That turns out to be the better position: a problem built on purpose can have a closed-form
answer, which no bundled dataset would have given.

The builder travels as a **staged, pinned input** rather than inline in the spec, because a spawn
task command rides in EC2 user data capped at **16,384 bytes**.

The version is read from the **running binary**, not the env lock — a lock file in git and an
image in a registry are two different artifacts.

cosign-verified against `playgroundlogic/aarchsci`; the signature covers the **manifest-list**
digest, so verify the tag and pin the arm64 digest (the list also carries an `unknown/unknown`
attestation entry, which is not an image).

### Run + verify

```sh
make stage RECIPE=modflow
spawn task run --spec "$(make -s spec RECIPE=modflow)" --wait
aws s3 cp "s3://$(make -s print-bucket)/runs/modflow/r1/score.tsv" -
```

Fails on a pin mismatch, a head that differs from the analytical line by more than 1e-8, a
through-flow that disagrees with Darcy's law, a budget that does not balance, a missing closing
marker, or a measured order outside ~2 — but check the bucket regardless
([exit 0 isn't proof](../../practices/container-path.md)). mf6's own listings are staged out for
both the coarse and finest grids, since a failed solve explains itself there and spawn uploads a
tool's log only when the spec declares it an output.

### Not covered

Transient flow and storage, unconfined/convertible cells (which make the equation non-linear and
void the closed form), 2-D and 3-D grids, DISV/DISU unstructured discretisations, wells, rivers,
drains and evapotranspiration, solute transport (GWT) and the flow–transport exchange, parallel
MPI solves, and the USGS `modflow6-examples` benchmark suite — `get-modflow` is in this image and
would fetch it, which is the obvious next recipe and would turn this into a published-benchmark
reproduction rather than a manufactured one.

</details>
