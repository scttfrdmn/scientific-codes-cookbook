---
tool: sunpy
tool_version: "8.0.0"
env: astro
image: quay.io/aarchsci/astro@sha256:c09e88de976ec1184dbc4461bbb19477e9d39a991ef8aceeddb012da8d356a1c
env_x86_64: astro-e2ee19affec3
last_verified_x86_64: 2026-10-17
last_verified_amd: 2026-10-18
spawn_version: 0.127.0
last_verified: 2026-10-12
---
# sunpy — solar geometry against IAU constants, and a sphere that is not a disc

Checks sunpy's ephemeris, coordinate frames and rotation laws on Graviton4 against defined constants and closed forms, with two plausible-but-wrong formulas as the controls. For anyone doing solar physics on ARM.

## Run it

```bash
make stage RECIPE=sunpy     # once: the checks only — every reference is published or analytic
spawn task run --spec "$(make -s spec RECIPE=sunpy)" --wait
make ls RECIPE=sunpy

sun.angular_radius(t)                    # == arcsin(R_sun/d) exactly — NOT arctan
SkyCoord(0*u.arcsec, 0*u.arcsec, frame=frames.Helioprojective(obstime=t, observer="earth")
         ).transform_to(frames.HeliographicStonyhurst(obstime=t))   # lon exactly 0, lat == B0
differential_rotation(1*u.day, 30*u.deg, model="howard")            # Howard 1990, to 1e-9
```

## Make it yours

| In the recipe | Swap for | What to know |
|---|---|---|
| the two wrong formulas as controls | the `arcsin` check alone | **keep them.** `arctan(R/d)` is a flat *disc*'s angle and `R/d` the small-angle form; they miss by `1.085e-05` and `3.617e-06`, and the series predicts both (`x²/2`, `x²/6`) — so the controls' sizes are **derived, not observed**. |
| `model="howard"` | `snodgrass` / `allen` / `rigid` | four published laws ship. `rigid` returns the constant table's own 14.1844 °/day at **every** latitude, which is what makes it the control the three differential laws are measured against. |
| the synthetic 64×64 `Map` | your own FITS | **nothing is fetched.** sunpy's sample data downloads, which a hermetic run cannot do, so the run asserts the network is *off* before importing sunpy. [astropy](../astropy/README.md) covers the FITS/WCS layer underneath. |
| hourly `B0` over a year | daily | daily gives `max |B0| = 7.252146`, hourly `7.252200` — finer sampling moves it **away** from the pole-derived `7.251735`, so the 1.7″ residual is physical, not a sampling artifact. |
| the limb asserted at `1e-6` | the measured `2e-12` | the claim worth making is *arcsin not arctan*, which `1e-6` settles with 10× margin. `2e-12` would assert something about floating-point tangency instead. |
| `observer="earth"` | a spacecraft's position | every frame identity below is observer-relative; Stonyhurst longitude is *defined* from the observer's central meridian. |

**There is no fixture to leave or scale** — every reference is a defined constant or a closed form, which is why this recipe fetches nothing and needs no data pin. **Scale it** by pointing the frame checks at a real observation, which the measurement below does for a whole day of AIA.

## Shape, size, cost

One task on `m8g.large` (2 vCPU / 8 GiB), TTL 30m as a **backstop** with `cost_limit` $0.12 as the real guard. All of it — 8,784 hourly `B0` epochs, 1,000 Carrington rotations, a 50-step limb bisection — is **14 s** of a 1m17s window ([layout](../../patterns/layout-and-effective-cost.md)). **Which box — [measured](../../measurements/sunpy-real/README.md)** on all 343 AIA 171 Å images of 2017-09-06 (8.6 G pixels through the frames and back, plus a derotated flare-region cube), 16 workers on a `4xlarge`. **Take Graviton4:** the per-pixel transforms are 83–85% of the CPU, and it runs them 1.5× faster than AMD and 2.2× faster than Intel. All 22 assertions hold on every chip, including the limb to zero pixels in 8.6 G and FITS checksums recomputed from the raw bytes. Size by memory: each worker peaks at 1.9 GiB.

