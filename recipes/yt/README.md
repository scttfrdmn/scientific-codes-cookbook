---
tool: yt
tool_version: "4.4.2"
env: astro
image: quay.io/aarchsci/astro@sha256:eaa6cb334d95db68662c51646a139c0ff635e9ebf3cb603cf5856ad40c26d066
env_x86_64: astro-ed5afa726bbe
last_verified_x86_64: 2026-10-17
last_verified_amd: 2026-10-18
spawn_version: 0.127.0
last_verified: 2026-10-11
---
# yt — a volumetric analysis stack held to conservation

Checks yt's grid, projection, profile and region machinery on Graviton4 against closed forms and independent numpy reductions. For anyone reducing simulation output on ARM.

## Run it

```bash
make stage RECIPE=yt      # once: the checks only — the density field is analytic
spawn task run --spec "$(make -s spec RECIPE=yt)" --wait
make ls RECIPE=yt

ds = yt.load_uniform_grid({"density": rho}, rho.shape,
                          length_unit="cm", mass_unit="g", bbox=bbox, nprocs=1)
ds.all_data().quantities.total_quantity(("gas", "mass"))   # == rho0 * L^3, exactly
ds.proj(("gas", "density"), "y")                           # integrates back to that mass
ih, iv = ds.coordinates.x_axis[1], ds.coordinates.y_axis[1]   # 2, 0 — NOT 0, 2
```

## Make it yours

| In the recipe | Swap for | What to know |
|---|---|---|
| `load_uniform_grid` on an analytic field | `yt.load("plt00100")` | the stream frontend takes an in-memory array, so the right answer exists before yt runs. [warpx](../warpx/README.md) drives the AMReX frontend on a real plotfile instead. |
| the axis mapping read from `ds.coordinates` | hardcoding the surviving axes | **don't.** yt maps image axes **cyclically**: projecting along `y` puts x *vertical*. Assuming ascending order swapped the width tuple, the buffer sampled outside the domain, and it came back NaN. |
| 32×48×64 cells on a 1×2×3 cm box | a cube | a cube can't catch an axis mix-up — every axis is interchangeable there. |
| the flip and weighted controls | the identities alone | drop them and a projection that ignored orientation, or averaged instead of integrating, still passes. |
| the 256³ finest rung | your own resolution | 256³ float64 is 134 MB, and that ladder is the only reason this needs 8 GiB. |

**Leave the fixture.** Nothing is fetched and no simulation runs: the density is a polynomial sampled at cell centres, so every reference is a closed form or an independent numpy sum over the same doubles. **Scale it** by aiming the same checks at a real dataset — the conservation identities transfer unchanged, the closed forms don't.

## Which box — [measured](../../measurements/yt-real/README.md) on all 51 outputs of the Enzo_64 cosmology run

Eight workers on a `2xlarge`, one output each: **Graviton4 $0.0096 billed** (32 s), AMD $0.0104 (29 s), Intel $0.0126 (50 s). Leaf cells, covering grids and particles match an independent h5py reader exactly on every output and every chip. Every mass total closes to ≤ 2e-15, and the grid counts match Enzo's own log. The box is sized by the 4 GB staged in tmpfs, not by cores.

## Shape, size, cost

One task on `m8g.large` (2 vCPU / 8 GiB), TTL 30m as a **backstop** with `cost_limit` $0.12 as the real guard. Every check together is **6 s** of a 1m15s window; 45 s is installing Docker and 23 s pulling the image ([layout](../../patterns/layout-and-effective-cost.md)).

<details>
<summary>As shipped: mass exact to the last bit through three code paths, an elementwise axis check with its own control, and a convergence order deliberately not claimed</summary>

### The checks

