#!/usr/bin/env python3
"""sunpy on Graviton4: solar geometry against published constants and closed forms.

Solar physics is unusually well supplied with references that need no measurement, so
almost nothing here is a band on an observed value:

 1. DEFINED CONSTANTS. The IAU 2015 nominal solar radius is 695,700 km *by resolution*,
    not by measurement -- uncertainty exactly zero. Same for the luminosity (3.828e26 W)
    and, from IAU 2012, the astronomical unit (149,597,870,700 m exactly).

 2. A CLOSED FORM WITH TWO PLAUSIBLE WRONG ANSWERS. The apparent angular radius of a
    SPHERE is the tangent half-angle arcsin(R/d) -- the sight line grazes the limb. Two
    implementations a careful person might write instead are arctan(R/d) (which is the
    angle subtended by a flat DISC of radius R, not a sphere) and the small-angle R/d.
    Both are wrong, and the series says BY HOW MUCH: arcsin - arctan = x^3/2 and
    arcsin - x = x^3/6 for x = R/d. So the controls' magnitudes are predicted, not merely
    observed, which is what separates a control from a coincidence.

 3. DEFINITIONAL IDENTITIES BETWEEN FRAMES. Helioprojective (0, 0) is the centre of the
    disc, so it must map to Stonyhurst longitude EXACTLY zero (that is what Stonyhurst
    longitude means), to latitude exactly B0, and to a radius of exactly R_sun -- the ray
    lands on the photosphere. Three exact statements from one transform.

 4. FOUR PUBLISHED ROTATION LAWS. Howard, Snodgrass and Allen are each A + B sin^2 + C
    sin^4 with published coefficients, and sunpy's 'rigid' model must return the constant
    table's own sidereal rotation rate at every latitude.

THE TRAP THIS RECIPE IS BUILT AROUND: a Helioprojective point OUTSIDE the disc does not
intersect the Sun, and the transform returns NaN. `nan > tol` is False, so a NaN sails
through every one-sided bound here -- the same way a swapped width tuple once passed a
conservation check in the yt recipe. Every bound below is paired with an explicit
finiteness test, and the off-disc NaN is asserted POSITIVELY as its own control.

Hermeticity: astropy's IERS tables auto-download by default. This disables the network
before importing anything that could use it, and asserts it is disabled, so a passing run
is evidence the recipe fetched nothing.

Run with `python3 -u` so a killed run still has its output in command.log.
"""
import sys

import numpy as np

# ---- the network goes down BEFORE anything that might reach for it is imported ----
from astropy.utils import iers
iers.conf.auto_download = False
iers.conf.auto_max_age = None
from astropy.utils.data import conf as data_conf
data_conf.allow_internet = False

ROWS = []


def rec(k, v):
    """Always str() the value.

    `"%s" % obj` unpacks a tuple-like object as format arguments, which is how four
    earlier runs in this catalog died inside a line whose only job was to describe the
    run. astropy Quantity and Constant objects are exactly that shape.
    """
    v = v if isinstance(v, str) else str(v)
    print("%s\t%s" % (k, v))
    ROWS.append((k, v))


def die(msg):
    print("FAIL: %s" % msg)
    sys.exit(1)


def ver(label, fn):
    """A descriptive line must never be able to fail the run."""
    try:
        rec(label, fn())
    except Exception as exc:
        rec(label, "unknown (%s)" % type(exc).__name__)


def finite(name, *vals):
    """A one-sided bound cannot catch a NaN, so every bound here is paired with this."""
    for v in vals:
        if not np.all(np.isfinite(np.asarray(v, dtype="float64"))):
            die("%s is not finite; a NaN would pass every bound below silently" % name)


import astropy.units as u
import astropy.constants as apc
from astropy.time import Time
from astropy.coordinates import SkyCoord, ICRS, BarycentricMeanEcliptic, get_sun
import sunpy
import sunpy.map
import sunpy.sun.constants as sunconst
from sunpy.coordinates import frames, sun
from sunpy.sun.models import differential_rotation

