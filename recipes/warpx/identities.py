#!/usr/bin/env python3
"""WarpX on Graviton: upstream's Langmuir deck, upstream's closed form, upstream's tolerance.

The package ships WarpX's own `Examples/Tests/langmuir` decks, and upstream's own analysis script
carries the analytic solution for them:

    E_x = eps * (m_e c^2 k_x / q_e) * sin(k_x x) cos(k_z z) * sin(w_p t)
    w_p = sqrt(n e^2 / (m_e eps_0))        n = 4e24 (2e24 electrons + 2e24 positrons)

So this is not a band on an observed value: the whole 2D field is compared against a closed form,
at upstream's own committed tolerance of 0.0503, with w_p derived from the deck's own `n0` rather
than typed in here.

aarchsci's D3 suite already asserts the charge-conservation identity, the closed-form frequency
and the yt/openPMD equality for this env. This recipe is not a restatement: it adds a REAL
workload with a thread sweep and a cost figure, and it adds the deposition-scheme discrimination
below. The overlapping checks are kept because they are free and they are the strongest available.

TWO THINGS READ OUT OF UPSTREAM'S HARNESS RATHER THAN GUESSED:

 1. Upstream checks charge conservation ONLY for esirkepov / vay / current_correction deposition
    -- NOT for `direct`, which is what the shipped 2D test variant uses. So this runs esirkepov
    and asserts their 1e-11, and REPORTS `direct` separately. Asserting the identity on `direct`
    would be asserting something upstream's own code declines to.
 2. The analytic field is sampled at CELL CENTRES (`xmin + dx*(0.5 + arange(N))`), which is how
    their get_contribution() does it. Sampling at edges would introduce a half-cell phase error
    that looks like a physics discrepancy.

Run with `python3 -u`.
"""
import os
import re
import subprocess
import sys
import time

import numpy as np
from scipy.constants import c, e, epsilon_0, m_e

out = {}


def rec(k, v):
    out[k] = v
    print("%s\t%s" % (k, v))


_once = set()


def rec_once(k, v):
    if k not in _once:
        _once.add(k)
        rec(k, v)


def dump():
    with open("/tmp/score.tsv", "w") as fh:
        fh.write("observable\tvalue\n")
        for k, v in out.items():
            fh.write("%s\t%s\n" % (k, v))


def die(msg):
    dump()
    sys.exit("FAIL: %s" % msg)


# The env prefix, not a hardcoded /opt/conda: the x86 twin installs to /var/tmp/env.
EXAMPLES = os.path.join(sys.prefix, "etc/conda/test-files/warpx/1/Examples")
BASE = os.path.join(EXAMPLES, "Tests/langmuir/inputs_base_2d")
WARPX = "warpx.2d"
UPSTREAM_FIELD_TOL = 0.0503      # analysis_2d.py, particle_shape != 4
UPSTREAM_CHARGE_TOL = 1e-11      # analysis_utils.py, esirkepov without psatd/RZ

import openpmd_api
import yt

yt.funcs.mylog.setLevel(50)
import shutil
rec("warpx_binary", shutil.which(WARPX) or "NOT ON PATH")
if not shutil.which(WARPX):
    die("%s is not on PATH" % WARPX)
ver = subprocess.run([WARPX, "--version"], capture_output=True, text=True)
rec("warpx_version_line", (ver.stdout + ver.stderr).strip().splitlines()[0][:90]
    if (ver.stdout + ver.stderr).strip() else "no --version output")
rec("openpmd_api", openpmd_api.__version__)
rec("yt", yt.__version__)
rec("numpy", np.__version__)

# ---- 1. the deck must be upstream's, unmodified ------------------------------------------
if not os.path.exists(BASE):
    die("upstream's deck is not in the package at %s" % BASE)
deck = open(BASE).read()
rec("deck_path", BASE)
rec("deck_bytes", len(deck))


def deck_val(key):
    m = re.search(r"^\s*%s\s*=\s*(.+?)\s*(?:#.*)?$" % re.escape(key), deck, re.M)
    return m.group(1).strip() if m else None


expect = {"max_step": "80", "amr.n_cell": "128  128", "geometry.dims": "2",
          "warpx.cfl": "1.0", "my_constants.epsilon": "0.01",
          "my_constants.n0": "2.e24", "algo.particle_shape": "1"}
for k, want in expect.items():
    got = deck_val(k)
    if got is None or " ".join(got.split()) != " ".join(want.split()):
        die("upstream's deck has %s = %r, this recipe was written against %r" % (k, got, want))
rec("deck_parameters_verified", "%d of %d match" % (len(expect), len(expect)))
if "periodic periodic" not in deck:
    die("the deck is no longer periodic; the closed form assumes it is")
