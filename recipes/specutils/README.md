---
tool: specutils
tool_version: "2.4.0"
env: astro
image: quay.io/aarchsci/astro@sha256:c09e88de976ec1184dbc4461bbb19477e9d39a991ef8aceeddb012da8d356a1c
env_x86_64: astro-e2ee19affec3
last_verified_x86_64: 2026-10-17
last_verified_amd: 2026-10-18
spawn_version: 0.127.0
last_verified: 2026-10-12
---
# specutils — an analytic equivalent width, and flux conservation that bites

Measures a synthetic absorption line on Graviton4 against its closed form, then resamples it onto grids coarse enough to separate a flux-conserving resampler from an interpolating one. For anyone doing spectroscopy on ARM.

## Run it

```bash
make stage RECIPE=specutils     # once: the checks only — the spectrum is synthesised
spawn task run --spec "$(make -s spec RECIPE=specutils)" --wait
make ls RECIPE=specutils

from specutils import Spectrum                 # NOT Spectrum1D — deprecated in 2.x
spec = Spectrum(spectral_axis=lam * u.AA, flux=flux * u.Jy)
equivalent_width(spec, continuum=1.0 * u.Jy, regions=reg)   # == d·σ·√(2π), to 1e-16
FluxConservingResampler()(spec, coarse * u.AA)              # integral exact at 0.3 samples/σ
```

## Make it yours

| In the recipe | Swap for | What to know |
|---|---|---|
| `Spectrum` | `Spectrum1D` | **deprecated in specutils 2.x** and warns it may be removed; the old name still works but emits `AstropyDeprecationWarning` on every construction. |
| the **erf** integral as reference | a trapezoid over a sub-range | **those differ by 0.05 here** — partial end intervals, not a conservation error. It cost me a phantom `1.9e-04` result; staging now records the trap. |
| grids down to **0.3 samples/σ** | the native grid | at 15 samples/σ *all three* resamplers conserve flux equally, so the control proves nothing. Coarseness is what separates them. |
| `FluxConservingResampler` | `LinearInterpolatedResampler` | at 10 Å sampling of a 3 Å line the interpolator **loses 6.5% of the equivalent width**. For line measurements that is the whole point. |
| the synthetic line | a real spectrum | nothing is fetched — the closed form is what makes this checkable. [astropy](../astropy/README.md) covers the FITS/WCS layer these objects are built on. |

**Leave the fixture.** `EW = d·σ·√(2π)` is exact, so the reference exists before specutils runs and staging verifies it independently. **Scale it** by adding lines or noise — the EW formula is per-line and the conservation law is unconditional.

## Shape, size, cost

One task on `m8g.large` (2 vCPU / 8 GiB), TTL 30m as a **backstop** with `cost_limit` $0.12 as the real guard. The analysis plus 12 resamplings is **6 s** of a 1m36s window ([layout](../../patterns/layout-and-effective-cost.md)).

<details>
<summary>As shipped: an analytic EW to 1.5e-16, exact conservation at every spacing, and a graded control from a sibling class</summary>

### The checks

| observable | assertion | observed — arm64 = x86_64 |
|---|---|---|
| staged bytes | match pin | 1 of 1 |
| specutils / astropy / numpy / scipy | read from the running install | **2.4.0** / 8.0.1 / 2.5.4 / 1.18.1 |
| spectrum class | current, not deprecated | **`Spectrum`** |
| reference self-check | erf == trapezoid on the **full** range | **0.000e+00** |
| **equivalent width** | **== `d·σ·√(2π)`, < 1e-12** | **1.476e-16** |
| **centroid of a symmetric line** | **== λ₀** | **0.000e+00** |
| **`FluxConserving` integral** | **`== 0` at 0.2, 2, 5, 10 Å** | **0.000e+00** ×4 |
| control: `LinearInterp` integral | must be non-zero and **graded** | 0 → 2.5e-08 → 1.8e-05 → **7.6e-04** |
| control: `SplineInterp` integral | — | 0 → 2.8e-12 → 1.8e-05 → 7.6e-04 |
| **EW after `FluxConserving` to 5 Å** | **`== 0`** | **0.000e+00** |
| **EW after `FluxConserving` to 10 Å** | **`== 0`** | **0.000e+00** |
| control: EW after `LinearInterp` to 10 Å | must lose > 1e-3 | **6.493e-02** |
| determinism | two calls agree | identical |

**On x86_64** (`m8i.large`, the `astro-e2ee19affec3` twin: 349 of 364 versions identical, specutils 2.4.0, astropy, numpy 2.5.4 and scipy 1.18.1 among them; the 14 that differ are minor bumps such as `fonttools`, `tifffile`, `libsqlite`, listed in the lock header): `score.tsv` and `identities.log` are **byte-identical** to the Graviton run, every exact zero and the 1.476e-16 EW residual included.

### An analytic reference, verified without the tool

A Gaussian absorption line of depth `d` and width `σ` on a unit continuum has
`EW = d·σ·√(2π)` exactly — no fitting, no reference table:

```text
analytic        3.007953929557 A
specutils       3.007953929557 A      relative 1.476e-16
centroid        6562.800000000 A      offset   0.000e+00
```