ver("sunpy", lambda: sunpy.__version__)
ver("astropy", lambda: __import__("astropy").__version__)
ver("numpy", lambda: np.__version__)
rec("network", "iers.auto_download=%s allow_internet=%s"
    % (iers.conf.auto_download, data_conf.allow_internet))
if iers.conf.auto_download or data_conf.allow_internet:
    die("the network is still enabled; a passing run would not be evidence of a "
        "hermetic recipe")

# -------------------------------------------------------------- published references
R_IAU = 695700000.0                 # m, IAU 2015 Resolution B3 -- defined, not measured
L_IAU = 3.828e26                    # W, IAU 2015 Resolution B3
AU_IAU = 149597870700.0             # m, IAU 2012 Resolution B2 -- the definition of the au
SIDEREAL_RATE = 14.1844             # deg/d, Archinal et al. 2018
SYNODIC_PERIOD = 27.2752611514      # d, Archinal et al. 2018
CR1_JD_TT, CR1_UNC = 2398167.4, 0.1  # Meeus 1998, with its stated uncertainty
HOWARD = (2.894, -0.428, -0.370)    # murad/s, Howard, Harvey & Forgach 1990
SNODGRASS = (2.851, -0.343, -0.474)  # murad/s, Snodgrass & Ulrich 1990
ALLEN_A, ALLEN_B = 14.44, -3.0      # deg/d, Allen's Astrophysical Quantities
ALLEN_ANGULAR = 959.63              # arcsec, Allen 4th ed. -- a LEGACY value; see below
R_PRE2015 = 696.0e6                 # m, the radius Allen's angular size implies
SIDEREAL_YEAR = 365.25636           # d, for Earth's mean orbital motion

T = Time("2026-10-12T00:00:00", scale="utc")
rec("epoch", T.isot)

# ====================================================== 1. the constant table, as cited
for key, want, src in (("radius", R_IAU, "IAU 2015 Resolution B 3"),
                       ("luminosity", L_IAU, "IAU 2015 Resolution B 3"),
                       ("mean distance", AU_IAU, "IAU 2012 Resolution B2")):
    c = sunconst.get(key)
    got = c.si.value
    rec("const_%s" % key.replace(" ", "_"), "%.12g %s  [%s]" % (got, c.unit, c.reference))
    if got != want:
        die("sunpy's %s is %.17g, not the defined value %.17g; these are fixed by "
            "resolution and an exact comparison is the right one" % (key, got, want))
    if src not in str(c.reference):
        die("sunpy's %s no longer cites %s but %r; the assertion above compares against "
            "that source's number" % (key, src, c.reference))
    if c.uncertainty != 0:
        die("sunpy's %s carries uncertainty %g; a defined constant has none, so this is "
            "no longer the quantity being asserted" % (key, c.uncertainty))
rec("defined_constants", "radius, luminosity and mean distance match their resolutions "
    "exactly, with zero uncertainty")

# The astronomical unit is DEFINED, so the constant table and the unit system must agree.
if sunconst.get("mean distance").si.value != (1 * u.au).si.value:
    die("sunpy's mean distance and astropy's au disagree, but IAU 2012 defines both")
rec("au_identity", "sunpy 'mean distance' == astropy's au, exactly")

# Two independently maintained tables carrying one definition.
if apc.R_sun.si.value != sunconst.radius.si.value:
    die("astropy's R_sun (%.17g) and sunpy's radius (%.17g) differ"
        % (apc.R_sun.si.value, sunconst.radius.si.value))
rec("cross_table", "astropy R_sun == sunpy radius (separate objects: %s)"
    % (apc.R_sun is not sunconst.radius))

# ========================= 2. the angular radius is a sphere's tangent half-angle
d = sun.earth_distance(T)
ar = sun.angular_radius(T)
finite("angular_radius", ar.to_value(u.arcsec), d.to_value(u.m))
x = R_IAU / d.to_value(u.m)
ARC = 3600.0 * 180.0 / np.pi
forms = {"arcsin": np.arcsin(x) * ARC,       # a sphere: the sight line grazes the limb
         "arctan": np.arctan(x) * ARC,       # a flat disc of radius R, facing the observer
         "linear": x * ARC}                  # the small-angle approximation
