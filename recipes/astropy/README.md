---
tool: astropy
tool_version: 8.0.1
env: astro
image: quay.io/aarchsci/astro@sha256:430fb49e6352fe1e24fe52fcf9c1a3587d41e86a34c3b733dd2098ee126e9c82
spawn_version: 0.104.0
last_verified: 2026-09-13
---
# Astropy — units, WCS, coordinates, time, and FITS on Graviton

Astropy runs the core transforms every astronomy pipeline leans on — physical units, WCS sky projections, coordinate-frame conversions, time scales, and FITS I/O — on Graviton4. The first recipe in the `astro` env, for anyone doing astronomy who knows the library.

> **What this covers.** One offline astropy task exercising five core operations, each checked by an *exact* identity rather than a tolerance — the strongest verification the catalog has, in a domain that had none. Not a benchmark, and no live archive queries (astroquery needs network; out of scope).

## Run it

```bash
spawn task run --spec "$(make -s spec RECIPE=astropy)" --wait
```

```python
from astropy.wcs import WCS
w = WCS(naxis=2)
w.wcs.ctype = ["RA---TAN", "DEC--TAN"]; w.wcs.crval = [150.0, 2.0]
w.wcs.crpix = [256.5, 256.5]; w.wcs.cdelt = [-0.000277778, 0.000277778]
world = w.wcs_pix2world(pixels, 0)   # pixel → sky
back  = w.wcs_world2pix(world, 0)    # sky → pixel — returns the input, exactly
```

That WCS round-trip is the headline; the recipe adds a coordinate transform, a time-scale offset, a units check, and a FITS round-trip — five identities in one task, nothing staged.

## Make it yours

| In the recipe | Swap for | What to know |
|---|---|---|
| the synthetic checks (constructed WCS, in-code arrays) | your FITS images / source catalogs | synthetic *because* these identities are exact by construction; a real image is a longer run, not a more legible check. |
| astropy core | photutils, sunpy, healpy, reproject, specutils | all in the `astro` env; this recipe proves the core those build on. |
| offline | astroquery for live archive/catalog queries | needs network — deliberately out of this zero-network recipe. |

**Leave the fixture:** the point is that astropy's core transforms are correct on arm64, and exact identities show that better than any real dataset would. **Scale it** to your data and the domain packages — the env carries the whole stack.

## Shape, size, cost

One task, `c8g.large` (2 vCPU / 4 GiB), TTL 4m, cap $0.03. The compute is **~1 s**; recorded command window **93 s** — boot and the 0.57 GB `astro` image pull are the whole task. **These timings are not compute cost.**

**Sizing:** no family question — the checks are CPU-light and definitional. A real workload (large FITS mosaics, healpix maps, catalog cross-matches) sizes by data volume and shifts to RAM; size it on the dataset, not this.

<details>
<summary>As shipped: the five identities, pins, smoke check, run + verify</summary>

### Five identities, each a check shape the catalog already trusts

Nothing here is a band on an observed value — every check is exact, algorithmic, or definitional, so none can go flaky:

- **Definitional** (the [salmon-TPM](../salmon/README.md) shape): the speed of light is **299792458 m/s** exactly (SI definition), and **TT − TAI = 32.184 s** exactly. The time offset is read between two instants sharing a calendar reading in different scales — *not* `t.tt − t.tai`, which is zero by construction (same instant, two labels) and would be a vacuous check.
- **Algorithmic** (the [BLAST self-hit](../blast/README.md) shape): a TAN-projection WCS round-trip, pixel→world→pixel, returns the input to **8.3e-11 px** — exact up to floating point, no tolerance earned.
- **Reproduce a published constant** ([reproduce a published number](../../practices/reference-from-tests.md)): the Galactic north pole (`b = +90°`) transforms to ICRS **RA 192.85948°, Dec +27.12825°** — the IAU-defining values for the Galactic frame.
- **Decode statistic** (the sixth format after BAM/PDAL/COG/…): a FITS write→read returns the array **bit-identical**, exercising the real encoder/decoder, not just a header.

All six reproduced bit-identically on the Graviton verifying run — expected, since each is exact, algorithmic, or definitional rather than a measured value, but confirmed rather than assumed.

### Pins (data tier: synthetic / in-code)

| | |
|---|---|
| image | `quay.io/aarchsci/astro@sha256:430fb49e…` (`astro` env, astropy 8.0.1 + photutils/sunpy/healpy/reproject/specutils, cosign-signed, `linux/arm64`) |
| input | none — WCS, coordinates, time, and the FITS array are all built in-code |

### Smoke check (inside the task; measured before launch)

| observable | assertion | observed |
|---|---|---|
| speed_of_light | == 299792458 m/s (exact) | 299792458.0 |
| wcs_roundtrip | max \|pix − round-trip\| < 1e-9 px | 8.3e-11 |
| ngp_ra / ngp_dec | IAU 192.85948° / 27.12825° (±1e-4) | 192.859478 / 27.128252 |
| tt_minus_tai | == 32.184 s (±1e-6) | 32.184000 |
| fits_roundtrip | array bit-identical | True |

### Run + verify

```sh
make run RECIPE=astropy
make ls  RECIPE=astropy
```

The smoke check runs inside the task; the bucket listing is the second half ([exit 0 isn't proof](../../practices/container-path.md)). Expect `smoke-check.txt` and `astro.fits` (the round-tripped image). Re-run: `make run` launches a fresh task and overwrites this prefix — no spec edit needed.

</details>
