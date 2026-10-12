#!/usr/bin/env python3
"""specutils on Graviton4: an analytic equivalent width and an exact flux-conservation law.

A Gaussian absorption line of depth d (in continuum units) and width sigma has

    EW = d * sigma * sqrt(2 pi)

exactly. That is an analytic reference, not a fit, so the spectrum is synthesised and
nothing is staged. The integral of the spectrum over any interval is likewise exact via
erf, which matters more than it sounds -- see below.

The second claim is a conservation law with a control built into the library:
FluxConservingResampler promises to preserve the integral, while
LinearInterpolatedResampler and SplineInterpolatedResampler do not. Same package,
different class, and only one making the claim.

TWO THINGS THAT HAD TO BE FIXED BEFORE THIS MEASURED ANYTHING:

 1. THE REFERENCE. Comparing a trapezoid over a SUB-RANGE of the fine grid against a
    trapezoid on the resampled grid differs by partial end intervals -- on [6420, 6680]
    that is 0.05, which read as a 1.9e-04 "conservation error" that did not exist. The
    analytic erf integral is exact for any interval and is used throughout. The
    sanity check is that it agrees with the fine-grid trapezoid over the FULL range,
    where there are no partial intervals.
 2. THE GRID. At 0.075 A spacing a sigma = 3 A line is oversampled 40x, so plain
    interpolation is already near-exact and all three resamplers conserve equally. The
    control only bites when the target grid is COARSE relative to the line, so the sweep
    runs to 10 A. Reporting "FluxConserving conserves flux" from the fine grid alone
    would have been a true statement backed by no evidence.

Run with `python3 -u` so a killed run still has its output in command.log.
"""
import sys

import numpy as np
from scipy.special import erf

ROWS = []


def rec(k, v):
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


import astropy.units as u
import specutils
# specutils 2.x: Spectrum1D is deprecated in favour of Spectrum and warns it may be
# removed. Use the current name; fall back only if this build predates the rename.
try:
    from specutils import Spectrum, SpectralRegion
    SPEC_CLASS = "Spectrum"
except ImportError:
    from specutils import Spectrum1D as Spectrum, SpectralRegion
    SPEC_CLASS = "Spectrum1D (deprecated)"
from specutils.analysis import equivalent_width, centroid
from specutils.manipulation import (FluxConservingResampler,
                                    LinearInterpolatedResampler,
                                    SplineInterpolatedResampler)

ver("specutils", lambda: specutils.__version__)
ver("astropy", lambda: __import__("astropy").__version__)
ver("numpy", lambda: np.__version__)
ver("scipy", lambda: __import__("scipy").__version__)
rec("spectrum_class", SPEC_CLASS)

LAM0, SIGMA, DEPTH = 6562.8, 3.0, 0.4          # H-alpha, Angstrom, fraction of continuum
LAM_LO, LAM_HI, NFINE = 6400.0, 6700.0, 4001
A, B = 6420.0, 6680.0                          # the resampling target range
EW_EXACT = DEPTH * SIGMA * np.sqrt(2.0 * np.pi)

lam = np.linspace(LAM_LO, LAM_HI, NFINE)
prof = DEPTH * np.exp(-0.5 * ((lam - LAM0) / SIGMA) ** 2)
flux = 1.0 - prof
spec = Spectrum(spectral_axis=lam * u.AA, flux=flux * u.Jy)

rec("line", "Gaussian absorption at %.1f A, sigma %.1f A, depth %.2f" % (LAM0, SIGMA, DEPTH))
rec("fine_grid", "%d points, %.4f A spacing (%.1fx oversampled vs sigma)"
    % (NFINE, lam[1] - lam[0], SIGMA / (lam[1] - lam[0])))
rec("EW_analytic", "%.12f" % EW_EXACT)


def analytic_integral(a, b):
    """Exact integral of [1 - d exp(-(x-l0)^2 / 2 s^2)] from a to b, via erf."""
    g = DEPTH * SIGMA * np.sqrt(np.pi / 2.0) * (
        erf((b - LAM0) / (SIGMA * np.sqrt(2.0)))
        - erf((a - LAM0) / (SIGMA * np.sqrt(2.0))))
    return (b - a) - g