got = ar.to_value(u.arcsec)
rec("earth_distance", "%.12f AU" % d.to_value(u.AU))
rec("angular_radius", "%.9f arcsec" % got)
rel = {}
for name, v in forms.items():
    rel[name] = abs(got - v) / v
    rec("angular_radius_%s" % name, "%.9f arcsec   rel %.3e" % (v, rel[name]))

if rel["arcsin"] != 0.0:
    die("sunpy's angular radius is %.3e from arcsin(R/d); the apparent radius of a sphere "
        "is the tangent half-angle and nothing here is approximated" % rel["arcsin"])
rec("angular_radius_exact", "sunpy == arcsin(R/d) exactly (0.000e+00)")

# THE CONTROLS, and their sizes are PREDICTED by the series rather than observed:
#   arcsin(x) - arctan(x) = x^3/2 + O(x^5)      arcsin(x) - x = x^3/6 + O(x^5)
for name, predicted in (("arctan", x * x / 2.0), ("linear", x * x / 6.0)):
    rec("control_%s_predicted" % name, "measured rel %.4e, series predicts %.4e, ratio %.6f"
        % (rel[name], predicted, rel[name] / predicted))
    if rel[name] == 0.0:
        die("the %s form agrees with sunpy exactly, so the arcsin result above does not "
            "identify which formula sunpy uses -- the comparison cannot discriminate"
            % name)
    if abs(rel[name] / predicted - 1.0) > 1e-3:
        die("the %s form is %.4e away where the series predicts %.4e; the control's "
            "magnitude should be analytic, so a mismatch means the geometry is not what "
            "this check assumes" % (name, rel[name], predicted))
rec("controls_graded", "flat-disc arctan off by %.3e, small-angle off by %.3e -- both "
    "plausible implementations, both refuted" % (rel["arctan"], rel["linear"]))

# AND AN INVARIANCE ACROSS A YEAR: the angular radius swings by ~3% as Earth's distance
# changes, but sin(angular_radius) * d is R_sun at every epoch, by construction.
ts = Time("2026-01-01") + np.arange(0, 365, 7) * u.day
ds = sun.earth_distance(ts).to_value(u.km)
ars = sun.angular_radius(ts).to_value(u.rad)
finite("yearly angular_radius", ars, ds)
prod = np.sin(ars) * ds
R_km = R_IAU / 1000.0
worst = float(np.abs(prod - R_km).max() / R_km)
swing = (ars.max() - ars.min()) / ars.mean()
rec("invariance_sin_ar_times_d", "n=%d epochs, max rel |sin(ar)*d - R_sun| = %.3e, while "
    "angular_radius itself swings %.2f%%" % (len(ts), worst, 100 * swing))
if worst > 1e-12:
    die("sin(angular_radius) * d departs from R_sun by %.3e; it is R_sun identically"
        % worst)
if swing < 0.02:
    die("the angular radius only varied %.3f%% over the year, so the invariance above is "
        "not being tested against a moving quantity" % (100 * swing))

# ============================ 3. frame transforms: three exact definitional identities
hp0 = SkyCoord(0 * u.arcsec, 0 * u.arcsec,
               frame=frames.Helioprojective(obstime=T, observer="earth"))
h0 = hp0.transform_to(frames.HeliographicStonyhurst(obstime=T))
finite("disc centre", h0.lon.to_value(u.deg), h0.lat.to_value(u.deg),
       h0.radius.to_value(u.km))
b0 = sun.B0(T).to_value(u.deg)
rec("disc_centre_lon", "%.17g deg" % h0.lon.to_value(u.deg))
rec("disc_centre_lat", "%.12f deg (B0 = %.12f, diff %.3e)"
    % (h0.lat.to_value(u.deg), b0, abs(h0.lat.to_value(u.deg) - b0)))
