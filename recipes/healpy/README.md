---
tool: healpy
tool_version: 1.20.0
env: astro
image: quay.io/aarchsci/astro@sha256:430fb49e6352fe1e24fe52fcf9c1a3587d41e86a34c3b733dd2098ee126e9c82
spawn_version: 0.104.0
last_verified: 2026-09-13
---
# healpy — HEALPix sky pixelization, exact identities on Graviton

healpy does HEALPix sky pixelization — the equal-area tessellation of the sphere that CMB and large-scale-structure analysis are built on — on Graviton4. The `astro` env's third recipe, on a different axis from [photutils](../photutils/README.md)' photometry. For anyone who works in HEALPix.

> **What this covers.** healpy 1.20 checked by four **exact** HEALPix identities — combinatorial, equal-area, and two round-trips — each *zero-band* (exact-or-wrong), not a tolerance. Sky pixelization, a different sub-culture and a different check family from photutils' constructed-truth reproductions. No map data, no network.

## Run it

```bash
spawn task run --spec "$(make -s spec RECIPE=healpy)" --wait
```

```python
import healpy as hp
nside = 64
npix  = hp.nside2npix(nside)                 # 49152 = 12 * nside^2, exact
theta, phi = hp.pix2ang(nside, range(npix))  # each pixel's centre on the sphere
```

The recipe asserts four properties of this pixelization that hold exactly, by HEALPix's construction — no measured value, no band.

## Make it yours

| In the recipe | Swap for | What to know |
|---|---|---|
| `nside = 64` | any `nside = 2^k` | npix and pixel area scale exactly (12·nside², 4π/npix); the identities hold at every resolution. |
| the four identities | your HEALPix ops (`ud_grade`, `query_disc`, `alm2map`) | these are the exact primitives the rest of HEALPix builds on; a real analysis adds map data on top. |
| synthetic (no map) | a real sky map (FITS) | read with `healpy.read_map` / `astropy.io.fits`; nothing is staged here because the identities are exact by construction. |

**Leave the fixture:** the point is that HEALPix's exact properties hold on arm64, and exact identities show that better than any real map. **Scale it** to your maps and operations — the pixelization underneath is what these four checks pin down.

## Shape, size, cost

One task, `c8g.large` (2 vCPU / 4 GiB), TTL 3m, cap $0.03. The compute is **~1 s**; recorded command window **74 s** — boot and the 0.57 GB `astro` image pull are the whole task. **These timings are not compute cost.**

**Sizing:** no family question — pure combinatorics and coordinate math, CPU-light. A real workload (high-nside maps, spherical harmonic transforms) sizes by map size and shifts to RAM; size it on the data.

<details>
<summary>As shipped: four exact identities (why zero-band, why exhaustive), pins, smoke check, run + verify</summary>

### Four exact HEALPix identities — zero band, each a different property

None of these is a tolerance; each is exact-or-wrong, from a distinct mathematical property of HEALPix:

- **Combinatorial — `npix = 12·nside²`.** An integer identity that cannot drift, checked for nside 1…1024.
- **Equal-area — Σ(pixel areas) = 4π, bit-exact.** The sum of all pixel areas equals 4π to relative error **exactly 0.00e+00** — and that zero is the *point*, not a rounded or truncated display. HEALPix pixels are equal-area by construction (`nside2pixarea` returns 4π/npix), so 4π/npix summed npix times returns to 4π bit-for-bit in IEEE-754 double at these nside. "Bit-exact" is a stronger claim than "within tolerance," and it's the one the number is making.
- **Exhaustive bijection — nested↔ring.** The two pixel orderings round-trip to the identity over **all 49152 pixels** (nside 64) — verified *exhaustively, not sampled*. A permutation is right or it isn't, and this checks every pixel, not a spot-check; the distinction is the same one as verifying versus spot-checking.
- **Geometric round-trip — pix→ang→pix.** Each pixel maps to its centre angles and back to itself, over all 49152 pixels — closing the pixel-centre geometry.

All four held **bit-identically on the Graviton verifying run**, including `Σ(areas)` = `12.566370614359172` (rel_err 0.0e+00) to the bit — confirming that the equal-area sum is IEEE-754-deterministic across arm64, not merely reproducible to tolerance.

### Pins (data tier: synthetic / in-code)

| | |
|---|---|
| image | `quay.io/aarchsci/astro@sha256:430fb49e…` (`astro` env — healpy 1.20.0 + astropy 8.0.1, cosign-signed, `linux/arm64`) |
| input | none — the pixelization is generated in-code from `nside` alone |

### Smoke check (inside the task; measured before launch)

| observable | assertion | observed |
|---|---|---|
| npix_12_nside2 | npix == 12·nside² (nside 1…1024) | exact |
| area_sum_4pi | Σ(areas) == 4π, bit-exact | rel_err 0.0e+00 |
| nest_ring_bijection | identity over all 49152 pixels | exhaustive ✓ |
| pix_ang_roundtrip | identity over all 49152 pixels | ✓ |

### Run + verify

```sh
make run RECIPE=healpy
make ls  RECIPE=healpy
```

The smoke check runs inside the task; the bucket listing is the second half ([exit 0 isn't proof](../../practices/container-path.md)). Expect `smoke-check.txt` with the four identities. Re-run: `make run` launches a fresh task and overwrites this prefix — no spec edit needed.

</details>
