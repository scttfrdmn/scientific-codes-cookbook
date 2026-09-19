---
tool: photutils
tool_version: 3.0.0
env: astro
image: quay.io/aarchsci/astro@sha256:430fb49e6352fe1e24fe52fcf9c1a3587d41e86a34c3b733dd2098ee126e9c82
spawn_version: 0.104.0
last_verified: 2026-09-13
---
# photutils — aperture photometry, source detection, background on Graviton

photutils performs the core image photometry an astronomy pipeline runs — aperture flux, source detection, background estimation — on Graviton4. The `astro` env's second recipe: a photometry workflow, where [astropy](../astropy/README.md) proved the units/coordinates/time core. For anyone doing photometry who knows the library.

> **What this covers.** photutils 3.0 measuring aperture flux, detecting injected sources, and estimating a background — each checked by **reproducing a value constructed independently of the tool** (an analytic integral, known source positions, a known constant). No live archive data. Note: SEP (the independent C Source-Extractor) is *not* in this env, so there's no second implementation to cross-check against — photutils and astropy are one ecosystem, and agreement between them would be self-report; reproduction against constructed truth is the stronger shape available here.

## Run it

```python
from photutils.aperture import CircularAperture, aperture_photometry
ap = CircularAperture([(x0, y0)], r=5 * sigma)
flux = aperture_photometry(image, ap, method="exact")["aperture_sum"]   # → 2πAσ² for a Gaussian
```

The recipe wraps three such operations — aperture photometry, `DAOStarFinder` detection, `Background2D` — each against a synthetic image whose answer is known analytically.

## Make it yours

| In the recipe | Swap for | What to know |
|---|---|---|
| synthetic Gaussian sources + constant background | your FITS images | photutils works on any 2D array (stage a FITS, read it with `astropy.io.fits`); synthetic here so the truth is analytic and the checks are exact. |
| `DAOStarFinder` | `IRAFStarFinder`, segmentation (`detect_sources`) | photutils ships several detectors; the choice is your source morphology. |
| aperture photometry | PSF photometry (`photutils.psf`) | aperture is the simple case; crowded fields want PSF fitting. |

**Leave the fixture:** synthetic sources give an analytic answer to check against, which real images can't. **Scale it** to your data and detectors — the operations and the reproduction-against-truth discipline carry over.

## Shape, size, cost

One task, `c8g.large` (2 vCPU / 4 GiB), TTL 3m, cap $0.03. The compute is **~1 s**; recorded command window **79 s** — boot and the 0.57 GB `astro` image pull are the whole task. **These timings are not compute cost.**

**Sizing:** no family question — small synthetic images, CPU-light. Real work (large mosaics, deep source catalogs, PSF fitting) sizes by image dimensions and source count, shifting to RAM; size it on the data.

<details>
<summary>As shipped: three reproductions against constructed truth, why no cross-tool, pins, smoke check, run + verify</summary>

### Three reproductions against constructed truth — and why not a cross-tool check

photutils and astropy are one ecosystem, so their agreement is self-report, not independence — and **SEP (the independent C Source-Extractor) is absent from the env**, so there's no second implementation to compare against. Each check therefore reproduces a value constructed *independently of photutils* (computed analytically, or an injected known), which is the stronger shape available:

- **Aperture flux vs the analytic integral.** A 2D Gaussian has total flux 2πAσ²; within radius R the analytic fraction is 1 − e^(−R²/2σ²). photutils' exact-aperture sum at R = 5σ reproduces that to **6.5e-8** relative — the band is the *construction's* precision (analytic integral + sampling of a well-resolved σ=8 source), not a fitted tolerance.
- **Detection count and positions.** Five Gaussians injected at known positions → **5 detected** (exact-or-wrong on the count), each centroid within **< 0.1 px** of truth — the position band is `DAOStarFinder`'s centroiding accuracy, justified by the method, not the fixture.
- **Background of a constant field.** A constant 123.5 → `Background2D` recovers **123.5 exactly** (max deviation 0) — exact for a pure constant, no band.

All four reproduced **bit-identically on the Graviton verifying run** — including the one numerical check (aperture rel_err 6.46e-08 to three figures), so even it is deterministic across machines, not merely within tolerance.

### Pins (data tier: synthetic / in-code)

| | |
|---|---|
| image | `quay.io/aarchsci/astro@sha256:430fb49e…` (`astro` env — photutils 3.0.0 + astropy 8.0.1, cosign-signed, `linux/arm64`) |
| input | none — the Gaussian sources, the injected field, and the constant background are all built in-code |

### Smoke check (inside the task; measured before launch)

| observable | assertion | observed |
|---|---|---|
| aperture_flux | rel err vs analytic within-R < 1e-6 | 6.5e-8 |
| detect_count | exactly 5 (injected 5) | 5 |
| detect_positions | max centroid error < 0.1 px | 0.0000 |
| background_const | recovers 123.5, max dev ~0 | 123.5 / 0 |

### Run + verify

```sh
make run RECIPE=photutils
make ls  RECIPE=photutils
```

The smoke check runs inside the task; the bucket listing is the second half ([exit 0 isn't proof](../../practices/container-path.md)). Expect `smoke-check.txt` with the three reproductions. Re-run: `make run` launches a fresh task and overwrites this prefix — no spec edit needed.

</details>