rec("disc_centre_radius", "%.9f km (R_sun = %.1f, rel %.3e)"
    % (h0.radius.to_value(u.km), R_km, abs(h0.radius.to_value(u.km) - R_km) / R_km))
if h0.lon.to_value(u.deg) != 0.0:
    die("the centre of the disc is at Stonyhurst longitude %.17g, not 0; Stonyhurst "
        "longitude is DEFINED from the central meridian" % h0.lon.to_value(u.deg))
if abs(h0.lat.to_value(u.deg) - b0) > 1e-12:
    die("the centre of the disc is at latitude %.12f but B0 is %.12f; B0 IS the latitude "
        "of the sub-observer point" % (h0.lat.to_value(u.deg), b0))
if abs(h0.radius.to_value(u.km) - R_km) / R_km > 1e-11:
    die("the ray through the disc centre landed %.6e km from the photosphere; an on-disc "
        "Helioprojective point intersects the solar surface by construction"
        % abs(h0.radius.to_value(u.km) - R_km))
rec("disc_centre", "lon exactly 0, lat == B0, radius == R_sun -- three identities from "
    "one transform")

# the round-trip, over on-disc points, with a control that proves it can fail
GRID = [(0, 0), (300, -200), (-500, 400), (600, 100), (-700, 300)]
worst_rt, worst_ctl = 0.0, float("inf")
for tx, ty in GRID:
    if np.hypot(tx, ty) >= got:
        die("grid point (%d, %d) is outside the %.1f arcsec disc, so it cannot intersect "
            "the Sun and the round-trip would be NaN" % (tx, ty, got))
    c = SkyCoord(tx * u.arcsec, ty * u.arcsec,
                 frame=frames.Helioprojective(obstime=T, observer="earth"))
    h = c.transform_to(frames.HeliographicStonyhurst(obstime=T))
    b = h.transform_to(frames.Helioprojective(obstime=T, observer="earth"))
    finite("round-trip (%d,%d)" % (tx, ty), b.Tx.to_value(u.arcsec), b.Ty.to_value(u.arcsec))
    err = max(abs(b.Tx.to_value(u.arcsec) - tx), abs(b.Ty.to_value(u.arcsec) - ty))
    # THE CONTROL: transform back as if the SAME heliographic point were observed six
    # hours later. The Sun has rotated and Earth has moved, so it must land elsewhere.
    b2 = h.transform_to(frames.Helioprojective(obstime=T + 6 * u.hour, observer="earth"))
    ctl = max(abs(b2.Tx.to_value(u.arcsec) - tx), abs(b2.Ty.to_value(u.arcsec) - ty))
    worst_rt, worst_ctl = max(worst_rt, err), min(worst_ctl, ctl)
    rec("roundtrip_%d_%d" % (tx, ty), "%.3e arcsec | +6h observer moves it %.4f arcsec"
        % (err, ctl))
rec("roundtrip_worst", "%.3e arcsec over %d on-disc points" % (worst_rt, len(GRID)))
if worst_rt > 1e-10:
    die("a Helioprojective -> Stonyhurst -> Helioprojective round-trip lost %.3e arcsec; "
        "it is the identity" % worst_rt)
if worst_ctl < 0.1:
    die("shifting the observation time by six hours moved the point only %.3e arcsec, so "
        "the round-trip above would pass even against the wrong observer" % worst_ctl)
rec("roundtrip_control", "exact to %.1e arcsec, while a 6 h observer error moves it at "
    "least %.2f arcsec -- %.0e apart" % (worst_rt, worst_ctl, worst_ctl / max(worst_rt, 1e-300)))

# THE NaN CONTROL, asserted positively: off the disc there is no intersection.
for frac in (1.05, 1.5, 2.0):
    c = SkyCoord(frac * got * u.arcsec, 0 * u.arcsec,
                 frame=frames.Helioprojective(obstime=T, observer="earth"))
    r = c.transform_to(frames.HeliographicStonyhurst(obstime=T)).radius.to_value(u.km)
    rec("offdisc_%.2fR" % frac, "radius %s" % ("%.3f km" % r if np.isfinite(r) else "nan"))
    if np.isfinite(r):
        die("a sight line %.2fx the angular radius off centre returned a finite radius of "
            "%.3f km; it misses the Sun entirely and must not intersect it" % (frac, r))
