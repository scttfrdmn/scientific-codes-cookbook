---
tool: astra-toolbox
tool_version: "2.5.0"
env: tomography
image: quay.io/aarchsci/tomography@sha256:ea7b4900500f3eb23d3060611f1737175f4a6421cb24b4f29a809765b2754026
gap_x86_64: the aarch.science `tomography` env is arm64-only (manifest list has a linux/arm64 entry and no amd64, checked 2026-10-13) and aarch.* builds ARM only; no x86 build of the same env exists
spawn_version: 0.127.0
last_verified: 2026-10-11
---
# astra-toolbox — CPU tomography against Radon identities

> **The forward model cross-validates; the reconstructions do not.** astra and tomopy project
> the same phantom to within **2.6e-07**. But astra's FBP recovers it at **0.070** relative L2
> where tomopy's best manages **0.850** on the identical sinogram — reported, not asserted,
> and not attributed to either tool. Details below.

Projects and reconstructs a synthetic phantom on Graviton4 with no GPU, checking mass conservation, linearity and recovery against exact ground truth. For anyone doing CT reconstruction on ARM.

## Run it

```bash
make stage RECIPE=tomography     # once: the checks only — the phantom is analytic
spawn task run --spec "$(make -s spec RECIPE=tomography)" --wait
make ls RECIPE=tomography

pid = astra.create_projector("linear", proj_geom, vol_geom)   # CPU; no _CUDA suffix
sid, sino = astra.create_sino(disc, pid)
sino.sum(axis=1)            # the Radon integral — same at every angle, equals the mass
astra.astra_dict("FBP")     # CPU algorithm; FBP_CUDA is the GPU one
```

## Make it yours

| In the recipe | Swap for | What to know |
|---|---|---|
| the `if __name__ == "__main__"` guard | a plain script | **required.** Python 3.14 defaults to `forkserver` on Linux and tomopy calls `mp.Manager()` from library code, so without it `tomopy.project` dies with an error blaming *your* code. Staging asserts the guard is still there. |
| `"linear"` + `"FBP"` | `FBP_CUDA` | this env is **CPU-only** (`use_cuda()` is `False`), which the run asserts — a GPU algorithm would invalidate every timing here. |
| astra for reconstruction | tomopy | on this phantom astra's FBP gets 0.070 and tomopy's 0.850. See below before choosing. |
| `tomopy.recon(filter_name=…)` | — | **`filter_name` has no effect on `algorithm="fbp"`**: ramlak, shepp and parzen give **byte-identical** output. `gridrec` does respond to it. |
| the disc phantom | Shepp-Logan, or your data | a sharp edge rings, which is why the error floors near 0.063 no matter how many angles you add. A smooth phantom converges further. |
| 180 angles | more | the sweep is 45→360; error falls 0.174→0.063 and then stops improving much. |

**Leave the fixture.** Nothing is fetched: a uniform disc has a closed-form mass, so the Radon identities and the recovery metric all have exact references. **Scale it** by raising the grid or using a smooth phantom — the identities hold at any size.

## Shape, size, cost

One task on `m8g.xlarge` (4 vCPU / 16 GiB), TTL 30m as a **backstop** with `cost_limit` $0.15 as the real guard. Everything — a 180-angle projection, four reconstructions, a tomopy comparison — is **7 s** of a 1m03s window; 43 s is installing Docker and 12 s pulling the image ([layout](../../patterns/layout-and-effective-cost.md)). **No GPU at any point.**

<details>
<summary>As shipped: Radon conservation and linearity at the float32 floor, a forward-model cross-check at 2.6e-07, and a reconstruction comparison deliberately left as an observation</summary>

### The checks

