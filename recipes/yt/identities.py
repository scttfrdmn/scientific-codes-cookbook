#!/usr/bin/env python3
"""yt on Graviton4: conservation identities for a volumetric analysis stack.

Every reference here is either a closed form or an independent numpy reduction of the
same array -- never a band on an observed value. The field is analytic, so nothing is
fetched and the answer is known before yt runs.

Three things worth knowing about the design:

 1. The mass identities are asserted EXACTLY (`==`), not within a tolerance. That is
    defensible because both sides are the same finite sum of the same doubles: yt's
    total_quantity over cell_mass and numpy's rho.sum()*dV. The probe measured
    0.000e+00 on both the uniform and non-uniform cubes before this was written. A
    tolerance would have hidden the fact that it is bit-identical.

 2. Conservation alone cannot catch a transposed axis, so there are two projection
    checks, not one. Summing surface density back to total mass is transpose-invariant
    (a total does not care about index order), so it is paired with an ELEMENTWISE
    comparison of yt's projected 2-D array against numpy's directional sum, on a box
    where every axis has a different cell count AND a different cell size (32x48x64 on
    1x2x3 cm). Axis handling is where yt bit the warpx recipe -- openPMD's axisLabels
    were z,x and a square grid hid the transpose -- so the orientation here was measured
    on the instance, not assumed.

 3. The sphere ladder is REPORTED, NOT ASSERTED as an order. Measured error ratios per
    doubling were 2.523, 7.414, 31.460 -- not a clean power of h. Asserting "O(h^p)"
    on that would be a band wearing a method test's clothes. What IS asserted is the
    finest rung against the analytic sphere volume, plus monotone improvement, both of
    which are reproducible because yt is deterministic here (checked).

Run with `python3 -u` so a killed run still has its output in command.log.
"""
import sys

import numpy as np
import yt

yt.funcs.mylog.setLevel(50)      # yt is chatty; its log is not the evidence

ROWS = []


def rec(k, v):
    print("%s\t%s" % (k, v))
    ROWS.append((k, v))


def die(msg):
    print("FAIL: %s" % msg)
    sys.exit(1)


def finite(name, *vals):
    """Every bound below is one-sided, and `nan > x` is False in Python -- so a NaN
    sails straight through `if rel > tol: die(...)`. Measured, not hypothetical: a
    swapped width tuple made yt's y-projection NaN-filled, and the conservation check
    passed on it. Nothing is compared before it is known to be finite."""
    for v in vals:
        if not np.all(np.isfinite(v)):
            die("%s is not finite (NaN or inf) -- a one-sided bound cannot catch this"
                % name)


rec("yt", getattr(yt, "__version__", "unknown"))
rec("numpy", np.__version__)
try:
    import unyt
    rec("unyt", getattr(unyt, "__version__", "unknown"))
except Exception as exc:                                   # a version print must never
    rec("unyt", "unavailable (%s)" % type(exc).__name__)    # be able to fail the run

RHO0 = 2.5          # g/cm^3
LBOX = 1.0          # cm


def grid(rho, bbox):
    """Load an in-memory array as a yt dataset (the stream frontend)."""
    return yt.load_uniform_grid(
        {"density": rho}, rho.shape,
        length_unit="cm", mass_unit="g", time_unit="s",
        bbox=np.asarray(bbox), nprocs=1)


def analytic(shape, extent):
    """A polynomial density, sampled at CELL CENTRES.

    Cell centres, not edges: yt's cell_mass is rho_cell * dV, so the numpy reference
    has to use the same sample points or the two sums are of different things.

    Polynomial rather than trigonometric on purpose. An earlier version used
    sin(2 pi x) cos(4 pi y), whose average over a full period in y is zero -- which made
    the x-marginal exactly flat, so a field that looked richly 3-D was degenerate along
    an axis. Staging now asserts every marginal varies.
    """
    axes = []
    for n, (lo, hi) in zip(shape, extent):
        d = (hi - lo) / n
        axes.append(lo + d * (0.5 + np.arange(n)))
    X, Y, Z = np.meshgrid(*axes, indexing="ij")
    Lx, Ly, Lz = [hi - lo for lo, hi in extent]
    return (1.0 + 0.7 * (X / Lx) + 0.5 * (Y / Ly) ** 2
            + 0.3 * (Z / Lz) ** 3 + 0.2 * (X / Lx) * (Y / Ly) * (Z / Lz))


def total_mass(ds):
    return float(ds.all_data().quantities.total_quantity(("gas", "mass")).to("g"))