# and just inside the limb it must still hit, so the NaN is about geometry, not failure
c = SkyCoord(0.99 * got * u.arcsec, 0 * u.arcsec,
             frame=frames.Helioprojective(obstime=T, observer="earth"))
r_in = c.transform_to(frames.HeliographicStonyhurst(obstime=T)).radius.to_value(u.km)
finite("just inside the limb", r_in)
if abs(r_in - R_km) / R_km > 1e-9:
    die("a point at 0.99x the angular radius landed %.6e km from the photosphere" % abs(r_in - R_km))
rec("limb_boundary", "finite at 0.99x the angular radius (%.3f km), NaN beyond 1.05x -- "
    "the transform's limb is where angular_radius puts it" % r_in)
# WHERE exactly? A bisection localises the boundary, and this is a SECOND, INDEPENDENT
# route to the headline claim: the transform decides intersection from a ray-sphere
# discriminant, which changes sign exactly where sin(theta) = R/d -- i.e. at arcsin(R/d),
# through code that never calls angular_radius. So the limb refutes the flat-disc arctan
# a second time, geometrically rather than by formula.
lo, hi = 0.99, 1.05
for _ in range(50):
    mid = 0.5 * (lo + hi)
    rr = SkyCoord(mid * got * u.arcsec, 0 * u.arcsec,
                  frame=frames.Helioprojective(obstime=T, observer="earth")
                  ).transform_to(frames.HeliographicStonyhurst(obstime=T)).radius.to_value(u.km)
    lo, hi = (mid, hi) if np.isfinite(rr) else (lo, mid)
rec("limb_bisection", "the finite/NaN boundary sits at %.12f x angular_radius (%.3e away)"
    % (lo, abs(lo - 1.0)))
# Bounded at 1e-6 rather than at the 2e-12 measured: the claim worth making is that the
# limb is at arcsin(R/d) and NOT at arctan(R/d) (1.085e-05 away), which 1e-6 settles with
# 10x margin. Asserting the measured 2e-12 would be a claim about floating-point tangency.
if abs(lo - 1.0) > 1e-6:
    die("the intersection boundary sits at %.12f x the angular radius; the ray-sphere "
        "discriminant changes sign at arcsin(R/d) exactly, so the limb and the formula "
        "must agree" % lo)
if abs(lo - 1.0) > rel["arctan"]:
    die("the limb is %.3e from the computed angular radius, further than the %.3e that "
        "separates arcsin from arctan -- so this check cannot tell the two apart"
        % (abs(lo - 1.0), rel["arctan"]))
rec("limb_confirms_arcsin", "the ray-sphere limb agrees with arcsin(R/d) to %.3e, %.0fx "
    "inside the %.3e that the flat-disc arctan would need -- an independent route to the "
    "same geometry" % (abs(lo - 1.0), rel["arctan"] / max(abs(lo - 1.0), 1e-300),
                       rel["arctan"]))

# ============================== 4. the solar axial tilt, by two unrelated routes
pole = SkyCoord(sunconst.get("alpha_0").to_value(u.deg) * u.deg,
                sunconst.get("delta_0").to_value(u.deg) * u.deg, frame=ICRS())
tilt_const = 90.0 - pole.transform_to(BarycentricMeanEcliptic()).lat.to_value(u.deg)
tyr = Time("2026-01-01") + np.arange(0, 366, 1.0 / 24) * u.day
b0y = sun.B0(tyr).to_value(u.deg)
finite("B0 over a year", b0y)
tilt_eph = float(np.abs(b0y).max())
rec("tilt_from_pole", "%.9f deg (alpha_0 %.2f, delta_0 %.2f -> ecliptic latitude)"
    % (tilt_const, sunconst.get("alpha_0").to_value(u.deg),
       sunconst.get("delta_0").to_value(u.deg)))