| observable | assertion | observed — arm64 = x86_64 |
|---|---|---|
| staged bytes | match pin | 1 of 1 |
| yt / numpy / unyt | read from the running install | **4.4.2** / 2.5.3 / 3.1.0 |
| **uniform mass** | **`== rho0 * L^3` exactly** | **0.000e+00** |
| **cell volumes** | **`== L^3` exactly** | `1.00000000000000000e+00` |
| **non-uniform mass** | **`==` independent numpy sum** | **0.000e+00** |
| **anisotropic mass** (32×48×64 on 1×2×3) | **`==` independent numpy sum** | **0.000e+00** |
| **projection → total mass**, 3 axes × 2 boxes | < 1e-12 relative | **2.381e-15** worst |
| **projection elementwise vs numpy** | < 1e-12 relative | **4.785e-15** worst |
| control: vertically flipped image | must *not* match | 1.750e-01 / 3.447e-01 / 2.492e-01 |
| control: weighted projection | must be an average, not an integral | `g/cm**3`, sum 6.647e+03 |
| **profile mass summed over bins** | < 1e-12 relative | **6.043e-15** worst |
| sphere at 256³ vs `(4/3)πR³ρ` | < 1e-4 relative | **2.658e-05** |
| each refinement improves | strictly | 1.56e-2 → 6.20e-3 → 8.36e-4 → 2.66e-5 |
| sphere convergence **order** | *reported, **not** asserted* | ratios 2.523, 7.414, 31.460 |
| determinism | two identical loads agree | identical |

**On x86_64** (`m8i.large`, the `astro-ed5afa726bbe` twin: 345 of 364 versions identical, yt 4.4.2, numpy 2.5.3 and unyt 3.1.0 among them; the 18 that differ are minor bumps such as `fonttools`, `tifffile` and `spiceypy`, listed in the lock header): `score.tsv` and `identities.log` are **byte-identical** to the Graviton run, covering the exact `==` masses, the 1e-15 projection and profile residuals, the flipped-image controls and the sphere ladder.

### Mass, exactly, through three different code paths

The density is `1 + 0.7x̂ + 0.5ŷ² + 0.3ẑ³ + 0.2x̂ŷẑ` on normalised coordinates, sampled at cell
centres — cell centres because yt's `cell_mass` is `rho_cell * dV`, so the numpy reference has to
use the same sample points or the two sums are of different things.

```text
total_quantity(cell_mass)          1.61664733886718759e+00 g
numpy rho.sum() * dV               1.61664733886718759e+00 g   diff 0.000e+00
```

Asserted with `==`, not a tolerance, and that is defensible rather than reckless: both sides are
the same finite sum of the same doubles. A tolerance would have concealed that it is bit-identical.
The uniform case is stronger still — `rho0 * L^3` is a closed form with no quadrature error at all.

Then the same number is reached twice more by machinery that shares none of that code: a
**projection** integrates surface density along an axis and the result is summed over the face
area, and a **profile** histograms every cell into density bins and sums the mass in them. Both
land within a few 1e-15 — not exact, because each re-sums 262,144 doubles in a different order.

### The axis check, which conservation cannot provide

A total is transpose-invariant: `Σρ·dV` has one scalar `dV`, so permuting the array leaves it
unchanged. **Every conservation check in the table above would pass on a transposed image.** So
yt's projected buffer is also compared cell-by-cell against `rho.sum(axis=k) * dk`.

That needs yt's own image-axis convention, which is cyclic and was read from the install rather
than inferred:

```text
project along x  ->  horizontal = axis 1 (y),  vertical = axis 2 (z)
project along y  ->  horizontal = axis 2 (z),  vertical = axis 0 (x)
project along z  ->  horizontal = axis 0 (x),  vertical = axis 1 (y)
```

Note `y` is the odd one: its vertical axis is the *lower* index. Assuming the two surviving axes
stay in ascending order is therefore wrong for exactly one of three axes — it swapped the width
tuple, the buffer sampled outside the domain, and it returned **NaN-filled**. The check passed
anyway, because `nan > 1e-12` is `False` in Python and every bound here is one-sided. So each
bound is now paired with an explicit `isfinite` assertion; a one-sided tolerance alone cannot
catch a NaN. Same family of trap as the one [warpx](../warpx/README.md) hit on openPMD's
`axisLabels`, where a square grid hid a transpose.

