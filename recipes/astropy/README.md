---
tool: astropy
tool_version: 8.0.1
env: astro
image: quay.io/aarchsci/astro@sha256:430fb49e6352fe1e24fe52fcf9c1a3587d41e86a34c3b733dd2098ee126e9c82
env_x86_64: astro-903b8d412bd4
last_verified_x86_64: 2026-10-17
last_verified_amd: 2026-10-18
spawn_version: 0.104.0
last_verified: 2026-09-13
---
# Astropy — units, WCS, coordinates, time, and FITS on Graviton

Astropy runs the core transforms every astronomy pipeline leans on — physical units, WCS sky projections, coordinate-frame conversions, time scales, and FITS I/O — on Graviton4. The first recipe in the `astro` env, for anyone doing astronomy who knows the library.

## Run it

```bash
spawn task run --spec "$(make -s spec RECIPE=astropy)" --wait
```

```python
from astropy.wcs import WCS; w = WCS(naxis=2)
w.wcs.ctype = ["RA---TAN", "DEC--TAN"]; w.wcs.crval = [150.0, 2.0]; w.wcs.crpix = [256.5, 256.5]; w.wcs.cdelt = [-0.000277778, 0.000277778]
world = w.wcs_pix2world(pixels, 0)   # pixel → sky
back  = w.wcs_world2pix(world, 0)    # sky → pixel — returns the input, exactly
```

## Make it yours

| In the recipe | Swap for | What to know |
|---|---|---|
| the synthetic checks (constructed WCS, in-code arrays) | your FITS images / source catalogs | synthetic *because* these identities are exact by construction; the [real field](../../measurements/astropy-real/README.md) is the at-scale version. |
| astropy core | photutils, sunpy, healpy, reproject, specutils | all in the `astro` env; this recipe proves the core those build on. |
| offline | astroquery for live archive/catalog queries | needs network — deliberately out of this zero-network recipe. |

**Leave the fixture:** the point is that astropy's core transforms are correct on arm64, and exact identities show that better than any real dataset would. **Scale it** to your data and the domain packages — the env carries the whole stack.

## Shape, size, cost

One task, `c8g.large` (2 vCPU / 4 GiB), TTL 4m, cap $0.03. The compute is **~1 s**; recorded command window **93 s** — boot and the 0.57 GB `astro` image pull are the whole task. **These timings are not compute cost.** A real workload sizes by data volume and shifts to RAM — below.

## Which box — [measured](../../measurements/astropy-real/README.md) (Gaia DR3 × PS1 over 13.4 deg², 100 PS1 skycell WCS)

| chip | instance | cross-match / WCS / total | **billed $/result** |
|---|---|---|---|
| Graviton4 | `r8g.xlarge` | 65 / 767 / 866 s | 0.0706 |
| Intel | `r8i.xlarge` | 52 / 728 / 811 s | 0.0734 |
| **AMD** | `r8a.xlarge` | 49 / 575 / 651 s | **0.0675** |

**Memory, not cores, sizes it** (7.4 GiB staged in tmpfs + 8.5 GiB peak RSS). The 3.74 M-row merged catalogue is identical across chips (Intel and AMD bit for bit). Gaia pm is per TCB year and ERFA propagates in TDB, so `apply_space_motion` falls short by L_B = 1.55e-8. astropy 8.0.1's parquet reader raises `KeyError('_healpix_29')` on Gaia's HATS files, so read them with pyarrow.

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

| observable | assertion | arm64 | x86_64 |
|---|---|---|---|
| speed_of_light | == 299792458 m/s (exact) | 299792458.0 | identical |
| wcs_roundtrip | max \|pix − round-trip\| < 1e-9 px | 8.3e-11 | identical |
| ngp_ra / ngp_dec | IAU 192.85948° / 27.12825° (±1e-4) | 192.859478 / 27.128252 | identical |
| tt_minus_tai | == 32.184 s (±1e-6) | 32.184000 | identical |
| fits_roundtrip | array bit-identical | True | True |

**On x86_64** (`c8i.large`, the `astro-903b8d412bd4` twin: the same env solved for linux-64, 361 of 363 versions identical; `deprecated` 3.0.0 vs arm64's 1.3.1, a pure-Python shim): `smoke-check.txt` and `astro.fits` are **byte-identical** to the Graviton run. Nothing here is a path, so nothing was expected to move.

### Run + verify

```sh
make run RECIPE=astropy
make run RECIPE=astropy ARCH=x86_64   # same spec, x86 twin, writes runs-x86_64/
make ls  RECIPE=astropy
```

The smoke check runs inside the task; the bucket listing is the second half ([exit 0 isn't proof](../../practices/container-path.md)). Expect `smoke-check.txt` and `astro.fits` (the round-tripped image). Re-run: `make run` launches a fresh task and overwrites this prefix — no spec edit needed.

</details>