| chip | instance | tool wall | compute $ | **billed $/result** |
|---|---|---|---|---|
| **Graviton4** | `m8g.4xlarge` | **330 s** | **0.0659** | **0.0906** |
| Intel | `m8i.4xlarge` | 722 s | 0.1698 | 0.1919 |
| AMD | `m8a.4xlarge` | 501 s | 0.1356 | 0.1588 |

<details>
<summary>As shipped: an exact closed form with two analytically-sized controls, three definitional frame identities, four published rotation laws, and a constant that is not what it looks like</summary>

### The checks

| observable | assertion | arm64 | x86_64 |
|---|---|---|---|
| staged bytes | match pin | 1 of 1 | identical |
| sunpy / astropy / numpy | read from the running install | **8.0.0** / 8.0.1 / 2.5.4 | identical |
| **network** | **must be off before sunpy is imported** | `auto_download=False`, `allow_internet=False` | identical |
| **`radius`, `luminosity`, `mean distance`** | **exact, zero uncertainty, cited source unchanged** | 695700000 m, 3.828e26 W, 149597870700 m | identical |
| **`mean distance` == astropy's `au`** | **exact** | exact | identical |
| **astropy `R_sun` == sunpy `radius`** | **exact** (separate objects) | exact | identical |
| **`angular_radius` == `arcsin(R/d)`** | **`== 0` exactly** | **0.000e+00** | identical |
| control: flat-disc `arctan(R/d)` | must differ, by the series' `x²/2` | **1.085e-05**, ratio to prediction **1.000002** | identical |
| control: small-angle `R/d` | must differ, by the series' `x²/6` | **3.617e-06**, ratio **1.000010** | identical |
| **`sin(angular_radius)·d == R_sun`**, 53 epochs | **< 1e-12** while the angle swings > 2% | **3.347e-16**, swing **3.33%** | identical |
| **disc centre → Stonyhurst lon** | **exactly 0** | **0** | identical |
| **disc centre → lat == `B0`** | **< 1e-12** | **8.882e-16** | **0.000e+00** |
| **disc centre → radius == `R_sun`** | **< 1e-11** | **7.575e-13** | identical |
| **frame round-trip**, 5 on-disc points | **< 1e-10 arcsec** | **1.137e-13** | **1.137e-13** |
| control: round-trip with a +6 h observer | must move > 0.1 arcsec | **2.46–4.19 arcsec** (2e+13 apart) | identical |
| **off-disc rays (1.05/1.5/2.0×)** | **must be non-finite** | `nan` ×3 | identical |
| **limb boundary == `arcsin(R/d)`** | **< 1e-6, and inside the arctan gap** | **1.946e-12** (5,575,885× inside) | identical |
| **axial tilt: ICRF pole vs a year of `B0`** | **< 5e-3 deg** | **4.655e-04 deg** (1.7″) | identical |
| control: the same pole vs the *equatorial* pole | must differ by > 1 deg | **26.130** vs 7.252 | identical |
| **howard / snodgrass / allen / rigid** | **== published coefficients, < 1e-9 deg/day, 6 latitudes** | all four match | identical |
| **`rigid` == the table's sidereal rate** | **every latitude** | 14.1844 °/day, flat to **0** | identical |
| control: the three differential laws | must fall > 1 deg/day, monotone poleward | 2.80–3.63 °/day | identical |
| sidereal − synodic == Earth's mean motion | < 1e-4 deg/day | 9.121e-06 | identical |
| **Carrington `n` → time → `n`**, 1,000 rotations | **< 1e-7**, monotone | **3.530e-08** | identical |
| mean interval == cited synodic period | < 1e-3 d | **1.835e-04 d** | identical |
| control: the intervals must scatter | > 0.05 d | **0.1445 d** (787× the error) | identical |
| **rotation 1 vs Meeus' JD** | **within its stated ±0.1 d** | **0.011 d** | identical |
| WCS pixel→world→pixel | < 1e-9 pix | 1.066e-11 | identical |
| `average angular size` | *recorded, not asserted* | 959.63″ vs 959.231″ derived | identical |
| `get_sun` vs `earth_distance` | *recorded* — a shared ephemeris | 1.112e-16 | 2.224e-16 |
| determinism | repeat calls bit-identical | identical | identical |