rec("tilt_from_B0", "%.9f deg (max|B0| over %d hourly epochs)" % (tilt_eph, len(tyr)))
rec("tilt_difference", "%.3e deg (%.1f arcsec), rel %.3e"
    % (abs(tilt_eph - tilt_const), 3600 * abs(tilt_eph - tilt_const),
       abs(tilt_eph - tilt_const) / tilt_const))
if abs(tilt_eph - tilt_const) > 5e-3:
    die("the tilt from the ICRF pole constants (%.6f) and from a year of B0 (%.6f) differ "
        "by %.3e deg; they describe the same angle" % (tilt_const, tilt_eph,
                                                       abs(tilt_eph - tilt_const)))
# the control: the same computation against the EQUATORIAL pole must give a different
# angle, or the transform above is not doing anything.
eq = 90.0 - pole.dec.to_value(u.deg)
rec("tilt_control", "the same pole is %.6f deg from the EQUATORIAL pole, not %.6f -- the "
    "ecliptic transform is load-bearing" % (eq, tilt_const))
if abs(eq - tilt_const) < 1.0:
    die("the solar pole is %.6f deg from the equatorial pole and %.6f from the ecliptic "
        "pole; if those agree the frame transform is a no-op" % (eq, tilt_const))

# ============================ 5. differential rotation against four published laws
def murad_law(coeffs, lat_deg):
    """A + B sin^2 + C sin^4 in murad/s, as deg/day."""
    s2 = np.sin(np.radians(lat_deg)) ** 2
    rate = (coeffs[0] + coeffs[1] * s2 + coeffs[2] * s2 * s2) * 1e-6   # rad/s
    return rate * 86400.0 * 180.0 / np.pi


LATS = (0.0, 15.0, 30.0, 45.0, 60.0, 75.0)
rates = {}
for model, closed in (("howard", lambda L: murad_law(HOWARD, L)),
                      ("snodgrass", lambda L: murad_law(SNODGRASS, L)),
                      ("allen", lambda L: ALLEN_A + ALLEN_B * np.sin(np.radians(L)) ** 2),
                      ("rigid", lambda L: SIDEREAL_RATE)):
    seq = []
    for lat in LATS:
        v = differential_rotation(1 * u.day, lat * u.deg, model=model).to_value(u.deg)
        w = float(closed(lat))
        finite("%s at lat %.0f" % (model, lat), v, w)
        seq.append(v)
        if abs(v - w) > 1e-9:
            die("the %s model gives %.12f deg/day at latitude %.0f where its published "
                "coefficients give %.12f" % (model, v, lat, w))
    rates[model] = seq
    rec("rotation_%s" % model, "matches its published coefficients at all %d latitudes "
        "(worst |diff| < 1e-9 deg/day); equator %.9f deg/day" % (len(LATS), seq[0]))

# rigid must BE the constant table's sidereal rate, at every latitude -- it ties the
# model code to the cited constant.
if any(abs(v - SIDEREAL_RATE) > 1e-12 for v in rates["rigid"]):
    die("the rigid model is not the %.4f deg/day sidereal rate at every latitude: %s"
        % (SIDEREAL_RATE, rates["rigid"]))
if abs(sunconst.get("sidereal rotation rate").to_value(u.deg / u.day) - SIDEREAL_RATE) > 0:
    die("the sidereal rotation rate constant is no longer %.4f deg/day" % SIDEREAL_RATE)
rec("rotation_rigid_ties_constant", "rigid == the table's sidereal rotation rate "
    "(%.4f deg/day) at every latitude" % SIDEREAL_RATE)

# THE CONTROL: rigid is flat in latitude and the three differential laws are not. If the
# differential models were flat too, matching their coefficients would prove nothing about
# differential rotation.
if max(rates["rigid"]) - min(rates["rigid"]) != 0.0:
    die("the rigid model varies with latitude by %.3e deg/day"
        % (max(rates["rigid"]) - min(rates["rigid"])))