# ------------------------------------------------ 1. uniform field vs a closed form
N = 64
cube = [[0.0, LBOX]] * 3
ds_u = grid(np.full((N, N, N), RHO0), cube)
m_u = total_mass(ds_u)
m_closed = RHO0 * LBOX ** 3
finite("uniform total mass", m_u)
rec("uniform_mass_yt", "%.17e" % m_u)
rec("uniform_mass_closed_form", "%.17e" % m_closed)
rec("uniform_mass_diff", "%.3e" % abs(m_u - m_closed))
if m_u != m_closed:
    die("uniform total mass is %.17e, closed form rho*L^3 is %.17e" % (m_u, m_closed))

# yt's own geometry must close too: the cell volumes tile the box exactly.
vol = float(ds_u.all_data().quantities.total_quantity(("index", "cell_volume")).to("cm**3"))
rec("cell_volume_sum", "%.17e" % vol)
if vol != LBOX ** 3:
    die("cell volumes sum to %.17e, box is %.17e" % (vol, LBOX ** 3))

# ------------------------------- 2. non-uniform field vs an independent numpy sum
rho_c = analytic((N, N, N), cube)
ds_c = grid(rho_c, cube)
m_c = total_mass(ds_c)
finite("non-uniform total mass", m_c)
dV = (LBOX / N) ** 3
m_np = float(rho_c.sum() * dV)
rec("cube_mass_yt", "%.17e" % m_c)
rec("cube_mass_numpy", "%.17e" % m_np)
rec("cube_mass_diff", "%.3e" % abs(m_c - m_np))
if m_c != m_np:
    die("non-uniform mass: yt %.17e vs numpy %.17e" % (m_c, m_np))

# --------------- 3. the anisotropic box: different cell COUNT and SIZE on every axis
# NOTE what this does and does not prove. It does NOT catch a transposed axis: the total
# mass is sum(rho)*dV and dV is a single scalar product, so permuting the array leaves it
# unchanged. Any conservation total is transpose-invariant. What it does catch is a wrong
# cell volume -- a shape/bbox pairing that mismatches -- which a cube hides because every
# axis is interchangeable there. The transpose is caught by the ELEMENTWISE check below.
ashape = (32, 48, 64)
aextent = [[0.0, 1.0], [0.0, 2.0], [0.0, 3.0]]
rho_a = analytic(ashape, aextent)
ds_a = grid(rho_a, aextent)
m_a = total_mass(ds_a)
finite("anisotropic total mass", m_a)
dVa = np.prod([(hi - lo) / n for n, (lo, hi) in zip(ashape, aextent)])
m_a_np = float(rho_a.sum() * dVa)
rec("aniso_shape", "x".join(str(s) for s in ashape))
rec("aniso_domain_cm", "x".join("%g" % (hi - lo) for lo, hi in aextent))
rec("aniso_mass_yt", "%.17e" % m_a)
rec("aniso_mass_numpy", "%.17e" % m_a_np)
rec("aniso_mass_diff", "%.3e" % abs(m_a - m_a_np))
if m_a != m_a_np:
    die("anisotropic mass: yt %.17e vs numpy %.17e -- an axis is being mixed up"
        % (m_a, m_a_np))