**On x86_64** (`m8i.large`, the `astro-e2ee19affec3` twin: 349 of 364 versions identical, sunpy 8.0.0, astropy 8.0.1 and numpy 2.5.4 among them; the 14 that differ are minor bumps listed in the lock header). Every assertion holds, and every defined constant, closed form, control and published law is identical. Only last-ulp residuals of the coordinate transforms move: the disc-centre latitude lands exactly on `B0` (arm64 8.882e-16), the per-point frame round-trips shuffle among 0, 5.7e-14 and 1.1e-13 arcsec with the same worst case, and `get_sun` vs `earth_distance` is 2.224e-16 (arm64 1.112e-16). Each is one or two doubles of round-off in a chain of trigonometry, the same size on both chips, so these are neither a fixed point that must agree nor a trajectory, and nothing asserted is near its bound.

### The apparent radius of a sphere, and two ways to get it wrong

A sight line to the limb is **tangent** to the photosphere, so the Sun's angular radius is
`arcsin(R/d)`. Two formulas a careful person might write instead are `arctan(R/d)` — which is
the angle subtended by a flat **disc** of radius `R` facing the observer — and the small-angle
`R/d`:

```text
sunpy          960.935629970 arcsec
arcsin(R/d)    960.935629970      rel 0.000e+00      <- exact
arctan(R/d)    960.925202101      rel 1.085e-05      <- a disc, not a sphere
linear R/d     960.932153961      rel 3.617e-06      <- small angle
```

**What makes these controls rather than coincidences is that the series predicts their sizes.**
`arcsin x − arctan x = x³/2` and `arcsin x − x = x³/6`, so relative to the angle the two wrong
forms must sit `x²/2` and `x²/6` away — and they do, to 2 parts per million and 1 part in 100,000
of the prediction. Staging verifies that at perihelion, 1 au and aphelion before the run, so the
expected gap exists independently of what sunpy returns.

The same geometry is then confirmed **through different code**. A Helioprojective point off the
disc has no intersection with the Sun, and the transform decides that from a ray–sphere
discriminant that changes sign exactly where `sin θ = R/d`. Bisecting the finite/NaN boundary:

```text
finite out to   0.99x the angular radius      radius 695700.000 km
NaN beyond      1.05x
boundary at     1.000000000002 x              1.946e-12 away
```

That is **5,575,885× inside** the `1.085e-05` the flat-disc form would need — the limb and the
formula agree, through machinery that never calls `angular_radius`. Bounded at `1e-6` rather than
at the measured `2e-12`: the claim worth asserting is *arcsin, not arctan*, and `1e-6` settles it
with 10× margin, while `2e-12` would be a claim about floating-point tangency.

And across a year the angular radius swings **3.33%** as Earth's distance changes, while
`sin(angular_radius)·d` stays `R_sun` to **3.347e-16** — an invariance on a quantity that is
visibly moving.

### The NaN that would have passed every check

`nan > tol` is **False**, so a NaN satisfies every one-sided bound on this page. Off-disc
coordinates produce exactly that, which is how this recipe could have shipped a green run over
nothing — the same trap that let a swapped width tuple pass a conservation check in
[yt](../yt/README.md). Every bound here is paired with an explicit finiteness test, and the
off-disc NaN is asserted **positively** as its own control instead of being avoided.

### Three identities from one transform

Helioprojective `(0, 0)` is the centre of the disc, and where it lands is fixed by definition
rather than by computation:

```text
Stonyhurst longitude    0                      exactly — Stonyhurst longitude IS measured
                                               from the observer's central meridian
latitude                6.106636660074 deg     == B0, to 8.882e-16
radius                  695699.999999473 km    == R_sun, to 7.575e-13 — the ray lands
                                               on the photosphere
```