for model in ("howard", "snodgrass", "allen"):
    seq = rates[model]
    drop = seq[0] - seq[-1]
    if drop < 1.0:
        die("the %s model falls only %.3f deg/day from the equator to 75 deg; that is "
            "barely differential and the contrast with rigid would be weak" % (model, drop))
    if not all(seq[i] > seq[i + 1] for i in range(len(seq) - 1)):
        die("the %s model is not monotone decreasing toward the pole: %s" % (model, seq))
    rec("rotation_%s_gradient" % model, "falls %.3f deg/day equator -> 75 deg, monotone"
        % drop)
rec("rotation_control", "rigid is flat to 0.000e+00 while all three differential laws "
    "fall monotonically poleward -- the models are distinguishable")

# the sidereal/synodic offset IS Earth's mean orbital motion
sid = differential_rotation(1 * u.day, 0 * u.deg, model="rigid",
                            frame_time="sidereal").to_value(u.deg)
syn = differential_rotation(1 * u.day, 0 * u.deg, model="rigid",
                            frame_time="synodic").to_value(u.deg)
earth_motion = 360.0 / SIDEREAL_YEAR
rec("sidereal_minus_synodic", "%.9f deg/day; Earth's mean motion 360/%.5f = %.9f "
    "(diff %.3e)" % (sid - syn, SIDEREAL_YEAR, earth_motion,
                     abs((sid - syn) - earth_motion)))
if abs((sid - syn) - earth_motion) > 1e-4:
    die("the sidereal-synodic offset is %.9f deg/day but Earth's mean orbital motion is "
        "%.9f; the synodic correction is that motion" % (sid - syn, earth_motion))

# ================================================= 6. Carrington rotation bookkeeping
ns = np.arange(1500, 2500)
tms = sun.carrington_rotation_time(ns)
jd = np.asarray(tms.jd, dtype="float64")
finite("Carrington rotation times", jd)
dif = np.diff(jd)
back = np.asarray(sun.carrington_rotation_number(tms), dtype="float64")
finite("Carrington rotation numbers", back)
rt = float(np.abs(back - ns).max())
rec("carrington_roundtrip", "max |n - n'| = %.3e over rotations %d-%d"
    % (rt, ns[0], ns[-1]))
if rt > 1e-7:
    die("number -> time -> number drifts by %.3e over %d rotations; it is an identity"
        % (rt, len(ns)))
if not np.all(dif > 0):
    die("Carrington rotation times are not monotone in rotation number")
rec("carrington_mean_interval", "%.9f d over %d rotations; the cited mean synodic period "
    "is %.9f d (diff %.3e)" % (dif.mean(), len(ns), SYNODIC_PERIOD,
                               abs(dif.mean() - SYNODIC_PERIOD)))
if abs(dif.mean() - SYNODIC_PERIOD) > 1e-3:
    die("the mean interval between Carrington rotations is %.9f d but the cited mean "
        "synodic period is %.9f d" % (dif.mean(), SYNODIC_PERIOD))
# the control: the intervals must SCATTER, or carrington_rotation_time is just n * P and
# the mean above is arithmetic rather than an ephemeris result.
spread = float(dif.max() - dif.min())
rec("carrington_interval_spread", "%.6f d (min %.6f, max %.6f) -- the mean is %.0fx "
    "tighter than the scatter" % (spread, dif.min(), dif.max(),
                                  spread / max(abs(dif.mean() - SYNODIC_PERIOD), 1e-300)))
if spread < 0.05:
    die("the intervals between Carrington rotations vary by only %.6f d, so the mean "
        "matching the synodic period says nothing an n * P formula would not" % spread)
t1 = sun.carrington_rotation_time(1)
off = float(t1.tt.jd - CR1_JD_TT)
rec("carrington_first", "%s (JD TT %.6f); Meeus gives %.4f +/- %.1f, off by %.6f d"
    % (t1.isot, t1.tt.jd, CR1_JD_TT, CR1_UNC, off))
