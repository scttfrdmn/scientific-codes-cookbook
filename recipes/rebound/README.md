---
tool: rebound
tool_version: "5.0.1"
env: astro
image: quay.io/aarchsci/astro@sha256:9568095c64864dbcf4ad2759ad1aa5457bfa4e12530282378f706c3d394dc753
spawn_version: 0.123.0
last_verified: 2026-10-08
---
# REBOUND — IAS15 conserving energy to 3.4e-15 over 1000 Jupiter orbits

Integrates the outer solar system on Graviton4 and checks the integrator against machine precision, a second method, and Kepler's third law. For anyone running N-body dynamics on ARM.

## Run it

```bash
spawn task run --spec "$(make -s spec RECIPE=rebound)" --wait   # nothing to stage
make ls RECIPE=rebound

import rebound, math
sim = rebound.Simulation(); sim.integrator = "ias15"
sim.add("outer solar system"); sim.move_to_com()      # compiled-in, no download
e0 = sim.energy(); sim.integrate(1e3 * 11.86 * 2 * math.pi)
print(abs((sim.energy() - e0) / e0))                   # 3.365e-15
```

**Nothing is staged and nothing is fetched.** `"outer solar system"` is a dataset compiled into REBOUND, not a JPL Horizons lookup.

## Make it yours

| In the recipe | Swap for | What to know |
|---|---|---|
| `"outer solar system"` | `sim.add(m=..., a=..., e=...)` | build systems directly, or `sim.add("Jupiter")` for a Horizons query — **that one hits the network**, so stage it or keep it out of a recipe. |
| `integrator = "ias15"` | `"whfast"`, `"mercurius"`, `"trace"` | IAS15 is adaptive and near-exact but scales poorly with N; WHFast is fixed-step and fast. **The choice is accuracy vs cost, and this recipe measures the gap.** |
| 1000 Jupiter orbits | your timespan | IAS15's error grows as roughly √N_steps (Brouwer's law), not linearly — long integrations stay good. |
| `sim.dt` (WHFast only) | — | **IAS15 ignores `dt`**; it picks its own. Setting it on IAS15 and expecting an effect is a common wrong turn. |

**Leave the fixture.** The outer solar system over 1000 Jupiter orbits is REBOUND's own test configuration, which is what makes 1e-14 a *published* bound rather than one invented here. **Scale it** by adding bodies or extending the timespan once the check passes — but note IAS15's cost grows with N², so a 1000-body run wants WHFast or Mercurius.

## Shape, size, cost

One task on `c8g.large` (2 vCPU / 4 GiB), TTL 45m, cap $0.10. Both integrations plus the ladder and determinism re-run finish inside a **109 s** window that is mostly boot and image pull, so **these timings are not compute cost** ([layout](../../patterns/layout-and-effective-cost.md)). Single-threaded; REBOUND's N-body kernels do not use multiple cores here.

<details>
<summary>As shipped: a published bound, a 2.7e7x method ladder, a closed-form identity, and why the ladder is load-bearing</summary>

### The checks

| observable | assertion | observed |
|---|---|---|
| particle count | **5** — proves the builtin dataset, not a network fetch | 5 (Sun + 4 giants) |
| **IAS15 energy drift** | **\|ΔE/E\| < 1e-14 over 1000 Jupiter orbits** | **3.365e-15** |
| WHFast energy drift | — (reported) | 9.172e-08 |
| **method ladder** | **WHFast > 100× worse than IAS15** | **2.726e+07×** |
| **Kepler's third law** | a³/P² == (m₁+m₂)/4π², rel < 1e-12 | **1.414e-16** |
| **determinism** | two identical runs, identical energy | identical |

**The 1e-14 bound is REBOUND's, not ours.** `rebound/tests/test_integrator.py` ships inside the
conda package and asserts `fabs((e0-e1)/e1) < 1e-14` for IAS15 on exactly this system and timespan.
Reproducing a published bound beats any band this recipe could invent.

