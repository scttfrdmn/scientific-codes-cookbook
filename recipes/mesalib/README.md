---
tool: mesalib
tool_version: "26.2.4"
env: viz
image: quay.io/aarchsci/viz@sha256:dda63dc2dcbd830258017f93df6cafe37ff3afbfc0a843fd7690bd5e6be166a7
env_x86_64: viz-0fe5dabecafa
spawn_version: 0.127.0
last_verified: 2026-10-11
last_verified_x86_64: 2026-10-17
last_verified_amd: 2026-10-18
---
# Mesa llvmpipe — headless GL, checked to the pixel

Renders flat-shaded geometry with software OpenGL on Graviton4 and asserts exact pixel counts, not image similarity. For anyone doing offscreen rendering on a box with no GPU and no display.

## Run it

```bash
make stage RECIPE=mesalib     # once: the checks only — the geometry is two quads
spawn task run --spec "$(make -s spec RECIPE=mesalib)" --wait
make ls RECIPE=mesalib

Xvfb :99 -screen 0 1024x768x24 &      # REQUIRED — see below; without it VTK segfaults
export DISPLAY=:99
python3 -c "
import vtk; rw = vtk.vtkRenderWindow(); rw.SetOffScreenRendering(1); rw.Render()
print(rw.ReportCapabilities())"       # -> Mesa / llvmpipe (LLVM 23.1.2, 128 bits)
```

## Make it yours

| In the recipe | Swap for | What to know |
|---|---|---|
| `Xvfb` + `DISPLAY` | rendering with no X server | **you can't, in this image.** `mesalib` ships GLX but **no `libOSMesa`**, and EGL lacks `EGL_EXT_device_base`. With `DISPLAY` unset, VTK tries all three backends and **segfaults** — see the error chain below so you can recognise it. |
| `SetMultiSamples(0)` | anti-aliasing on | AA blends edge pixels, so exact colour counts stop being exact. Off is what makes this checkable. |
| `LightingOff()` + `SetAmbient(1)` | real shading | flat ambient colour removes any dependence on light position, so a quad is one RGB value. Turn lighting on and you are back to comparing images. |
| asserting `llvmpipe` | accepting any renderer | **keep it.** Every exactness claim here rests on deterministic software rasterization; a different renderer could anti-alias or reorder and still look right. |
| two quads | your own scene | this checks the **GL stack**. [paraview](../paraview/README.md) in this same env does real post-processing on up to 135M points and renders through this exact path. |

**Leave the fixture.** Nothing is fetched and no scene file is read: two axis-aligned quads under an orthographic camera, so every expected pixel count is an integer derived from the viewport. **Scale it** by raising the viewport — coverage is resolution-invariant and staging checks the sizes still divide exactly.

## Shape, size, cost

One task on `m8g.large` (2 vCPU / 8 GiB), TTL 30m as a **backstop** with `cost_limit` $0.12 as the real guard. All rendering is **33 s** of a 1m43s window (3 s of that is waiting for Xvfb); 39 s is installing Docker and 30 s pulling the image ([layout](../../patterns/layout-and-effective-cost.md)). No GPU is involved at any point.

<details>
<summary>As shipped: exact pixel counts, the renderer asserted, a depth test with an inverting control, and why the headless probe is a separate process</summary>

### The checks

| observable | assertion | observed |
|---|---|---|
| staged bytes | match pin | 1 of 1 |
| VTK / numpy | read from the running install | **9.7.0** / 2.5.4 |
| **`GL_VENDOR`** | **`== "Mesa"`** | **Mesa** |
| **`GL_RENDERER`** | **contains `llvmpipe`** | **llvmpipe (LLVM 23.1.2, 128 bits)** |
| `GL_VERSION` | names Mesa | 4.6 (Core Profile) Mesa 26.2.4 |
| render window class | — | `vtkXOpenGLRenderWindow` |
| **full-viewport quad** | **exactly 1 colour, all pixels** | **1 colour, 4096 of 4096** |
| **half-viewport quad** | **exactly `N²/2`, 2 colours** | **2048 / 8192 / 32768** at 64, 128, 256 |
| **depth, red nearer** | **exactly 2/3 red, 1/3 green** | **6144 / 3072**, and 24576 / 12288 |
| **control: depths swapped** | **counts must invert** | **3072 / 6144**, and 12288 / 24576 |
| **determinism** | **3 fresh contexts, one sha256** | **identical** (`8d4a68bc…`) |
| display-free backends | *diagnostic, **not** asserted* | all three fail; segfault |

### Rendering is exactly checkable, which is the whole point

Images are usually verified by eye or by a heuristic. The [paraview](../paraview/README.md)
recipe in this same env is honest about using one — ">50 colours, <98% one colour" — and that is
the right call for a shaded isosurface. But the GL layer underneath it can be pinned down
precisely, and this page does that, because three choices remove every source of fuzz:

- **orthographic camera**, so a quad's screen coverage is exactly its world extent
- **`LightingOff()` with `SetAmbient(1.0)`**, so colour cannot depend on light position
- **`SetMultiSamples(0)`**, so no edge pixel is a blend of two colours

The result is integers. A full-viewport red quad is `[255,0,0]` over **all 4096** pixels and
nothing else; a half-viewport quad is **exactly** `N²/2` red and `N²/2` black, at 64², 128² and
256². Two distinct colours, never three — a third would mean an anti-aliased edge had crept back.

