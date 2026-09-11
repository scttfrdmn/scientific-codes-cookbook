---
tool: paraview
tool_version: 6.1.1
env: viz
image: quay.io/aarchsci/viz@sha256:2539c1e42d24695785a2510e4fd041e547078209513b0aa201b9362b00b438f6
spawn_version: 0.104.0
---
# ParaView (viz env) — headless offscreen rendering

`pvbatch` renders a scientific dataset to a PNG headlessly — no GPU, no display — the batch-visualization path for a server or CI.

> **What this covers.** Headless ParaView 6.1.1 (`pvbatch`) rendering a small built-in dataset via the CPU software rasteriser — proof the whole offscreen pipeline works on Graviton4 (a real achievement for this env; see below). Not a benchmark, and no large real mesh, client/server, or GPU path.

## Run it

```python
# pvbatch script. Headless: the recipe starts Xvfb + sets LIBGL_ALWAYS_SOFTWARE=1 first
# (load-bearing — no GPU, no display; see below), then:
from paraview.simple import *
Wavelet()                                    # analytic scalar field, generated in memory
Contour(Isosurfaces=[150.0])                 # → 3034 points, 5768 cells (deterministic)
SaveScreenshot("render.png", ImageResolution=[400, 300])   # GLX + llvmpipe, no GPU
```

One `pvbatch` invocation in one task. The dataset is synthetic, so nothing is staged — but the render path is not trivial (below).

## Make it yours

| In the recipe | Swap for | What to know |
|---|---|---|
| the built-in `Wavelet` source | your own dataset (`.vtu`, `.vti`, …) | staged through S3; the analytic Wavelet field is what gives the render a *deterministic* contour to assert (3034 points), which a real mesh won't. |
| the headless GLX + `llvmpipe` render path | leave it | **load-bearing, baked into the image** — conda-forge ParaView on Graviton has no OSMesa and no GPU for EGL, so the only path that works is GLX against `Xvfb` with `LIBGL_ALWAYS_SOFTWARE=1`. The recipe starts `Xvfb` for exactly this reason. |

Deterministic — **nothing is determinism scaffolding**. **Leave the fixture:** the contour counts are an exact invariant of the analytic field and the render is CPU-bound at any size; a large mesh is a longer run, not a more legible one. Leave-it. (A GPU render would be Round Two, but Graviton has no GPU to want here.)

## Shape, size, cost

One task, `c8g.large` (2 vCPU / 4 GiB), TTL 5m, cap $0.02. The render is ~1 s on the CPU rasteriser; there is no GPU to want. Recorded command window **89s** — boot, Docker install, and the 0.84 GB `viz` image pull are the whole task ([why](../../practices/what-this-does-not-cover.md)). **These timings are not compute cost.**

<details>
<summary>As shipped: why the render path is hard, the two checks, pins, smoke check, run + verify</summary>

### Why this is harder than it looks

"ParaView imported" is spectacularly insufficient here, which is why the check runs the whole pipeline. A bare conda-forge ParaView on this platform **cannot load its own libraries** without a specific `hdf5` pin (the feedstock under-links `libhdf5` and the solver otherwise picks an `hdf5 2.2` build whose soname ParaView's binaries can't find — an upstream bug, not an arm64 one; the `viz` env pins `hdf5=1.14.*`), and **cannot render** via the usual headless paths (no OSMesa in conda-forge, and EGL needs a GPU device node Graviton lacks). Both fixes are baked into the pinned image; the recipe proves the result renders end to end as an unprivileged user with no display.

### The two checks

- **Contour point/cell counts are the conservation-identity equivalent for a render.** The `Wavelet` field is analytic and the isosurface at 150.0 is deterministic, so **3034** points and **5768** cells are an exact algorithmic invariant of this pinned image — caught before a single pixel is drawn. If VTK's flying-edges on Graviton produced anything else, that's a real correctness failure.
- **The image is verified by something that did not draw it.** `pvbatch` writing a file proves nothing — a silent render failure still touches a PNG. So `pillow` reads it back: **796 distinct colours** (a blank frame has one) and a background filling **79.8%** of the frame. Both reproduce aarch.science's published D3 render exactly — the [reproduce-a-published-result](../../practices/reference-from-tests.md) move.

### Pins (data tier: synthetic / in-image)

| | |
|---|---|
| image | `quay.io/aarchsci/viz@sha256:2539c1e42d24695785a2510e4fd041e547078209513b0aa201b9362b00b438f6` (tag `2026.09.04`, ParaView 6.1.1 + vtk + mesa/llvmpipe + Xvfb + pillow, cosign-signed, index has one `linux/arm64` manifest) |
| input | ParaView `Wavelet` synthetic source, **generated in memory** — nothing staged |

### Smoke check (inside the task; measured before launch)

| observable | assertion | observed |
|---|---|---|
| **contour points / cells** | exactly 3034 / 5768 (flying-edges isosurface at 150.0) | 3034 / 5768 |
| PNG exists / size | non-empty > 1000 B, exactly 400×300 | 23338 B, (400,300) |
| real geometry | > 50 distinct colours (published D3: 796; blank = 1) | 796 |
| not blank | < 98% of pixels one colour | 79.8% |
| luminance range | > 40 (a flat frame is ~0) | 6..225 |

### Run + verify

```sh
make run RECIPE=paraview
make ls RECIPE=paraview
```

a completed run does **not** prove the outputs exist — an exit code says the command ran, never that its output is real; the smoke check runs *inside* the task, and the bucket listing is the second half of it. Expect three objects — `render.png`, `smoke-check.txt`, `pvbatch.out`; the PNG is the artifact worth looking at. Re-run: `make run` launches a fresh task each time and overwrites this prefix — no spec edit needed.

</details>