rec("identity_upstream_deck",
    "running WarpX's own langmuir deck unmodified in its physics: 128x128, 80 steps, "
    "n0=2e24 per species, cfl=1.0, periodic")

# ---- the closed form, from the deck's own constants --------------------------------------
n0 = float(deck_val("my_constants.n0").replace("e24", "e24"))
eps_amp = float(deck_val("my_constants.epsilon"))
N = int(deck_val("amr.n_cell").split()[0])
xmin, xmax = -20e-6, 20e-6
n_total = 2.0 * n0                      # electrons + positrons, as the deck's comment states
wp = np.sqrt(n_total * e ** 2 / (m_e * epsilon_0))
kx = kz = 2.0 * np.pi * 2.0 / (xmax - xmin)
rec("n_total_from_deck", "%.3e" % n_total)
rec("plasma_frequency_rad_s", "%.6e" % wp)
rec("wavenumber_1_per_m", "%.6e" % kx)


def theoretical(field, t, n):
    du = (xmax - xmin) / n
    u = xmin + du * (0.5 + np.arange(n))        # CELL CENTRES, as upstream samples them
    amp = eps_amp * (m_e * c ** 2 * (kx if field == "Ex" else kz)) / e * np.sin(wp * t)
    xs = np.sin(kx * u) if field == "Ex" else np.cos(kx * u)
    zs = np.cos(kz * u) if field == "Ex" else np.sin(kz * u)
    return amp * xs[:, np.newaxis] * zs[np.newaxis, :]


def run(tag, n_cell, deposition, threads, steps=80):
    """Run upstream's deck with only diagnostics and the swept knobs appended."""
    d = "/tmp/%s" % tag
    os.makedirs(d, exist_ok=True)
    # Everything appended here is DIAGNOSTICS or a swept knob -- never physics. rho and divE are
    # needed for the charge-conservation identity; the openPMD diag is the second writer.
    extra = [
        "FILE = %s" % BASE,
        "algo.current_deposition = %s" % deposition,
        "amr.n_cell = %d %d" % (n_cell, n_cell),
        "max_step = %d" % steps,
        "diag1.fields_to_plot = Ex Ey Ez rho divE",
        "diag1.intervals = %d" % steps,
        "diagnostics.diags_names = diag1 diag2",
        "diag2.diag_type = Full",
        "diag2.format = openpmd",
        "diag2.openpmd_backend = h5",
        "diag2.intervals = %d" % steps,
        "diag2.fields_to_plot = Ex Ez",
    ]
    with open(os.path.join(d, "inputs"), "w") as fh:
        fh.write("\n".join(extra) + "\n")
    env = dict(os.environ, OMP_NUM_THREADS=str(threads))
    t0 = time.time()
    p = subprocess.run([WARPX, "inputs"], cwd=d, env=env, capture_output=True, text=True)
    wall = time.time() - t0
    log = p.stdout + p.stderr
    open(os.path.join(d, "warpx.log"), "w").write(log)
    if p.returncode != 0:
        tail = "\n".join(log.strip().splitlines()[-12:])
        die("%s: warpx exited %d\n%s" % (tag, p.returncode, tail))
    m = re.search(r"OMP threads?\s*:?\s*(\d+)", log) or re.search(r"(\d+)\s+OMP thread", log)
    return d, wall, (int(m.group(1)) if m else None), log


def find_plotfiles(d):
    """Return the MESH plotfiles under d, latest step last.

    An AMReX plotfile is a directory holding a Header and Level_0/. Two traps, both hit:
      * WarpX writes them under diags/, not the run directory
      * each species gets its OWN Header + Level_0 inside the plotfile
        (diags/diag1000080/electrons, .../positrons), so a naive walk returns particle
        containers too -- and yt fails on one with
        "invalid literal for int(): 'momentum_x'" while parsing it as a mesh.
    So nested matches are dropped, and the step number is sorted numerically rather than
    lexically.
    """
    found = []
    for root, dirs, files in os.walk(d):
        if "Header" in files and any(x.startswith("Level_") for x in dirs):
            found.append(root)
    top = [f for f in found if os.path.dirname(f) not in found]

    def step(path):
        m = re.search(r"(\d+)$", os.path.basename(path))
        return int(m.group(1)) if m else -1

    return sorted(top, key=step)


