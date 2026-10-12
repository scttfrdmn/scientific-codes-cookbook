#!/usr/bin/env python3
"""laspy on Graviton4: a second LAZ reader against PDAL's numbers, and the format's own law.

Two references, neither of them a band:

 1. PDAL ALREADY DECODED THIS FILE. recipes/pointcloud asserts, for these exact staged
    bytes, a point count of 10,653,336, the X/Y/Z bounds, and a mean Z of 434.1025 over
    all 10.6M decoded points. laspy and PDAL share no code, so agreement is about the
    decoding rather than about either tool's internal consistency. The mean is the
    load-bearing one: a header can be parsed correctly while the points are decoded
    wrongly, which is the distinction recipes/pointcloud draws when it pairs metadata
    with a statistic over the decoded points.

 2. THE LAS SPEC IS AN EXACT LAW. Coordinates are stored as int32 with a per-axis scale
    and offset, so x == X_raw * scale + offset holds EXACTLY -- not to a tolerance. That
    gives a control with a predictable magnitude: coordinates written ON that grid must
    round-trip bit-exactly, and coordinates written OFF it must move by at most scale/2.
    An implementation that violated either would be breaking the format, not merely
    being imprecise.

PDAL's published mean is quoted to four decimals, so agreement is asserted at THAT
precision (5e-05, half the last published digit) rather than tighter. Asserting more
precision than the reference carries would be claiming agreement that was never stated.

Run with `python3 -u` so a killed run still has its output in command.log.
"""
import hashlib
import os
import sys

import numpy as np

ROWS = []


def rec(k, v):
    """Always str() the value. `"%s" % obj` unpacks a tuple-like object as format
    arguments, which is how las.header.version -- a Version(major, minor) -- raised
    TypeError and killed an earlier run of this very script. Callers pass values, not
    pre-formatted strings, wherever the type is not obviously scalar."""
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


import laspy

ver("laspy", lambda: laspy.__version__)
ver("numpy", lambda: np.__version__)
# laspy needs a LAZ backend; lazrs ships here as `lazrs-python`, laszip does not.
backends = []
for mod in ("lazrs", "laszip"):
    try:
        __import__(mod)
        backends.append(mod)
    except Exception:
        pass
rec("laz_backends", ",".join(backends) if backends else "NONE")
if not backends:
    die("no LAZ backend (lazrs or laszip) is importable, so laspy cannot read the "
        "compressed cloud this recipe cross-checks")

LAZ = "autzen.laz"
PINNED_SHA = "944b947501156e45df1b3b9d25bc1dc04ff5ef377e7e169576ba59231c2896ba"

# PDAL's published values for these exact bytes (recipes/pointcloud).
PDAL_COUNT = 10653336
PDAL_BOUNDS = {"x": (635577.79, 639003.73),
               "y": (848882.15, 853537.66),
               "z": (406.14, 615.26)}
PDAL_MEAN_Z = 434.1025
PDAL_MEAN_Z_PRECISION = 5e-5          # half the last digit PDAL published

# ------------------------------------------------ 0. the bytes must be the pinned ones
if not os.path.exists(LAZ):
    die("%s was not staged" % LAZ)
h = hashlib.sha256()
with open(LAZ, "rb") as fh:
    for chunk in iter(lambda: fh.read(1 << 20), b""):
        h.update(chunk)
got = h.hexdigest()
rec("laz_bytes", "%d" % os.path.getsize(LAZ))
rec("laz_sha256", got)
if got != PINNED_SHA:
    die("the staged cloud hashes %s, pinned %s -- PDAL's published numbers describe the "
        "pinned bytes, so a different file makes the cross-check meaningless"
        % (got, PINNED_SHA))

# ------------------------------------------------------------------ 1. read it
las = laspy.read(LAZ)
rec("point_format", las.header.point_format.id)
rec("las_version", las.header.version)
rec("scales", ",".join("%g" % s for s in las.header.scales))
rec("offsets", ",".join("%.2f" % o for o in las.header.offsets))

x = np.asarray(las.x, dtype="float64")
y = np.asarray(las.y, dtype="float64")
z = np.asarray(las.z, dtype="float64")
for nm, arr in (("x", x), ("y", y), ("z", z)):
    if not np.all(np.isfinite(arr)):
        die("%s contains a non-finite coordinate" % nm)

# ------------------------- 2. the header must agree with the array it describes
rec("header_point_count", "%d" % las.header.point_count)
rec("decoded_points", "%d" % len(z))
if las.header.point_count != len(z):
    die("the header claims %d points and the array holds %d" % (las.header.point_count, len(z)))
for i, (nm, arr) in enumerate((("x", x), ("y", y), ("z", z))):
    hmin, hmax = float(las.header.mins[i]), float(las.header.maxs[i])
    if hmin != float(arr.min()) or hmax != float(arr.max()):
        die("the header's %s extent is [%.6f, %.6f] but the data spans [%.6f, %.6f]"
            % (nm, hmin, hmax, arr.min(), arr.max()))
rec("header_matches_data", "count and all three extents agree exactly")

# ----------------------------- 3. the cross-check against PDAL's published numbers
rec("count_vs_pdal", "%d vs %d" % (len(z), PDAL_COUNT))
if len(z) != PDAL_COUNT:
    die("laspy decoded %d points; PDAL published %d for this file" % (len(z), PDAL_COUNT))