Staging checks that formula against a quadrature of the same profile before the run, so a
mis-derived reference cannot silently become the thing specutils is judged against.

### The reference trap, recorded rather than hidden

The integral of the spectrum is also analytic, via `erf`. That matters because the obvious
alternative is wrong:

```text
full range [6400, 6700]   analytic 296.992046070443   trapezoid 296.992046070443   agree
sub-range  [6420, 6680]   analytic 256.992046070443   trapezoid 256.942046070444   differ by 0.050
```

A trapezoid over a **sub-range** of the fine grid differs from the true integral by partial end
intervals. My first attempt used exactly that as the conservation reference and produced a
`1.946e-04` "error" that did not exist — all three resamplers were correct and the reference was
not. The run now asserts erf and trapezoid agree over the **full** range, where they cannot
differ by end effects, and records the sub-range discrepancy so the trap is visible.

### Conservation, and a control that only bites on coarse grids

`FluxConservingResampler` promises to preserve the integral; `LinearInterpolatedResampler` and
`SplineInterpolatedResampler` do not. Same package, different class, one making the claim:

```text
spacing   samples/σ   FluxConserving   LinearInterp   SplineInterp
 0.200 A     15.00      0.000e+00       0.000e+00      0.000e+00
 2.000 A      1.50      0.000e+00       2.534e-08      2.811e-12
 5.000 A      0.60      0.000e+00       1.764e-05      1.785e-05
10.000 A      0.30      0.000e+00       7.599e-04      7.601e-04
```

**The first row is why this needed a sweep.** At 15 samples/σ all three conserve flux equally, so
a test on the native grid would have reported "FluxConservingResampler conserves flux" from
evidence that said nothing — every resampler did. Staging now asserts the coarsest grid
**undersamples** the line (0.3 samples/σ), so an interpolator must lose flux and the control
cannot be vacuous.

The control is also **graded**: the interpolator's error grows monotonically with spacing, which
the run asserts. A monotone ladder makes the conserving resampler's exact zero a property rather
than a coincidence — same reasoning as the Δφ ladder in [fastjet](../fastjet/README.md).

### And the physics, not just the integral

Conserving an integral is necessary but not interesting on its own. What a spectroscopist needs is
that the **line measurement** survives:

```text
                    0.2 A        5 A          10 A
FluxConserving    2.953e-16   0.000e+00    0.000e+00
LinearInterp      2.067e-15   1.507e-03    6.493e-02
```

At 10 Å sampling of a 3 Å line — under a third of a sample per σ — the conserving resampler
returns the equivalent width **exactly**, while linear interpolation has lost **6.5%** of it. That
is the difference between a usable and an unusable line measurement, and it is the same number the
integral check predicts.

### Pins

| | |
|---|---|
| data | **none.** A Gaussian line at 6562.8 Å on a unit continuum; references are `d·σ·√(2π)` and an erf integral, both verified in staging |
| checks | `identities.py`, pinned by sha256 |
| image | `quay.io/aarchsci/astro@sha256:c09e88de976ec1184d…` — specutils 2.4.0, astropy 8.0.1, sunpy 8.0.0, photutils, healpy, reproject, yt, rebound, scipy 1.18.1, numpy 2.5.4, python 3.14.8 |

cosign-verified against `playgroundlogic/aarchsci`; the signature covers the **manifest-list**
digest, so verify the tag and pin the arm64 digest. Note this is a **newer astro digest** than
[yt](../yt/README.md) pins — that recipe stays on the image it was verified against, since
repinning is a change to the input rather than a text edit.

### Run + verify

```sh
make stage RECIPE=specutils
spawn task run --spec "$(make -s spec RECIPE=specutils)" --wait
spawn task run --spec "$(make -s spec RECIPE=specutils ARCH=x86_64)" --wait   # x86 twin, writes runs-x86_64/
aws s3 cp "s3://$(make -s print-bucket)/runs/specutils/r1/score.tsv" -
```

Fails on a pin mismatch, an erf reference that disagrees with a full-range trapezoid, an
equivalent width more than 1e-12 from the closed form, a centroid off the line centre, any
`FluxConserving` integral that is not exactly zero error, an interpolator that conserves exactly
at every spacing (which would make the claim vacuous), an interpolator error that is not monotone
in spacing, an `EW` that moves under conserving resampling, or an interpolator that loses less
than 1e-3 of the EW at 10 Å — but check the bucket regardless
([exit 0 isn't proof](../../practices/container-path.md)).

### Not covered

**Real spectra.** No FITS is read, so nothing here touches `Spectrum.read`, the loaders for SDSS
or JWST products, WCS spectral axes, or the air/vacuum and velocity-frame conversions that make up
most of a reduction.

**Line fitting** — `fit_lines`, the `astropy.modeling` integration, and multi-component
deblending, which is how equivalent widths are actually measured on noisy data. **Uncertainties**:
every flux here is exact, so propagation through resampling and the `snr` machinery is untested.
**Continuum fitting** (`fit_generic_continuum`) — the continuum is handed in as a constant, which
is the easy case. The solar coordinate frames this env also carries are covered separately by
[sunpy](../sunpy/README.md). **Multi-dimensional spectra** (`SpectrumCollection`, spectral cubes), where the resampling
question above matters most and costs most.

</details>