# ------------------- 4. projection conservation: surface density back to total mass
# proj with no weight integrates along the axis, giving g/cm^2. Summing that over the
# face area must return the mass -- a different code path (pixelization + FRB) to the
# same number. Done on all three axes of BOTH boxes.
#
# THE AXIS MAPPING IS READ FROM yt, NOT INFERRED. The two axes left after projecting are
# not in ascending order: yt's cartesian handler maps them cyclically, so projecting
# along y puts z horizontal and x VERTICAL. Assuming ascending order got the width tuple
# backwards for y alone, the buffer sampled outside the domain, and it came back
# NaN-filled -- which the one-sided bound below then passed. Hence ds.coordinates.
worst_proj = 0.0
for label, ds, extent, ref in (("cube", ds_c, cube, m_np), ("aniso", ds_a, aextent, m_a_np)):
    widths = [hi - lo for lo, hi in extent]
    centre = [(lo + hi) / 2 for lo, hi in extent]
    for axi, ax in enumerate("xyz"):
        ih = int(ds.coordinates.x_axis[axi])      # input axis shown horizontally
        iv = int(ds.coordinates.y_axis[axi])      # input axis shown vertically
        if label == "cube":
            rec("axis_map_%s" % ax, "horizontal=%d vertical=%d" % (ih, iv))
        proj = ds.proj(("gas", "density"), ax)
        res = (ds.domain_dimensions[ih], ds.domain_dimensions[iv])
        frb = proj.to_frb(((widths[ih], "cm"), (widths[iv], "cm")), res, center=centre)
        sigma = np.array(frb[("gas", "density")].to("g/cm**2"))
        finite("projection of the %s box along %s" % (label, ax), sigma)
        pix = (widths[ih] / res[0]) * (widths[iv] / res[1])
        m_proj = float(sigma.sum() * pix)
        finite("projected mass of the %s box along %s" % (label, ax), m_proj)
        rel = abs(m_proj - ref) / ref
        worst_proj = max(worst_proj, rel)
        rec("proj_%s_%s_mass" % (label, ax), "%.17e" % m_proj)
        rec("proj_%s_%s_rel" % (label, ax), "%.3e" % rel)
        # 1e-12 not 0: the FRB re-pixelizes, so the sum order differs from the cell sum.
        # The probe measured 0.000e+00 on the cube; the bound is there for the resample.
        if rel > 1e-12:
            die("projection along %s of the %s box gives mass %.17e, total is %.17e "
                "(rel %.3e)" % (ax, label, m_proj, ref, rel))
rec("projection_worst_rel", "%.3e" % worst_proj)

# -- 4b. ELEMENTWISE: the shape-sensitive half, which conservation cannot provide.
# A total is transpose-invariant, so section 4 would pass on a transposed image. This
# compares yt's 2-D buffer cell-by-cell against numpy's directional sum on the
# anisotropic box, where no two axes have the same length.
worst_elem = 0.0
awidths = [hi - lo for lo, hi in aextent]
acentre = [(lo + hi) / 2 for lo, hi in aextent]
ad_cells = [(hi - lo) / n for n, (lo, hi) in zip(ashape, aextent)]
for axi, ax in enumerate("xyz"):
    ih = int(ds_a.coordinates.x_axis[axi])
    iv = int(ds_a.coordinates.y_axis[axi])
    # numpy's sum leaves the two surviving axes in ascending order; the FRB is [iv, ih]
    sigma_np = rho_a.sum(axis=axi) * ad_cells[axi]
    want = sigma_np if iv < ih else sigma_np.T
    proj = ds_a.proj(("gas", "density"), ax)
    frb = proj.to_frb(((awidths[ih], "cm"), (awidths[iv], "cm")),
                      (ashape[ih], ashape[iv]), center=acentre)
    arr = np.array(frb[("gas", "density")].to("g/cm**2"))
    finite("elementwise projection along %s" % ax, arr)
    if arr.shape != want.shape:
        die("projection along %s has shape %s, numpy's directional sum is %s -- the "
            "axis mapping is wrong" % (ax, arr.shape, want.shape))
    scale = float(np.abs(want).max())
    rel = float(np.abs(arr - want).max() / scale)
    worst_elem = max(worst_elem, rel)
    rec("elementwise_%s_max_rel" % ax, "%.3e" % rel)
    if rel > 1e-12:
        die("projection along %s differs from numpy's directional sum by %.3e" % (ax, rel))
    # The control. A flip is shape-preserving, so it is the wrong answer this check must
    # still reject -- and it is only ~26% away, not orders of magnitude, so the bound has
    # to be tight. (A transpose cannot even be compared here: no two axes share a length,
    # so it fails on shape, which is categorical.)
    relf = float(np.abs(arr - np.flipud(want)).max() / scale)
    rec("control_flip_%s_max_rel" % ax, "%.3e" % relf)
    if relf < 1e-3:
        die("a vertically flipped projection along %s is only %.3e away, so the "
            "elementwise check cannot tell orientation" % (ax, relf))
rec("elementwise_worst_rel", "%.3e" % worst_elem)

# ---- the control: an agreement is worth nothing unless disagreement was possible.
# A WEIGHTED projection is an average, not an integral. If yt silently weighted, the
# check above would be comparing the wrong quantity -- so prove the two differ.
pw = ds_c.proj(("gas", "density"), "z", weight_field=("gas", "density"))
frbw = pw.to_frb((LBOX, "cm"), 64, center=[LBOX / 2] * 3)
qw = frbw[("gas", "density")]
rec("control_weighted_units", str(qw.units))
rec("control_weighted_sum", "%.6e" % float(np.array(qw).sum()))
if str(qw.units) == "g/cm**2":
    die("a weighted projection came back in g/cm**2 -- it is supposed to be an average "
        "(g/cm**3), so the unweighted check is not testing an integral")