**The control matters here more than usual.** A vertical flip preserves shape, so it is the wrong
answer this check must still reject — and it is only **17–34%** away, not orders of magnitude. A
tolerance picked loosely would wave it through. (A transpose cannot even be compared on this box:
no two axes share a length, so it fails on shape, which is categorical.) The weighted-projection
control is the other half: an unweighted projection must be an *integral*, and a weighted one
comes back in `g/cm**3` — an average — so the unweighted path is confirmed to be integrating.

### A convergence order this page refuses to claim

A sphere selector's mass should approach `(4/3)πR³ρ` as cells shrink, and a *rate* would test the
method rather than the answer. The rate is not clean:

```text
n=32    rel err 1.5643e-02
n=64    rel err 6.1990e-03     ratio 2.523
n=128   rel err 8.3608e-04     ratio 7.414
n=256   rel err 2.6576e-05     ratio 31.460
```

Convergence is fast and monotone, but the ratios are not a power of `h` — so fitting `O(h^p)` to
them and asserting it would be a band wearing a method test's clothes, which is the opposite of
what a ladder is for ([paraview](../paraview/README.md) has one that genuinely is `O(h²)`:
4.01, 4.01, 4.00). What *is* asserted is the finest rung against the analytic volume and strict
improvement at every refinement — both reproducible, because this is deterministic. Why the
ratios accelerate is not established here and this page does not guess at a mechanism.

### Pins

| | |
|---|---|
| data | **none.** A polynomial density sampled at cell centres; references are a closed form, an independent numpy reduction, and an analytic sphere volume |
| checks | `identities.py`, pinned by sha256 |
| image | `quay.io/aarchsci/astro@sha256:eaa6cb334d95db6866…` — yt 4.4.2, unyt 3.1.0, numpy 2.5.3, astropy 8.0.1, python 3.14.8 |

cosign-verified against `playgroundlogic/aarchsci`; the signature covers the **manifest-list**
digest, so verify the tag and pin the arm64 digest.

`stage-inputs.sh` lifts the field definition out of `identities.py` by name rather than restating
it — an earlier version kept its own copy, they drifted, and the staging validation was checking a
function the run did not use. It also asserts the field varies along *every* axis: an earlier
fixture used `sin(2πx)cos(4πy)`, whose average over a full period in y is zero, which made the
x-marginal exactly flat.

### Run + verify

```sh
make stage RECIPE=yt
spawn task run --spec "$(make -s spec RECIPE=yt)" --wait
spawn task run --spec "$(make -s spec RECIPE=yt ARCH=x86_64)" --wait   # x86 twin, writes runs-x86_64/
aws s3 cp "s3://$(make -s print-bucket)/runs/yt/r1/score.tsv" -
```

Fails on a pin mismatch, a total mass that is not bit-identical to its closed form or to numpy, a
projection or profile that loses mass, a projected buffer that disagrees elementwise or whose shape
contradicts yt's own axis mapping, a flipped or weighted image that the controls fail to
distinguish, a sphere that misses the analytic volume or stops improving under refinement, or any
non-finite value anywhere — but check the bucket regardless
([exit 0 isn't proof](../../practices/container-path.md)).

### Not covered

**Rendering.** No `SlicePlot`, `ProjectionPlot` or volume rendering: verifying an image needs a
display and image comparison, and this env is deliberately network- and display-free.

**Real AMR.** `load_uniform_grid` is a single-level stream dataset. yt's reason to exist is
adaptive refinement across many frontends — grid selection, ghost zones and level traversal are
all untested here. [warpx](../warpx/README.md) reads a genuine AMReX plotfile, but it is
single-level. Real AMR is covered by the [measurement](../../measurements/yt-real/README.md)
instead: a five-level Enzo run read through yt's Enzo frontend and checked against an independent
reader.

**Particles**, and therefore every deposition and particle-filter path. **Derived fields** beyond
the `("gas", "mass")` yt builds itself. **Units beyond cgs** — no unit-system conversion is
exercised, though `unyt` carries the arithmetic. **Parallelism**: yt's `enable_parallelism()` and
its MPI decomposition are not touched, so nothing here speaks to multi-rank reductions.
**Covering grids and off-axis projections**, both of which would extend the axis check above.

</details>