| observable | assertion | observed |
|---|---|---|
| staged bytes | match pin | 1 of 1 |
| astra / tomopy / numpy | read from the running install | **2.5.0** / 1.15.3 / 2.5.4 |
| **`astra.use_cuda()`** | **must be `False`** | **False** |
| phantom vs `πR²` | within 1% | 0.999493 |
| **projection sum, angle-independent** | **< 1e-3** | **3.864e-04** |
| **mean projection sum / mass** | **within 1e-4 of 1** | **7.0e-06** |
| **linearity `R(af+bg)`** | **< 1e-5** | **1.692e-06** |
| **forward: astra vs tomopy** | **< 1e-5 relative** | **2.592e-07** |
| **astra FBP recovers the phantom** | **< 0.10 relative L2** | **0.070257** |
| control: noise / shifted / uniform | must be far worse | 3.27 / 1.39 / 2.19 — **19.8×** |
| **more angles must help** | monotone | 0.174 → 0.101 → 0.070 → 0.063 |
| convergence *order* | *reported, **not** asserted* | ratios 1.728, 1.435, 1.120 |
| **determinism** | forward + FBP byte-identical | yes |
| tomopy reconstruction | *reported, **not** asserted* | 0.850 |
| tomopy `filter_name` effect | *reported* | **byte-identical** across 3 filters |

### Mass conservation, with bounds from float32 rather than from the run

The Radon transform conserves mass: the integral of a projection is the object's total mass,
**at every angle**. That angle-independence is what makes it a conservation identity rather than
a band:

```text
mean projection sum        11303.920898
object mass (pixels)       11304.000000      ratio 0.999993026   -> 7.0e-06
spread across 180 angles                                            3.864e-04
```

Neither is exactly zero, and the reason is arithmetic rather than error: **astra computes in
float32**, and the disc's rasterised boundary means each ray clips a jagged edge. So the
per-angle spread sits near 1e-4 while the mean over angles averages that away to 1e-5. The bounds
(1e-3 and 1e-4) are set from those two facts, not from where the run landed. Linearity —
`R(af+bg) = aR(f) + bR(g)`, exact in theory — lands at **1.692e-06**, about 14 float32 epsilons
over a 256-sample ray sum.

### The cross-check that works, and the one that does not

**Forward: astra and tomopy agree to 2.6e-07.** Two unrelated Radon implementations, the same
phantom, the same total:

```text
astra  mean projection sum   11303.920898
tomopy mean projection sum   11303.917969     relative difference 2.592e-07
```

**Reconstruction: they do not agree, and the gap is not a scale factor.** astra's FBP recovers
the phantom at 0.070; tomopy's best configuration reaches 0.850 — and gets the *same* 0.850
whether fed its own projections or astra's, which rules out a sinogram-convention mismatch on my
side. The honest metric took three attempts to find:

```text
raw comparison              tomopy relative error 24050   -> a ~1.8e4 normalisation difference
scale-normalised L2         0.834
correlation inside the disc 0.319   <- WORSE than a disc shifted 40 px, which scores 0.495
```

That last line is why this is reported rather than asserted. **The control is what exposed it**:
two reconstructions of one object agreeing *less* than one agrees with a deliberately wrong image
is not a tolerance problem. Reporting the 0.895 whole-image correlation as "good agreement" would
have been a green check on the wrong metric ([compare like with
like](../../practices/cross-checks.md)).

One part of the mechanism **is** established, from identical bytes: `tomopy.recon(algorithm="fbp")`
returns the same array for `ramlak`, `shepp` and `parzen`, so **`filter_name` is not reaching the
computation**. `gridrec` does respond to it (shepp 2.036, ramlak catastrophic at 2.1e4). What this
page does **not** claim is which default accounts for the 0.850-versus-0.070 gap — that was not
established, and guessing a mechanism is how a recipe acquires a confident wrong explanation.
Filed upstream rather than worked around quietly.

### Recovery, with a control, and a rate that is only reported

```text
angles   45    relative L2  0.174111
angles   90                 0.100784     ratio 1.728
angles  180                 0.070257     ratio 1.435
angles  360                 0.062753     ratio 1.120
```