The round-trip Helioprojective → Stonyhurst → Helioprojective is exact to `1.137e-13` arcsec over
five on-disc points. On its own that proves little, so the control transforms the *same*
heliographic point back as if observed six hours later: the Sun has rotated and Earth has moved,
and it lands **2.46 to 4.19 arcsec** away. Thirteen orders of magnitude between the identity and
the nearest wrong answer.

### The axial tilt, by two unrelated routes

The inclination of the solar equator comes out of a static ICRF pole (`alpha_0`, `delta_0`,
Archinal et al. 2018) pushed through astropy's ecliptic transform, and separately out of a year of
ephemeris `B0` values:

```text
from the pole constants      7.251734877 deg
max|B0|, 8784 hourly epochs  7.252200382 deg      1.7 arcsec apart
the same pole, measured
against the EQUATORIAL pole  26.130000 deg        <- the control: the transform is load-bearing
```

Exact agreement is not expected and the recipe does not ask for it: `B0` is an **apparent**
quantity (light-travel and aberration corrected) while the pole constant is a **mean** direction,
and hourly sampling moves the maximum *away* from the pole-derived value rather than toward it —
so 1.7″ is physics, not resolution. Bounded at `5e-3` deg, which a wrong pole or a wrong obliquity
would miss by degrees. Staging derives the same angle by hand from a literal J2000 obliquity
(astropy is not installed locally), so the two routes are independent of each other.

### Four published rotation laws, and the flat one is the control

Howard 1990 and Snodgrass & Ulrich 1990 are `A + B sin²θ + C sin⁴θ` in µrad/s; Allen's is
`14.44 − 3.0 sin²θ` in °/day. All three reproduce to better than `1e-9` °/day at six latitudes:

```text
latitude        0      15      30      45      60      75     equator->75
howard      14.3263 14.1762 13.6822 12.8090 11.7070 10.7550   -3.571
snodgrass   14.1135 13.9892 13.5423 12.6779 11.5201 10.4866   -3.627
allen       14.4400 14.2390 13.6900 12.9400 12.1900 11.6410   -2.799
rigid       14.1844 14.1844 14.1844 14.1844 14.1844 14.1844    0.000
```

`rigid` is the control in two directions at once. It must be **flat** in latitude (it is, to
`0.000e+00`) while the other three fall monotonically poleward — otherwise matching coefficients
would say nothing about *differential* rotation. And it must equal the constant table's own
`sidereal rotation rate` of 14.1844 °/day, which ties the model code to the cited constant rather
than to a number embedded twice.

The sidereal→synodic offset is a free physical check: `14.1844 − 13.1988 = 0.98560` °/day against
Earth's mean orbital motion `360/365.25636 = 0.98561`, agreeing to `9.1e-06` — bounded at `1e-4`,
which is the granularity of sunpy's own four-decimal constant.

### Carrington bookkeeping, with a control against a trivial formula

```text
n -> time -> n, rotations 1500-2499     max |diff| 3.530e-08, monotone
mean interval                           27.275444646 d
cited mean synodic period               27.275261151 d        1.835e-04 d apart
interval spread                         0.144467 d (27.1975 .. 27.3420)
rotation 1                              1853-11-09T21:51:18   JD(TT) 2398167.411004
Meeus 1998                              2398167.4 +/- 0.1     0.011 d — inside its uncertainty
```

The mean matching the cited synodic period would be vacuous if `carrington_rotation_time` were
just `n × P`, so the recipe asserts the intervals **scatter**: they span 0.1445 d, making the mean
787× tighter than the spread it is drawn from. And rotation 1 is compared at Meeus' **stated
±0.1 d**, not at sunpy's precision — the same discipline as taking PDAL's four decimals in
[laspy](../laspy/README.md).

### A constant that is not what it looks like

Recorded, not asserted, because it is a correctly cited value rather than an error:

```text
'average angular size'              959.63 arcsec   [Allen's Astrophysical Quantities 4th Ed.]
arcsin(radius / mean distance)       959.231186      <- from the same table's IAU 2015 radius
arcsin(696000 km / mean distance)    959.644829      <- a PRE-2015 radius, 0.0148 away
```