def field_error(d, n_cell):
    plt = find_plotfiles(d)
    if not plt:
        tree = []
        for root, dirs, files in os.walk(d):
            tree.append("%s: %s" % (os.path.relpath(root, d), sorted(dirs + files)[:6]))
        die("no AMReX plotfile under %s\n  %s" % (d, "\n  ".join(tree[:10])))
    ds = yt.load(plt[-1])
    # yt's AMReX frontend registers raw fields as ("boxlib", name) and exposes ("mesh", name)
    # only as an ALIAS -- so field_list has no "mesh" entries even for a valid mesh plotfile.
    # A first version of this guard filtered on ftype == "mesh" and rejected a good file.
    # Upstream uses both forms (("mesh", "Ex") in analysis_2d.py, ("boxlib", "rho") in
    # analysis_utils.py), which is the tell. So match on the NAME, across field types.
    have = {f for (_ftype, f) in ds.field_list}
    rec_once("plotfile_field_list", " ".join(sorted(have)[:12]))
    if "Ex" not in have:
        die("%s has no Ex field -- fields present: %s. Either a particle container was picked "
            "up or fields_to_plot did not take." % (plt[-1], sorted(have)[:8]))
    t0 = float(ds.current_time.to_value())
    data = ds.covering_grid(level=0, left_edge=ds.domain_left_edge,
                            dims=ds.domain_dimensions)
    worst = 0.0
    for f in ("Ex", "Ez"):
        sim = data[("mesh", f)].to_ndarray()[:, :, 0]
        th = theoretical(f, t0, n_cell)
        worst = max(worst, float(np.abs(sim - th).max() / np.abs(th).max()))
    return worst, t0, data


# ---- 2. the field against the closed form, at upstream's tolerance -----------------------
NT = int(os.environ.get("SWEEP_MAX_THREADS", "4"))
d, wall, omp, log = run("base", N, "esirkepov", NT)
rec("output_layout", " ".join(sorted(os.path.relpath(p, d) for p in find_plotfiles(d))) or "none")
err, t0, data = field_error(d, N)
rec("base_wall_s", "%.2f" % wall)
rec("base_omp_threads_reported", omp)
rec("diagnostic_time_s", "%.6e" % t0)
rec("plasma_periods_elapsed", "%.4f" % (t0 * wp / (2 * np.pi)))
rec("field_max_relative_error", "%.4e" % err)
rec("upstream_field_tolerance", UPSTREAM_FIELD_TOL)
if err >= UPSTREAM_FIELD_TOL:
    die("field error %.4e exceeds upstream's own tolerance %.4f" % (err, UPSTREAM_FIELD_TOL))
rec("identity_closed_form_field",
    "the whole Ex/Ez field matches eps*(m_e c^2 k/q_e)*sin(kx)cos(kz)*sin(wp t) to %.4e, "
    "inside upstream's committed %.4f" % (err, UPSTREAM_FIELD_TOL))

# ---- 3. charge conservation, for the scheme upstream says to check it on -----------------
rho = data[("boxlib", "rho")].to_ndarray()
divE = data[("boxlib", "divE")].to_ndarray()
cc = float(np.amax(np.abs(divE - rho / epsilon_0)) / np.amax(np.abs(rho / epsilon_0)))
rec("charge_conservation_esirkepov", "%.3e" % cc)
rec("upstream_charge_tolerance", UPSTREAM_CHARGE_TOL)
if cc >= UPSTREAM_CHARGE_TOL:
    die("eps0*div(E) != rho to %.3e, over upstream's %.1e" % (cc, UPSTREAM_CHARGE_TOL))
rec("identity_charge_conservation",
    "eps0*div(E) == rho to %.3e with esirkepov deposition, inside upstream's %.1e"
    % (cc, UPSTREAM_CHARGE_TOL))

# ---- 4. the cross-reader equality: two writers, two readers -----------------------------
h5 = []
for root, _, files in os.walk(d):
    h5 += [os.path.join(root, f) for f in files if f.endswith((".h5", ".bp"))]
if not h5:
    die("no openPMD output in %s" % d)
series = openpmd_api.Series(sorted(h5)[-1], openpmd_api.Access.read_only)
it = series.iterations[max(series.iterations)]
# The two readers must be brought to the same convention BEFORE comparing, and the convention
# is read from openPMD's own metadata rather than guessed by trying permutations:
#   * axisLabels gives the array's axis order, which a square grid would otherwise hide --
#     a first version compared a transposed array and got 5.733e+08 against a ~1.6e9 field
#   * unitSI converts stored values to SI
# Comparing without resolving these is a method difference, not a disagreement between readers.
worst_rd = 0.0
mesh = it.meshes["E"]
labels = [str(a) for a in mesh.axis_labels]
rec_once("openpmd_axis_labels", ",".join(labels))
rec_once("openpmd_grid_spacing", " ".join("%.6e" % v for v in mesh.grid_spacing))
for f in ("Ex", "Ez"):
    comp = mesh[f[-1]]
    unit = float(comp.unit_SI)
    rec_once("openpmd_unit_SI_%s" % f, "%.6e" % unit)
    arr = comp.load_chunk()
    series.flush()
    o = np.squeeze(np.asarray(arr)) * unit
    y = data[("mesh", f)].to_ndarray()[:, :, 0]
    # yt's covering_grid is indexed (x, z) for a 2D WarpX plotfile. openPMD's axisLabels say
    # what order IT used; reorder to (x, z) accordingly instead of assuming.
    if labels and labels[0] != "x":
        o = o.T
    rec_once("openpmd_shape_%s" % f, "%s vs yt %s" % (o.shape, y.shape))
    if o.shape != y.shape:
        die("openPMD %s is %s after reordering, yt gives %s" % (f, o.shape, y.shape))
    d_now = float(np.abs(o - y).max())
    # One honest diagnostic: what IGNORING axisLabels would have given. Computed from the same
    # flushed array (o.T undoes the reorder), so it is a real number rather than a re-read of an
    # unflushed buffer. This is the figure that shows a square grid hides the axis swap.
    rec_once("openpmd_%s_without_reorder" % f, "%.3e" % float(np.abs(o.T - y).max()))
    worst_rd = max(worst_rd, d_now)
