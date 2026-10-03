---
tool: paraview
tool_version: 6.1.1
env: viz
image: quay.io/aarchsci/viz@sha256:2539c1e42d24695785a2510e4fd041e547078209513b0aa201b9362b00b438f6
spawn_version: 0.115.0
last_verified: 2026-10-03
---
# ParaView — contour, integrate and render, up to 135M points with no GPU

Builds an isosurface on four grids from 65³ to 513³, integrates its area and volume, and renders headlessly on CPU. For anyone doing batch `pvbatch` post-processing.

## Run it

```bash
spawn task run --spec "$(make -s spec RECIPE=paraview)" --wait   # 24 s of compute, nothing staged
```

```python
w = Wavelet(); w.WholeExtent = [-256, 256] * 3          # 513³ = 135M points
calc = Calculator(Input=w); calc.Function = "mag(coords)"   # r = |x|
c = Contour(Input=calc, ContourBy=["POINTS", "r"], Isosurfaces=[153.6])
IntegrateVariables(Input=c)                              # Area -> 4πR² to 1.3e-05
```

## Make it yours

| In the recipe | Swap for | What to know |
|---|---|---|
| `r = mag(coords)` on a Wavelet grid | your own field (`OpenDataFile`, XDMF, VTK…) | swapping in real data loses the closed-form answer — keep the sphere rung as the thing that proves the pipeline, then point it at your field. |
| the four-rung ladder | one resolution | **you lose the strongest check** — see below; a single resolution can only support a band. |
| `SaveScreenshot` at 400×300 | your camera and size | rendering is Xvfb + llvmpipe, so a bigger image costs CPU linearly and needs no GPU. |

**Leave the ladder.** The rungs are not four tries at one number — the *ratio between them* is the
assertion, and it cannot be formed from one run. 513³ is also where this stops being a toy:
135,005,697 points, 21 s, 4.1 GiB peak.

## Which box

`c8g.xlarge` (4 vCPU / 8 GiB), sized from the measured **4,185 MiB peak RSS** at the top rung — RAM
picks the box. The whole ladder is 24.2 s: 0.1 s, 0.4 s, 2.7 s, **21.0 s**, which is the ~8×
per-rung growth you expect from 8× the cells. No GPU, no display: Xvfb plus llvmpipe software GL
([what this does not cover](../../practices/what-this-does-not-cover.md)). **These timings are not
compute cost** ([layout](../../patterns/layout-and-effective-cost.md)).

<details>
<summary>As shipped: an integer topological identity, a convergence rate, and why one resolution isn't enough</summary>

### The checks

| observable | assertion | observed |
|---|---|---|
| pvbatch completed | `RENDER_OK` present (last line of the script) | present |
| ladder complete | 4 resolutions | 4 |
| **Euler characteristic** | **V − F/2 = 2 at every resolution** | **2.0, 2.0, 2.0, 2.0** |
| **area convergence** | **error ratio 3.5–4.5 per doubling** | **4.01, 4.01, 4.00** |
| area at 513³ | < 5e-05 relative | 1.33e-05 |
| **volume convergence** | **error ratio 3.5–4.5 per doubling** | **4.01, 4.01, 3.99** |
| volume at 513³ | < 1e-04 relative | 2.53e-05 |
| PNG | 400×300, > 50 colours, < 98% one colour | 440 colours, 80.9% |

```
    grid       points        V        F    chi  area err %   vol err %      s
    65^3      274,625     6918    13832   +2.0     -0.0854     -0.1629    0.1
   129^3    2,146,689    27822    55640   +2.0     -0.0213     -0.0406    0.4
   257^3   16,974,593   111078   222152   +2.0     -0.0053     -0.0101    2.7
   513^3  135,005,697   444846   889688   +2.0     -0.0013     -0.0025   21.0
```

**The Euler characteristic is the only check here with no tolerance at all.** For a closed triangle
mesh E = 3F/2, so χ = V − E + F = V − F/2, and for a topological sphere that is exactly **2** — an
integer, independent of resolution. A surface that leaked through the domain boundary, came back as
two components, or carried unmerged duplicate points would not give 2. It holds at 6,918 points and
at 444,846.

**Asserting the convergence *rate* is what makes this a method check instead of a band.** Marching
cubes on a smooth surface is second-order accurate, so halving the cell size must quarter the
error — observed 4.01, 4.01, 4.00. A correct-but-first-order implementation would sit comfortably
inside any single-resolution tolerance you picked and would fail here immediately. That is the
reason the ladder exists, and the reason "run one resolution and check it's close" is weaker than
it looks: it tests the answer, not the method. The closed-form targets (4πR², 4/3πR³) are what
make the rate computable at all, which is why the field is analytic rather than real data.

A detail worth stating because it looks like a flaw: the isosurface is coloured by `r`, which is
*constant* on its own isosurface, so the 440 distinct colours in the PNG are entirely lighting.
That is fine for what the check claims — a blank fill or a failed render cannot produce a shaded
gradient — but the image is not a data visualisation, it is evidence that the GL path works.

`R` scales with the grid (`R = 0.6n`) rather than staying fixed. Wavelet's point coordinates *are*
its extent indices, so growing the extent at fixed `R` would change the resolution and the geometry
at the same time, and the convergence ratio would mean nothing. This cost one wasted probe: the
first attempt used `R = 100` with extent ±32, where the field never reaches 100, so the contour was
empty and `IntegrateVariables` produced no `Area` array at all.

### Pins (data tier: none / in-task)

| | |
|---|---|
| image | `quay.io/aarchsci/viz@sha256:2539c1e42d24…` (ParaView 6.1.1, cosign-signed, `linux/arm64`) |
| input | the analytic field, built in code — **nothing is staged** |

### Run + verify

```sh
spawn task run --spec "$(make -s spec RECIPE=paraview)" --wait
make ls RECIPE=paraview
```

The smoke check runs inside the task and the bucket listing is the second half of it
([exit 0 isn't proof](../../practices/container-path.md)). Expect `smoke-check.txt`, `iso.json`,
`render.png` and `pvbatch.out` — the last so a run killed at TTL still leaves its evidence.

</details>