if abs(off) > CR1_UNC:
    die("rotation 1 begins at JD TT %.6f, %.6f d from the cited %.4f +/- %.1f"
        % (t1.tt.jd, off, CR1_JD_TT, CR1_UNC))

# ==================================================== 7. a Map and its WCS, briefly
NPIX, SCALE = 64, 2.0
data = np.arange(NPIX * NPIX, dtype="float64").reshape(NPIX, NPIX)
ref = SkyCoord(0 * u.arcsec, 0 * u.arcsec,
               frame=frames.Helioprojective(obstime=T, observer="earth"))
m = sunpy.map.Map(data, sunpy.map.make_fitswcs_header(
    data, ref, scale=[SCALE, SCALE] * u.arcsec / u.pix))
rec("map", "%dx%d, scale %.1f arcsec/pix, dtype %s"
    % (m.data.shape[0], m.data.shape[1], m.scale[0].to_value(u.arcsec / u.pix), m.data.dtype))
if not np.array_equal(m.data, data):
    die("the Map changed the array it was handed")
worst_px = 0.0
for px, py in ((0.0, 0.0), (31.5, 31.5), (63.0, 63.0), (0.0, 63.0)):
    w = m.wcs.pixel_to_world(px, py)
    b = m.wcs.world_to_pixel(w)
    finite("WCS round-trip (%g,%g)" % (px, py), float(b[0]), float(b[1]))
    worst_px = max(worst_px, abs(float(b[0]) - px), abs(float(b[1]) - py))
rec("wcs_roundtrip", "worst %.3e pix over 4 corners/centre" % worst_px)
if worst_px > 1e-9:
    die("a WCS pixel -> world -> pixel round-trip lost %.3e pix" % worst_px)

# ====== 8. RECORDED, NOT ASSERTED: the constant table mixes sources, and one shows it
aas = sunconst.get("average angular size").to_value(u.arcsec)
from_iau = np.arcsin(R_IAU / AU_IAU) * ARC
from_old = np.arcsin(R_PRE2015 / AU_IAU) * ARC
rec("legacy_angular_size_note",
    "'average angular size' = %.2f arcsec [%s], but arcsin(radius/mean distance) from the "
    "same table is %.6f -- %.4f arcsec apart. arcsin(696000 km / au) = %.6f explains it to "
    "%.4f arcsec: a pre-2015 radius. The table cites five sources and is not internally "
    "consistent, so do not derive one constant from another."
    % (aas, sunconst.get("average angular size").reference, from_iau,
       aas - from_iau, from_old, abs(aas - from_old)))
if abs(aas - from_old) > abs(aas - from_iau):
    die("the legacy explanation no longer holds: 'average angular size' is now closer to "
        "the IAU 2015 radius than to the pre-2015 one, so the note above is stale")

# ============== and an honest non-result: sunpy's distance IS astropy's, not a check
a_dist = get_sun(T).distance.to_value(u.AU)
s_dist = d.to_value(u.AU)
rec("ephemeris_shared_note", "astropy get_sun %.12f AU vs sunpy earth_distance %.12f AU, "
    "rel %.3e -- the SAME ephemeris, so this is internal consistency and NOT an "
    "independent confirmation" % (a_dist, s_dist, abs(a_dist - s_dist) / a_dist))
if abs(a_dist - s_dist) / a_dist > 1e-12:
    die("sunpy and astropy disagree on the Sun's distance by %.3e; they share an ephemeris"
        % (abs(a_dist - s_dist) / a_dist))

# ------------------------------------------------------------------- determinism
if sun.angular_radius(T).to_value(u.arcsec) != sun.angular_radius(T).to_value(u.arcsec):
    die("two identical angular_radius calls disagreed")
rec("determinism", "repeat angular_radius calls are bit-identical")

with open("score.tsv", "w") as fh:
    fh.write("observable\tvalue\n")
    for k, v in ROWS:
        fh.write("%s\t%s\n" % (k, v))

print("SUNPY OK")