Monotone, so more angles are asserted to help — but the ratios are **not** a clean order, and they
are plainly saturating. The floor near 0.063 is edge ringing from a sharp-edged disc, not a
shortage of angles, so no convergence order is claimed. ([paraview](../paraview/README.md) has a
ladder that genuinely is O(h²); this one is not, and saying so is the point.)

**The metric is checked against wrong answers both in staging and in the run**: gaussian noise
scores 3.27, a 40-px-shifted disc 1.39, a uniform field 2.19. The closest wrong image is **19.8×**
worse than astra's reconstruction, so the `< 0.10` threshold is separated from the failure region
rather than drawn tightly around a single observation.

### The Python 3.14 edge, worth recognising by its message

`tomopy.project` crashes on this env, and the error points at the wrong place:

```text
RuntimeError: An attempt has been made to start a new process before the
current process has finished its bootstrapping phase.
    ... you have forgotten to use the proper idiom in the main module:
        if __name__ == '__main__':
```

The script it blames is an ordinary top-level script that is perfectly legal under `fork`.
**Python 3.14 changed the default start method on Linux to `forkserver`**, so the child re-imports
the module — and tomopy calls `mp.Manager()` from inside `tomopy/util/mproc.py`, library code the
caller never invoked directly. The fix is the `__main__` guard (or `ncore=1`), and staging asserts
the guard survives, because losing it reintroduces a crash whose message sends you to your own
code.

### Pins

| | |
|---|---|
| data | **none.** A uniform disc, so the mass reference is `πR²`; all other references are Radon identities |
| checks | `identities.py`, pinned by sha256 |
| image | `quay.io/aarchsci/tomography@sha256:ea7b4900500f3eb23d…` — astra-toolbox 2.5.0 (libastra 2.5.0), tomopy 1.15.3 (libtomo 1.15.3), scikit-image 0.25.2, tifffile 2025.1.10, numpy 2.5.4, python 3.14 |

cosign-verified against `playgroundlogic/aarchsci`; the signature covers the **manifest-list**
digest, so verify the tag and pin the arm64 digest.

This env was built in response to
[aarchsci#41](https://github.com/playgroundlogic/aarchsci/issues/41), which also corrected two
beliefs about arm64 availability: astra-toolbox has **12 non-CUDA aarch64 builds** at 2.5.0 (the
CUDA siblings merely sort last by build string), and tomopy is **noarch** at 1.15.3 with its
compiled core `libtomo` built for aarch64 — not stale since 2022 as previously recorded.

### Run + verify

```sh
make stage RECIPE=tomography
spawn task run --spec "$(make -s spec RECIPE=tomography)" --wait
aws s3 cp "s3://$(make -s print-bucket)/runs/tomography/r1/score.tsv" -
```

Fails on a pin mismatch, a build reporting CUDA available, a projection sum that varies more than
1e-3 across angles or misses the object mass by more than 1e-4, a linearity residual above 1e-5,
astra and tomopy disagreeing on the forward projection by more than 1e-5, an astra reconstruction
worse than 0.10, a control image that scores no worse than the reconstruction, a refinement that
fails to reduce the error, or a non-deterministic repeat — but check the bucket regardless
([exit 0 isn't proof](../../practices/container-path.md)).

### Not covered

**Real data.** No measured projections, so nothing here touches flat/dark-field correction,
`minus_log`, ring artifacts, or centre-of-rotation finding — which is most of what a beamline
pipeline does and where tomopy earns its place.

**Iterative reconstruction** — SIRT, SART and CGLS are all in astra's CPU set and only FBP is run.
**3D and cone-beam geometry**: everything is a single 2D slice with parallel beam. **The GPU
path**, by construction. **tomopy's reconstruction quality**, beyond recording that it does not
match astra's here and that `filter_name` is inert for `fbp` — establishing *why* needs a
narrower experiment than a recipe. **Phantom variety**: one sharp-edged disc, so the edge-ringing
floor above is a property of this fixture rather than of FBP in general.

</details>