del series
rec("yt_vs_openpmd_max_abs_diff", "%.3e" % worst_rd)
if worst_rd != 0.0:
    die("yt and openpmd-api differ by %.3e on the same step" % worst_rd)
rec("identity_cross_reader",
    "the same step written by two unrelated writers and read by two unrelated readers is "
    "byte-identical (0.0); independently found by aarchsci's D3 suite")

# ---- 5. the deposition discrimination: `direct` does NOT conserve charge -----------------
# Upstream's own harness declines to check charge conservation for `direct`. Measuring what it
# actually gives is what shows the esirkepov result above is a property of the scheme and not
# something any deposition would satisfy.
dd, _, _, _ = run("direct", N, "direct", NT)
_, _, ddata = field_error(dd, N)
rho_d = ddata[("boxlib", "rho")].to_ndarray()
divE_d = ddata[("boxlib", "divE")].to_ndarray()
cc_d = float(np.amax(np.abs(divE_d - rho_d / epsilon_0)) / np.amax(np.abs(rho_d / epsilon_0)))
rec("charge_conservation_direct", "%.3e" % cc_d)
rec("direct_vs_esirkepov_ratio", "%.3e" % (cc_d / cc if cc > 0 else float("inf")))
if cc_d <= UPSTREAM_CHARGE_TOL:
    die("`direct` also conserves charge to %.3e -- the discrimination is gone, so the "
        "esirkepov assertion is not testing the scheme" % cc_d)
rec("identity_deposition_discriminates",
    "`direct` deposition gives %.3e, %.0fx worse than esirkepov -- which is why upstream's "
    "harness only checks the identity on charge-conserving schemes"
    % (cc_d, cc_d / cc if cc > 0 else 0))

# ---- 6. the thread sweep: same answer, and a cost figure -------------------------------
# One box, threads varied -- resizing the instance per rung would measure scaling plus whatever
# else changed. The invariance is asserted first: a timing table is meaningless if the answer
# moves with the thread count.
HOURLY = float(os.environ.get("INSTANCE_HOURLY_USD", "0"))
rows = []
for nt in [int(v) for v in os.environ.get("SWEEP", "1,2,4").split(",")]:
    tag = "omp%d" % nt
    dd2, w2, omp2, _ = run(tag, N, "esirkepov", nt)
    e2, _, _ = field_error(dd2, N)
    rows.append((nt, w2, omp2, e2))
    rec("sweep_t%d" % nt, "wall %.2fs  omp_reported %s  field_err %.4e" % (w2, omp2, e2))
for nt, w2, omp2, e2 in rows:
    if omp2 is not None and omp2 != nt:
        die("asked for %d OpenMP threads, WarpX reported %s" % (nt, omp2))
rec("identity_openmp_honoured",
    "WarpX reported the thread count it was given at every rung (%s) -- nompi is not serial"
    % ",".join(str(r[2]) for r in rows))
spread = max(r[3] for r in rows) - min(r[3] for r in rows)
rec("field_error_spread_across_threads", "%.3e" % spread)
if spread > 1e-6:
    die("the field error moves by %.3e across thread counts -- the answer is not "
        "thread-invariant, so the timings below are not comparable" % spread)
rec("identity_thread_invariance",
    "the field error is identical to %.1e across %d thread counts, which is what licenses "
    "comparing their wall times" % (spread, len(rows)))
base_w = rows[0][1]
tbl = []
for nt, w2, _, _ in rows:
    sp = base_w / w2
    cost = HOURLY * w2 / 3600.0 if HOURLY else float("nan")
    tbl.append("t=%d %.2fs %.2fx eff=%.0f%% $%.5f" % (nt, w2, sp, 100 * sp / nt, cost))
rec("sweep_table", " | ".join(tbl))

dump()
print("WARPX OK")