# ------------------------------- 5. profile conservation: bins must hold all the mass
dens = np.array(ds_c.all_data()[("gas", "density")].to("g/cm**3"))
lo, hi = float(dens.min()), float(dens.max())
rec("density_range", "%.6e .. %.6e" % (lo, hi))
worst_prof = 0.0
for nb in (16, 64):
    prof = yt.create_profile(
        ds_c.all_data(), ("gas", "density"), ("gas", "mass"),
        weight_field=None, n_bins=nb,
        extrema={("gas", "density"): (lo * 0.999, hi * 1.001)},
        logs={("gas", "density"): False})
    s = float(np.array(prof[("gas", "mass")].to("g")).sum())
    finite("profile mass sum at %d bins" % nb, s)
    rel = abs(s - m_np) / m_np
    worst_prof = max(worst_prof, rel)
    rec("profile_%d_bins_mass" % nb, "%.17e" % s)
    rec("profile_%d_bins_rel" % nb, "%.3e" % rel)
    # Not exact: binning re-sums 262144 doubles in a different order. Measured 8.7e-15.
    if rel > 1e-12:
        die("profile with %d bins holds %.17e g, total is %.17e (rel %.3e) -- mass is "
            "falling outside the bins" % (nb, s, m_np, rel))
rec("profile_worst_rel", "%.3e" % worst_prof)

# ---------------- 6. the sphere selector: convergence REPORTED, order NOT asserted
# Measured ratios per doubling were 2.523, 7.414, 31.460 -- not a clean order, so no
# order is claimed. What is asserted: the finest rung hits the analytic volume, and
# every refinement improves. Both are reproducible because this is deterministic.
R = 0.3
V_sphere = 4.0 / 3.0 * np.pi * R ** 3
errs = []
for nn in (32, 64, 128, 256):
    d = grid(np.full((nn, nn, nn), RHO0), cube)
    sp = d.sphere([LBOX / 2] * 3, (R, "cm"))
    m = float(sp.quantities.total_quantity(("gas", "mass")).to("g"))
    exact = RHO0 * V_sphere
    finite("sphere mass at n=%d" % nn, m)
    err = abs(m - exact) / exact
    errs.append(err)
    rec("sphere_n%d_mass" % nn, "%.12e" % m)
    rec("sphere_n%d_rel_err" % nn, "%.4e" % err)
rec("sphere_exact_mass", "%.12e" % (RHO0 * V_sphere))
rec("sphere_err_ratios", ", ".join("%.3f" % (errs[i] / errs[i + 1])
                                   for i in range(len(errs) - 1)))
if errs[-1] > 1e-4:
    die("at 256^3 the sphere mass is still %.4e from (4/3)pi R^3 rho" % errs[-1])
for i in range(len(errs) - 1):
    if not errs[i + 1] < errs[i]:
        die("refining 2^%d -> 2^%d did not improve the sphere mass (%.4e -> %.4e)"
            % (i + 5, i + 6, errs[i], errs[i + 1]))
rec("sphere_order_asserted", "no -- ratios are not a clean power of h; see README")

# the sphere must also be a strict subset, or the selector is not selecting
d64 = grid(np.full((64, 64, 64), RHO0), cube)
frac = float(d64.sphere([LBOX / 2] * 3, (R, "cm")).quantities.total_quantity(
    ("gas", "mass")).to("g")) / total_mass(d64)
rec("sphere_mass_fraction", "%.6f" % frac)
rec("sphere_geometric_fraction", "%.6f" % (V_sphere / LBOX ** 3))
if not 0.0 < frac < 1.0:
    die("sphere holds %.6f of the box mass; a region selector must be a strict subset"
        % frac)
if abs(frac - V_sphere / LBOX ** 3) > 0.01:
    die("sphere mass fraction %.6f is not the geometric fraction %.6f"
        % (frac, V_sphere / LBOX ** 3))

# ------------------------------------------------------------------- 7. determinism
a = total_mass(grid(rho_c, cube))
b = total_mass(grid(rho_c, cube))
rec("determinism", "identical" if a == b else "DIFFERS")
if a != b:
    die("two identical loads gave %.17e and %.17e" % (a, b))

with open("score.tsv", "w") as fh:
    fh.write("observable\tvalue\n")
    for k, v in ROWS:
        fh.write("%s\t%s\n" % (k, v))

print("YT OK")