for nm, arr in (("x", x), ("y", y), ("z", z)):
    lo, hi = PDAL_BOUNDS[nm]
    rec("bounds_%s" % nm, "%.6f..%.6f vs pdal %.2f..%.2f" % (arr.min(), arr.max(), lo, hi))
    # PDAL published two decimals, and the scale is 0.01, so the bound is half a step.
    if abs(float(arr.min()) - lo) > 5e-3 or abs(float(arr.max()) - hi) > 5e-3:
        die("%s bounds %.6f..%.6f disagree with PDAL's %.2f..%.2f"
            % (nm, arr.min(), arr.max(), lo, hi))
mean_z = float(z.mean())
d_mean = abs(mean_z - PDAL_MEAN_Z)
rec("mean_z_laspy", "%.9f" % mean_z)
rec("mean_z_pdal", "%.4f" % PDAL_MEAN_Z)
rec("mean_z_abs_diff", "%.3e" % d_mean)
if d_mean > PDAL_MEAN_Z_PRECISION:
    die("the mean Z over %d decoded points is %.9f; PDAL published %.4f, and %.3e exceeds "
        "the %.0e that its four published decimals allow"
        % (len(z), mean_z, PDAL_MEAN_Z, d_mean, PDAL_MEAN_Z_PRECISION))
rec("cross_validation", "laspy and PDAL agree on count, bounds and the mean over %d "
    "decoded points to %.1e" % (len(z), d_mean))

# ------------------- 4. the scaled-integer law: exact, not approximate
for i, (nm, scaled, raw) in enumerate((("x", x, np.asarray(las.X, dtype="int64")),
                                       ("y", y, np.asarray(las.Y, dtype="int64")),
                                       ("z", z, np.asarray(las.Z, dtype="int64")))):
    rebuilt = raw * las.header.scales[i] + las.header.offsets[i]
    d = float(np.abs(rebuilt - scaled).max())
    rec("scaled_int_%s" % nm, "%.3e" % d)
    if d != 0.0:
        die("%s: raw*scale + offset differs from the scaled coordinate by %.3e; the LAS "
            "spec makes that an exact relation" % (nm, d))
rec("scaled_integer_law", "x == X_raw*scale + offset exactly, on all three axes")

# ----------- 5. write/read: on the grid it is exact, off it quantises by <= scale/2
SCALE, OFFSET, N = 0.01, 1000.0, 1000
rng = np.random.default_rng(20261012)
ongrid = rng.integers(-50000, 50000, N) * SCALE + OFFSET
offgrid = ongrid + rng.uniform(-SCALE / 2, SCALE / 2, N)


def roundtrip(coords, tag):
    hdr = laspy.LasHeader(point_format=3, version="1.2")
    hdr.scales = np.array([SCALE, SCALE, SCALE])
    hdr.offsets = np.array([OFFSET, OFFSET, OFFSET])
    d = laspy.LasData(hdr)
    d.x = coords
    d.y = coords
    d.z = coords
    fn = "rt_%s.las" % tag
    d.write(fn)
    back = laspy.read(fn)
    return float(np.abs(np.asarray(back.x, dtype="float64") - coords).max())


d_on = roundtrip(ongrid, "ongrid")
rec("roundtrip_on_grid", "%.6e" % d_on)
if d_on != 0.0:
    die("coordinates written on the quantisation grid came back %.6e different; on-grid "
        "values are representable exactly and must survive" % d_on)

# THE CONTROL: off the grid the write MUST lose something, and by a known bound.
d_off = roundtrip(offgrid, "offgrid")
rec("roundtrip_off_grid", "%.6e  (scale/2 = %.6e)" % (d_off, SCALE / 2))
if d_off == 0.0:
    die("coordinates deliberately placed off the quantisation grid round-tripped "
        "exactly, so the on-grid result above does not demonstrate quantisation at all")
if d_off > SCALE / 2:
    die("off-grid coordinates moved %.6e, beyond the %.6e that a %g scale permits"
        % (d_off, SCALE / 2, SCALE))
rec("quantisation_control", "on-grid exact; off-grid %.4e, just inside the %.4e bound"
    % (d_off, SCALE / 2))

# --------------------------------- 6. LAZ is lossless: identical integers, smaller file
hdr = laspy.LasHeader(point_format=3, version="1.2")
hdr.scales = np.array([SCALE, SCALE, SCALE])
hdr.offsets = np.array([OFFSET, OFFSET, OFFSET])
d = laspy.LasData(hdr)
d.x = ongrid
d.y = ongrid
d.z = ongrid
d.write("c.las")
d.write("c.laz")
a, b = laspy.read("c.las"), laspy.read("c.laz")
same = all(np.array_equal(np.asarray(getattr(a, k)), np.asarray(getattr(b, k)))
           for k in ("X", "Y", "Z"))
s_las, s_laz = os.path.getsize("c.las"), os.path.getsize("c.laz")
rec("laz_vs_las", "%d vs %d bytes (%.2fx), raw integers identical: %s"
    % (s_las, s_laz, s_las / s_laz, same))
if not same:
    die("the same points written as .las and .laz decoded to different integers; LAZ is "
        "a lossless codec")
if s_laz >= s_las:
    die("the .laz (%d bytes) is not smaller than the .las (%d)" % (s_laz, s_las))

# ------------------------------------------------------------------ 7. determinism
again = laspy.read(LAZ)
if float(np.asarray(again.z, dtype="float64").mean()) != mean_z:
    die("reading the same file twice gave a different mean Z")
rec("determinism", "two reads of the staged cloud agree exactly")

with open("score.tsv", "w") as fh:
    fh.write("observable\tvalue\n")
    for k, v in ROWS:
        fh.write("%s\t%s\n" % (k, v))

print("LASPY OK")