# ---------- 0. the reference checks itself: over the FULL range a trapezoid has no
# partial end intervals, so the two must agree. This is what caught the bad sub-range
# reference that produced a phantom 1.9e-04 error.
full_trap = float(np.trapezoid(flux, lam))
full_exact = analytic_integral(LAM_LO, LAM_HI)
rec("integral_full_trapezoid", "%.12f" % full_trap)
rec("integral_full_analytic", "%.12f" % full_exact)
d_ref = abs(full_trap - full_exact) / abs(full_exact)
rec("reference_selfcheck", "%.3e" % d_ref)
if d_ref > 1e-12:
    die("the erf reference and a fine-grid trapezoid disagree by %.3e over the full "
        "range, where they cannot differ by end effects -- the reference is wrong" % d_ref)
# And the sub-range difference, recorded so the trap is visible rather than lurking.
sub_trap = float(np.trapezoid(flux[(lam >= A) & (lam <= B)], lam[(lam >= A) & (lam <= B)]))
rec("subrange_trapezoid_note", "%.12f vs analytic %.12f (differs by %.3f: partial end "
    "intervals, NOT a conservation error)"
    % (sub_trap, analytic_integral(A, B), abs(sub_trap - analytic_integral(A, B))))

# --------------------------------- 1. the equivalent width against the closed form
reg = SpectralRegion((LAM0 - 10 * SIGMA) * u.AA, (LAM0 + 10 * SIGMA) * u.AA)
ew = abs(float(getattr(equivalent_width(spec, continuum=1.0 * u.Jy, regions=reg),
                       "value", 0.0)))
rec("EW_specutils", "%.12f" % ew)
d_ew = abs(ew - EW_EXACT) / EW_EXACT
rec("EW_rel_vs_analytic", "%.3e" % d_ew)
if not np.isfinite(ew):
    die("equivalent_width returned a non-finite value")
if d_ew > 1e-12:
    die("the equivalent width is %.3e from d*sigma*sqrt(2pi); the grid oversamples the "
        "line 40x so discretisation cannot explain that" % d_ew)

# ----------------------------- 2. the centroid of a symmetric line is its centre
emis = Spectrum(spectral_axis=lam * u.AA, flux=prof * u.Jy)
cen = float(getattr(centroid(emis, regions=reg), "value", float("nan")))
rec("centroid", "%.9f" % cen)
rec("centroid_offset", "%.3e" % abs(cen - LAM0))
if not np.isfinite(cen):
    die("centroid returned a non-finite value")
if abs(cen - LAM0) > 1e-9:
    die("the centroid of a symmetric line is %.9f, not %.1f" % (cen, LAM0))

# ------- 3. flux conservation, swept over grid coarseness so the control can bite
RESAMPLERS = [("FluxConserving", FluxConservingResampler),
              ("LinearInterp", LinearInterpolatedResampler),
              ("SplineInterp", SplineInterpolatedResampler)]
REF = analytic_integral(A, B)
rec("target_range", "[%.0f, %.0f], analytic integral %.12f" % (A, B, REF))

errors = {name: {} for name, _ in RESAMPLERS}
for npts in (1301, 131, 53, 27):
    new = np.linspace(A, B, npts)
    dl = new[1] - new[0]
    for name, R in RESAMPLERS:
        out = R()(spec, new * u.AA)
        f2 = np.asarray(out.flux.value, dtype="float64")
        good = np.isfinite(f2)
        if good.sum() != len(f2):
            die("%s produced %d non-finite samples at %.3f A spacing"
                % (name, len(f2) - good.sum(), dl))
        i2 = float(np.trapezoid(f2[good], new[good]))
        e = abs(i2 - REF) / abs(REF)
        errors[name][dl] = e
        rec("integral_%s_%.3fA" % (name, dl), "%.12f  rel %.3e" % (i2, e))