*Correction to an earlier note in this project: the test asserts **1e-14**, not 1e-15. The value
1e-15 appears only on `ias15_timescale`, a different quantity.*

### Why the ladder carries the result

A passing 1e-14 on its own proves nothing about **IAS15** — any sufficiently accurate run satisfies
it, and a bug that silently substituted a different integrator could too. So the same system, same
initial conditions and same duration are integrated with WHFast at `dt = P_Jupiter/100`:

```text
IAS15    3.365e-15     adaptive, 15th order
WHFast   9.172e-08     fixed step, 2nd order symplectic
ratio    2.726e+07
```

**27 million times apart.** That is the integrators being genuinely distinguished, and it is why
the IAS15 number means something. The assertion is the **ordering** (>100×), not a band on WHFast's
error — an ordering spanning seven orders of magnitude is categorical and cannot go flaky, whereas
a band on 9.172e-08 would be a band on a timestep choice.

Worth stating plainly: **WHFast is not wrong here.** It is a symplectic integrator whose energy
error is bounded and oscillatory rather than secular, 1e-8 is excellent for its cost, and it is the
right choice for large-N or long-timespan work. The ladder measures a documented accuracy/cost
trade-off, not a defect.

### Kepler's third law — an identity no integrator can fake

For two bodies with G=1, `P = 2π√(a³/(m₁+m₂))` exactly. Measured **1.414e-16** relative error,
about one ulp. This checks REBOUND's orbital-element machinery independently of the integration, so
a correct integrator sitting behind broken element conversion still fails it.

### Pins

| | |
|---|---|
| image | `quay.io/aarchsci/astro@sha256:9568095c…` (rebound 5.0.1, numpy 2.5.3) |
| input | none — the system is compiled into REBOUND (`src/tools.c` builtin datasets) |

cosign-verified against `playgroundlogic/aarchsci`; the signature covers the **manifest-list**
digest, so verify the tag and pin the arm64 digest.

**The image was checked for staleness, not just authenticity.** The `astro` env lock reports
`Built: 2026.10.08.144529` and the tag was pushed at 14:47:50 the same day, so the lock describing
`rebound 5.0.1` and the image being pinned are the same build. That check exists because
[gsw](../gsw/README.md) was first pinned to an image **eight days older** than the lock read from
git HEAD — selected by sorting `last_modified` as a string, where `"Wed, 30 Sep"` sorts after
`"Thu, 08 Oct"`. cosign verified it happily, because a signature proves an image is authentic and
never that it is the one you meant.

### Two API hazards handled rather than guessed

`particles[i].orbit(primary=...)` and `particles[i].calculate_orbit(primary=...)` are the same call
in different REBOUND generations. The task picks whichever exists via `hasattr` instead of assuming,
so a wrong guess reports rather than discarding a completed integration — the
[version-probe rule](../../practices/container-path.md), which this project paid for once by
throwing away a finished 2.5-minute simulation on a guessed attribute path.

`sim.dt` is set **only** for WHFast. IAS15 chooses its own timestep and ignores it.

### Run + verify

```sh
spawn task run --spec "$(make -s spec RECIPE=rebound)" --wait
aws s3 cp "s3://$(make -s print-bucket)/runs/rebound/r1/score.tsv" -
```

The checks run inside the task and fail it on a wrong particle count, IAS15 drift above 1e-14, the
two integrators failing to separate, a broken Kepler identity, or non-determinism — but check the
bucket regardless ([exit 0 isn't proof](../../practices/container-path.md)).

### Not covered

Collisions and close encounters (`sim.collision`, Mercurius/Trace hybrid switching), non-gravitational
forces via REBOUNDx, variational equations and chaos indicators, and anything needing real ephemerides
— `sim.add("Jupiter")` queries Horizons at run time, which a recipe here may not do.

</details>