The table cites five different sources — IAU 2015, IAU 2012, Archinal et al. 2018, Allen's 4th
edition and Meeus 1998 — and **is not internally consistent**: the angular size predates the 2015
nominal radius and is 0.4″ from what the table's own radius implies. The run asserts only that the
legacy explanation still holds (if `average angular size` ever moves closer to the IAU value, the
note is stale and the run fails), so nobody derives one constant from another here.

Likewise recorded: `astropy.get_sun(t).distance` and `sun.earth_distance(t)` agree to `1.112e-16`
because they are the **same ephemeris**. That is internal consistency, not independent
confirmation, and the page says so rather than counting it as a cross-check — unlike the
genuinely independent pair in [specutils](../specutils/README.md).

### Pins

| | |
|---|---|
| data | **none, and none is possible** — every reference is an IAU/Archinal/Allen/Meeus constant or a closed form. sunpy's sample data downloads, so the run asserts the network is off |
| checks | `identities.py`, pinned by sha256 |
| image | `quay.io/aarchsci/astro@sha256:c09e88de976ec1184d…` — sunpy 8.0.0, astropy 8.0.1, specutils 2.4.0, photutils, healpy, reproject, yt, rebound, numpy 2.5.4, python 3.14.8 |

cosign-verified against `playgroundlogic/aarchsci`; the signature covers the **manifest-list**
digest, so verify the tag and pin the arm64 digest. Same digest as
[specutils](../specutils/README.md); [yt](../yt/README.md) stays on the older astro image it was
verified against, since repinning is a change to the input rather than a text edit.

### Run + verify

```sh
make stage RECIPE=sunpy
spawn task run --spec "$(make -s spec RECIPE=sunpy)" --wait
spawn task run --spec "$(make -s spec RECIPE=sunpy ARCH=x86_64)" --wait   # x86 twin, writes runs-x86_64/
aws s3 cp "s3://$(make -s print-bucket)/runs/sunpy/r1/score.tsv" -
```

Fails on a pin mismatch, a network setting left enabled, a defined constant that is not exactly
its resolution's value or no longer cites it, an angular radius not exactly `arcsin(R/d)`, a wrong
form that agrees with sunpy or whose error is not the size the series predicts, an invariance
tested against a quantity that barely moved, a disc centre off longitude zero or off `B0` or off
the photosphere, a round-trip that loses more than `1e-10` arcsec or one a six-hour observer error
would not have broken, an off-disc ray that returns a finite radius, a limb outside `1e-6` of the
formula, a tilt mismatch beyond `5e-3` deg, any rotation model departing `1e-9` °/day from its
published coefficients, a `rigid` model that varies with latitude or a differential one that does
not, a Carrington round-trip beyond `1e-7`, intervals too uniform to be an ephemeris, a rotation-1
epoch outside Meeus' stated uncertainty, a NaN anywhere a bound is applied, or a stale legacy-
constant note — but check the bucket regardless
([exit 0 isn't proof](../../practices/container-path.md)).

### Not covered

**Real solar data, in this recipe.** No FITS is read here. The [measurement](../../measurements/sunpy-real/README.md)
covers the AIA loader, `submap`, `reproject_to` and `propagate_with_solar_surface` on a day of AIA;
`Map.rotate` and the HMI/LASCO loaders and their metadata fixups are untouched by either.
Nothing here exercises `Fido`, the search and download layer, which is unavoidable: a hermetic
recipe cannot query VSO or JSOC, so the half of sunpy that talks to data archives has no coverage
and would need a pinned local mirror.

**Timeseries and spectra** (`sunpy.timeseries`, GOES/RHESSI light curves). **Differential rotation
applied to a map** (`solar_rotate_coordinate`, `differential_rotate`) — only the rate law is
checked, not the warp that uses it. **Spacecraft frames**: every identity here uses
`observer="earth"`, so the Heliocentric/Helioprojective frames as seen from STEREO, SDO or Solar
Orbiter, where the observer position is the interesting part, are unchecked. **The screen
assumptions** (`assume_spherical_screen`) that decide what off-disc coordinates mean — this recipe
asserts the NaN rather than configuring it away. **Carrington longitude of a feature over time**,
the practical use of the rotation machinery.

</details>