# The claim: the conserving resampler is EXACT at every spacing.
for dl, e in sorted(errors["FluxConserving"].items()):
    if e != 0.0:
        die("FluxConservingResampler left the integral %.3e off at %.3f A spacing; it "
            "promises conservation and the reference is analytic" % (e, dl))
rec("conservation", "FluxConservingResampler is exact (0.000e+00) at every spacing")

# THE CONTROL, and it must be GRADED: an interpolator's error has to grow as the grid
# coarsens. If it stayed at zero, the conservation result above would prove nothing,
# because every resampler would conserve.
lin = sorted(errors["LinearInterp"].items())
rec("control_linear_errors", ", ".join("%.3fA:%.3e" % (d, e) for d, e in lin))
if all(e == 0.0 for _, e in lin):
    die("LinearInterpolatedResampler conserved the integral exactly at every spacing, so "
        "the conservation claim above does not distinguish the resamplers")
coarse = [e for d, e in lin if d > 1.0]
if not coarse or max(coarse) < 1e-6:
    die("the interpolator's worst error is %.3e; too small to show that conservation is "
        "a real property rather than an artifact of a fine grid"
        % (max(coarse) if coarse else 0.0))
if not all(lin[i][1] <= lin[i + 1][1] for i in range(len(lin) - 1)):
    die("the interpolator's error is not monotone in grid spacing (%s), so the control is "
        "not graded and the comparison is harder to trust"
        % ["%.3e" % e for _, e in lin])
rec("control_graded", "LinearInterp error grows %.1e -> %.1e as spacing goes %.1fA -> %.1fA"
    % (lin[0][1], lin[-1][1], lin[0][0], lin[-1][0]))

# ------------- 4. and the physics: does the EW survive a coarse resampling?
rec("EW_after_resampling", "analytic %.12f" % EW_EXACT)
ew_err = {}
for npts, label in ((1301, "0.2A"), (53, "5A"), (27, "10A")):
    new = np.linspace(A, B, npts)
    for name, R in (("FluxConserving", FluxConservingResampler),
                    ("LinearInterp", LinearInterpolatedResampler)):
        out = R()(spec, new * u.AA)
        v = abs(float(getattr(equivalent_width(out, continuum=1.0 * u.Jy, regions=reg),
                              "value", float("nan"))))
        if not np.isfinite(v):
            die("the equivalent width after %s resampling to %s is not finite"
                % (name, label))
        e = abs(v - EW_EXACT) / EW_EXACT
        ew_err[(name, label)] = e
        rec("EW_%s_%s" % (name, label), "%.9f  rel %.3e" % (v, e))

for label in ("5A", "10A"):
    if ew_err[("FluxConserving", label)] > 1e-12:
        die("FluxConserving resampling to %s moved the equivalent width by %.3e; the "
            "line's integrated depth is what it conserves" % (label, ew_err[("FluxConserving", label)]))
if ew_err[("LinearInterp", "10A")] < 1e-3:
    die("linear interpolation to 10 A lost only %.3e of the equivalent width, so the "
        "conserving result is not a meaningful contrast"
        % ew_err[("LinearInterp", "10A")])
rec("physics_control", "at 10 A sampling of a %.0f A line: FluxConserving %.1e, "
    "LinearInterp %.3f of the EW lost" % (SIGMA, ew_err[("FluxConserving", "10A")],
                                          ew_err[("LinearInterp", "10A")]))

# ------------------------------------------------------------------- 5. determinism
a1 = abs(float(getattr(equivalent_width(spec, continuum=1.0 * u.Jy, regions=reg), "value", 0)))
a2 = abs(float(getattr(equivalent_width(spec, continuum=1.0 * u.Jy, regions=reg), "value", 0)))
rec("determinism", "identical" if a1 == a2 else "DIFFERS")
if a1 != a2:
    die("two identical equivalent-width calls gave %.17g and %.17g" % (a1, a2))

with open("score.tsv", "w") as fh:
    fh.write("observable\tvalue\n")
    for k, v in ROWS:
        fh.write("%s\t%s\n" % (k, v))

print("SPECUTILS OK")