### The renderer is asserted, for the same reason an MPI recipe counts its ranks

`GL_VENDOR` must be `Mesa` and `GL_RENDERER` must contain `llvmpipe`. This is not decoration:
every claim above — one colour per quad, exact counts, a reproducible digest — depends on a
deterministic software rasterizer. A context serviced by something else would still produce a
perfectly plausible image while quietly invalidating the reasoning, which is the same silent
failure shape as a "parallel" run that is really serial ([rank
count](../../practices/mpi-rank-count.md)).

### The depth test, and the control that stops it being vacuous

**The obvious depth check proves nothing.** Two full-viewport quads at different z, asserting the
nearer colour wins, passes identically with depth testing *disabled* — whichever is drawn last
simply wins. So the quads here only partly overlap: red spans x ∈ [−1, ⅓], green spans x ∈ [−⅓, 1],
and they share the middle third. Each colour's pixel count then reports who owned the overlap:

```text
red at z=0 (near), green at z=-1 (far)      red 6144   green 3072      (2/3, 1/3)
CONTROL: depths exchanged                   red 3072   green 6144      inverted
```

Both at 96² and again at 192². If the counts did **not** invert, the first result would not have
been about the depth buffer at all, and the run says so in those words. Staging separately checks
that the quads genuinely overlap and that each is *wider* than the overlap — a quad contained
within it would make the winner unobservable.

### The headless finding, and why it is a diagnostic rather than a check

**`mesalib` being installed does not give you a display-free GL context.** With `DISPLAY` unset,
VTK walks three backends and fails all of them:

```text
vtkXOpenGLRenderWindow   bad X server connection. DISPLAY=
vtkEGLRenderWindow       Could not initialize a device. EGL_EXT_device_base,
                         EGL_EXT_platform_device and EGL_EXT_platform_base required
vtkOSOpenGLRenderWindow  libOSMesa not found. It appears that OSMesa is not installed in
                         your system. Please install the OSMesa library from your
                         distribution's package manager.
Segmentation fault (core dumped)
```

Two things make this worth recording rather than just working around. The last message sends you
to "your distribution's package manager", which is **not** how this env is built — conda-forge's
`mesalib` simply does not carry `libOSMesa`, so the advice points away from the fix. And the
failure is a **segfault, not an exception**: `except BaseException` does not catch it.

That is why the probe runs as a **separate process** before the checks. Putting it inside the main
flow is the obvious implementation and would have killed the whole run — a diagnostic must never be
able to do that. It is also reported rather than asserted on purpose: if aarchsci adds `osmesa`,
display-free rendering starts working, and a check demanding that it fail would break exactly when
the platform improved. `backends.txt` is staged out either way.

### Pins

| | |
|---|---|
| data | **none.** Two axis-aligned quads; every expected count is an integer from the viewport size |
| checks | `identities.py`, pinned by sha256 |
| image | `quay.io/aarchsci/viz@sha256:dda63dc2dcbd830258…` — mesalib 26.2.4 (LLVM 23.1.2), VTK 9.7.0 via ParaView 6.2.0, xorg-xvfb-server, numpy 2.5.4, python 3.14.8 |

cosign-verified against `playgroundlogic/aarchsci`; the signature covers the **manifest-list**
digest, so verify the tag and pin the arm64 digest.

**x86_64** (`m8i.large`, 2026-10-17): every identity holds, from the env's linux-64 twin
(`envs-x86_64/viz-0fe5dabecafa`, 231 of 244 versions identical; mesalib, VTK and numpy among
them). The one difference is llvmpipe's vector width, `256 bits` against Graviton4's `128 bits`,
and it moves no pixel: the render sha256 is the same `8d4a68bc…` on both chips. Every count is an
integer from fixed geometry, so none of these is a path. With no `DISPLAY`, x86 also has no
display-free backend (`libOSMesa not found`), same as arm64.

### Run + verify

```sh
make stage RECIPE=mesalib
spawn task run --spec "$(make -s spec RECIPE=mesalib)" --wait
aws s3 cp "s3://$(make -s print-bucket)/runs/mesalib/r1/score.tsv" -
```

Fails on a pin mismatch, a vendor that is not Mesa, a renderer that is not llvmpipe, a flat quad
that is not exactly one colour, a coverage count that is not exactly `N²/2`, a depth split that is
not exactly 2/3–1/3, a control that fails to invert, or three renders that do not hash identically
— but check the bucket regardless ([exit 0 isn't proof](../../practices/container-path.md)).
`backends.txt` and `xvfb.log` are staged out so a failure in the GL setup is diagnosable without a
rerun.

### Not covered

**Anything that looks like a real visualization.** No shading, no textures, no transparency, no
volume rendering — all of which are what people use GL for, and none of which reduce to an integer
pixel count. [paraview](../paraview/README.md) renders a real shaded isosurface through this same
stack; its image check is a heuristic, and deliberately so.

**Shader compilation**, beyond whatever VTK's default mappers emit. **Anti-aliasing**, switched off
by construction here — its edge-pixel behaviour is untested. **The EGL and OSMesa paths**, absent
from this image, so nothing here says whether they would be exact if present. **Performance**: no
pixel-throughput or scaling measurement, and llvmpipe's thread count is left at its default.
**Large framebuffers** — the biggest viewport is 256², so nothing speaks to memory limits or tiling
at render sizes anyone would actually publish.

</details>
