# paraview-real: a week of hourly ERA5 3D temperature through a pvbatch pipeline.
#
#   pvbatch week.py <out dir> <file.nc> [file.nc ...]
#
# Per hour (each file holds 24): read T on 1440 x 721 x 37 (lon, lat, pressure level) with
# ParaView's NetCDF CF reader, contour the 0 degC isotherm (T = 273.15 K), find its boundary and
# non-manifold edges, integrate its area, clip the volume into below/above freezing and integrate
# both, integrate the whole volume, and render a frame. numpy then recomputes, from the same
# array, the numbers the filters must reproduce. One row per hour goes to hours.tsv; the assertions
# are made at the end, over all hours, and PARAVIEW REAL OK is the last line printed.
import sys, os, time, json, math, resource, ctypes, glob
import numpy as np
from paraview.simple import (NetCDFReader, Contour, FeatureEdges, IntegrateVariables, Clip,
                             Outline, GetActiveViewOrCreate, Show, Hide, Render, SaveScreenshot,
                             Delete, servermanager)
from vtkmodules.vtkCommonCore import vtkSMPTools
from vtkmodules.util.numpy_support import vtk_to_numpy

OUT = sys.argv[1]
FILES = sys.argv[2:]
ISO = 273.15
EPS = np.finfo(np.float64).eps
os.makedirs(os.path.join(OUT, "frames"), exist_ok=True)


def s(x):
    return str(x)


def corename():
    for p in glob.glob(os.path.join(sys.prefix, "lib", "libopenblas*.so*")):
        try:
            lib = ctypes.CDLL(p)
            lib.openblas_get_corename.restype = ctypes.c_char_p
            return lib.openblas_get_corename().decode()
        except Exception as e:  # a description must never fail the run
            return "unknown (%s)" % type(e).__name__
    return "no libopenblas found"



# VTK's SMP backend is chosen at run time. conda-forge's ParaView starts on "Sequential" (measured
# on the first probe), which runs every filter on one core whatever the box has. real.sh passes
# the backend to use; the first hour is timed on Sequential as well, so the difference is measured.
WANT = os.environ.get("PV_SMP_BACKEND", "TBB")
meta = {"smp_backend_default": s(vtkSMPTools.GetBackend()),
        "openblas_corename": corename(),
        "numpy": s(getattr(np, "__version__", "unknown"))}
try:
    meta["paraview"] = s(servermanager.vtkSMProxyManager.GetParaViewSourceVersion())
except Exception as e:
    meta["paraview"] = "unknown (%s)" % type(e).__name__


def set_backend(name):
    try:
        vtkSMPTools.SetBackend(name)
    except Exception as e:
        print("SetBackend(%s) raised %s" % (name, type(e).__name__), flush=True)
    return s(vtkSMPTools.GetBackend()), s(vtkSMPTools.GetEstimatedNumberOfThreads())


def one(proxy, arr, assoc="CellData"):
    d = servermanager.Fetch(proxy)
    a = getattr(d, "Get" + assoc)().GetArray(arr)
    return float(a.GetValue(0)) if a is not None else float("nan")


def trap(c):
    """Trapezoid weights on a (possibly non-uniform, possibly decreasing) 1D coordinate."""
    d = np.abs(np.diff(np.asarray(c, dtype=np.float64)))
    w = np.zeros(len(c))
    w[:-1] += d / 2
    w[1:] += d / 2
    return w


def axes(g, nx, ny, nz):
    """The three axis coordinates, from a rectilinear grid's arrays or a structured set's points."""
    if g.IsA("vtkRectilinearGrid"):
        return (vtk_to_numpy(g.GetXCoordinates()).astype(np.float64),
                vtk_to_numpy(g.GetYCoordinates()).astype(np.float64),
                vtk_to_numpy(g.GetZCoordinates()).astype(np.float64))
    if g.IsA("vtkImageData"):
        o, sp = g.GetOrigin(), g.GetSpacing()
        return tuple(o[i] + sp[i] * np.arange(n) for i, n in enumerate((nx, ny, nz)))
    P = vtk_to_numpy(g.GetPoints().GetData()).astype(np.float64).reshape(nz, ny, nx, 3)
    return P[0, 0, :, 0], P[0, :, 0, 1], P[:, 0, 0, 2]


def build(f):
    r = NetCDFReader(FileName=[f])
    r.Dimensions = "(level, latitude, longitude)"
    r.SphericalCoordinates = 0
    r.UpdatePipelineInformation()
    c = Contour(Input=r, ContourBy=["POINTS", "T"], Isosurfaces=[ISO])
    fb = FeatureEdges(Input=c)
    fb.BoundaryEdges, fb.FeatureEdges, fb.NonManifoldEdges, fb.ManifoldEdges = 1, 0, 0, 0
    fn = FeatureEdges(Input=c)
    fn.BoundaryEdges, fn.FeatureEdges, fn.NonManifoldEdges, fn.ManifoldEdges = 0, 0, 1, 0
    lo = Clip(Input=r); lo.ClipType = "Scalar"; lo.Scalars = ["POINTS", "T"]; lo.Value = ISO; lo.Invert = 1
    hi = Clip(Input=r); hi.ClipType = "Scalar"; hi.Scalars = ["POINTS", "T"]; hi.Value = ISO; hi.Invert = 0
    return dict(r=r, c=c, fb=fb, fn=fn, lo=lo, hi=hi, ivc=IntegrateVariables(Input=c),
                ivall=IntegrateVariables(Input=r), ivlo=IntegrateVariables(Input=lo),
                ivhi=IntegrateVariables(Input=hi), ol=Outline(Input=r))


def drop(P):
    for k in ("ol", "ivhi", "ivlo", "ivall", "ivc", "hi", "lo", "fn", "fb", "c", "r"):
        Delete(P[k])


def filters(P, t):
    """Run every ParaView filter for one hour; return its numbers and per-stage seconds."""
    tt = [time.time()]
    P["r"].UpdatePipeline(t); tt.append(time.time())
    P["c"].UpdatePipeline(t); tt.append(time.time())
    P["fb"].UpdatePipeline(t); P["fn"].UpdatePipeline(t); tt.append(time.time())
    P["lo"].UpdatePipeline(t); P["hi"].UpdatePipeline(t); tt.append(time.time())
    for k in ("ivc", "ivall", "ivlo", "ivhi"):
        P[k].UpdatePipeline(t)
    tt.append(time.time())
    di = lambda k: P[k].GetDataInformation()
    out = dict(V=di("c").GetNumberOfPoints(), F=di("c").GetNumberOfCells(),
               B=di("fb").GetNumberOfCells(), NM=di("fn").GetNumberOfCells(),
               nlo=di("lo").GetNumberOfCells(), nhi=di("hi").GetNumberOfCells(),
               area=one(P["ivc"], "Area"), vol_all=one(P["ivall"], "Volume"),
               vol_lo=one(P["ivlo"], "Volume"), vol_hi=one(P["ivhi"], "Volume"),
               intT_all=one(P["ivall"], "T", "PointData"),
               intT_lo=one(P["ivlo"], "T", "PointData"), intT_hi=one(P["ivhi"], "T", "PointData"))
    tt.append(time.time())
    dt = [tt[i + 1] - tt[i] for i in range(len(tt) - 1)]
    return out, dict(t_read=dt[0], t_contour=dt[1], t_edges=dt[2], t_clip=dt[3],
                     t_integrate=dt[4] + dt[5])


# ---- the backend comparison: hour 0 of the first file, Sequential, then the backend in use ----
smp = {}
for name in ("Sequential", WANT):
    got, nthr = set_backend(name)
    P = build(FILES[0])
    t0 = P["r"].TimestepValues[0]
    o, tm = filters(P, t0)
    smp[name] = dict(backend=got, threads=nthr, contour_clip_integrate_s=round(
        tm["t_contour"] + tm["t_edges"] + tm["t_clip"] + tm["t_integrate"], 3), V=o["V"], F=o["F"],
        area=repr(o["area"]), vol_lo=repr(o["vol_lo"]))
    drop(P)
    print("smp", name, json.dumps(smp[name]), flush=True)
meta["smp_comparison"] = smp
meta["smp_backend"] = set_backend(WANT)
print("meta", json.dumps(meta), flush=True)
json.dump(meta, open(os.path.join(OUT, "meta.json"), "w"), indent=1)

view = GetActiveViewOrCreate("RenderView")
view.ViewSize = [800, 450]
view.OrientationAxesVisibility = 0
cols = ["file", "hour", "time", "nx", "ny", "nz", "finite", "tmin", "tmax", "ties",
        "V", "edges_numpy", "F", "B", "B_numpy", "nonmanifold", "chi", "area",
        "vol_all", "vol_box", "vol_lo", "vol_hi", "intT_all", "intT_numpy", "intT_lo", "intT_hi",
        "ncells_lo", "ncells_hi",
        "t_read", "t_contour", "t_edges", "t_clip", "t_integrate", "t_render", "t_numpy", "t_hour"]
tsv = open(os.path.join(OUT, "hours.tsv"), "w")
tsv.write("\t".join(cols) + "\n")
rows = []
planes = []
T_START = time.time()
first = True
for fi, f in enumerate(FILES):
    P = build(f)
    times = list(P["r"].TimestepValues)
    print("%s: %d timesteps" % (os.path.basename(f), len(times)), flush=True)
    dc = Show(P["c"], view); do = Show(P["ol"], view)
    for d in (dc, do):
        d.Scale = [1.0, 1.0, -0.18]   # pressure in hPa, 1..1000: flatten it and put 1 hPa on top
    dc.DiffuseColor = [0.30, 0.55, 0.85]; dc.AmbientColor = [0.30, 0.55, 0.85]
    if hasattr(dc, "ColorArrayName"):
        dc.ColorArrayName = ["POINTS", ""]
    for hi_i, t in enumerate(times):
        t0 = time.time()
        o, tm = filters(P, t)
        t5 = time.time()
        view.ViewTime = t
        if first:
            view.ResetCamera()
        Render(view)
        frame = os.path.join(OUT, "frames", "f%03d.png" % len(rows))
        SaveScreenshot(frame, view)
        t6 = time.time()
        # ---- numpy, from the same array: what the filters must reproduce ----
        # The reader's own output, in place (builtin mode). servermanager.Fetch() would copy it,
        # and its copy of a vtkRectilinearGrid carries coordinate arrays one tuple per POINT
        # (38,414,880), zero after the first nx: measured on the second probe, shown once below.
        g = P["r"].GetClientSideObject().GetOutputDataObject(0)
        Ta = g.GetPointData().GetArray("T")
        nx, ny, nz = g.GetDimensions()
        if first:
            print("grid %s dims %s T tuples %d comps %d" % (g.GetClassName(), (nx, ny, nz),
                  Ta.GetNumberOfTuples(), Ta.GetNumberOfComponents()), flush=True)
            gf = servermanager.Fetch(P["r"])
            print("Fetch copy: %s, x coordinate tuples %d" % (gf.GetClassName(),
                  gf.GetXCoordinates().GetNumberOfTuples() if gf.IsA("vtkRectilinearGrid") else -1), flush=True)
            del gf
        A = vtk_to_numpy(Ta).astype(np.float64).reshape(nz, ny, nx)
        xs, ys, zs = axes(g, nx, ny, nz)
        if first:
            for nm, a in (("x", xs), ("y", ys), ("z", zs)):
                print("axis %s n %d first %s last %s" % (nm, len(a), a[:3], a[-3:]), flush=True)
            first = False
        finite = bool(np.isfinite(A).all())
        ties = int(np.count_nonzero(A == ISO))
        up = A >= ISO
        sx = up[:, :, 1:] != up[:, :, :-1]
        sy = up[:, 1:, :] != up[:, :-1, :]
        sz = up[1:, :, :] != up[:-1, :, :]
        edges = int(sx.sum() + sy.sum() + sz.sum())
        bx = sx[0].sum() + sx[-1].sum() + sx[1:-1, 0, :].sum() + sx[1:-1, -1, :].sum()
        by = sy[0].sum() + sy[-1].sum() + sy[1:-1, :, 0].sum() + sy[1:-1, :, -1].sum()
        bz = sz[:, 0, :].sum() + sz[:, -1, :].sum() + sz[:, 1:-1, 0].sum() + sz[:, 1:-1, -1].sum()
        bnum = int(bx + by + bz)
        wx, wy, wz = trap(xs), trap(ys), trap(zs)
        vol_box = float(wx.sum() * wy.sum() * wz.sum())
        intT_np = float(np.einsum("zyx,x,y,z->", A, wx, wy, wz, optimize=True))
        if hi_i == 0:
            # Control for the clip partition: a PLANAR cut (z = 512.3 hPa, between grid levels)
            # makes every clipped piece an exact polyhedron with planar faces, so below + above
            # must equal the whole to rounding. A scalar clip's cut faces are not planar, and the
            # two sides triangulate them independently, so its partition is a method gap instead.
            pl = []
            for inv in (1, 0):
                cp = Clip(Input=P["r"]); cp.ClipType = "Plane"; cp.Invert = inv
                cp.ClipType.Origin = [0.0, 0.0, 512.3]; cp.ClipType.Normal = [0.0, 0.0, 1.0]
                iv = IntegrateVariables(Input=cp); iv.UpdatePipeline(t)
                pl.append(one(iv, "Volume")); Delete(iv); Delete(cp)
            planes.append((os.path.basename(f), pl[0], pl[1], o["vol_all"]))
        t7 = time.time()
        V, F, B = o["V"], o["F"], o["B"]
        chi = V - (3 * F + B) / 2.0 + F
        row = [os.path.basename(f), hi_i, repr(t), nx, ny, nz, int(finite), float(A.min()),
               float(A.max()), ties, V, edges, F, B, bnum, o["NM"], chi, repr(o["area"]),
               repr(o["vol_all"]), repr(vol_box), repr(o["vol_lo"]), repr(o["vol_hi"]),
               repr(o["intT_all"]), repr(intT_np), repr(o["intT_lo"]), repr(o["intT_hi"]),
               o["nlo"], o["nhi"],
               "%.3f" % tm["t_read"], "%.3f" % tm["t_contour"], "%.3f" % tm["t_edges"],
               "%.3f" % tm["t_clip"], "%.3f" % tm["t_integrate"], "%.3f" % (t6 - t5),
               "%.3f" % (t7 - t6), "%.3f" % (t7 - t0)]
        tsv.write("\t".join(str(x) for x in row) + "\n"); tsv.flush()
        rows.append(dict(zip(cols, row)))
        del g, A, up, sx, sy, sz
        print("%s h%02d V %d F %d B %d area %.6g %.1fs rss %.0f MiB" % (
            os.path.basename(f)[-24:-14], hi_i, V, F, B, o["area"], t7 - t0,
            resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0), flush=True)
    Hide(P["c"], view); Hide(P["ol"], view)
    drop(P)
tsv.close()
WALL = time.time() - T_START
RSS = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0 / 1024.0

# ---------------- assertions over every hour ----------------
from PIL import Image
fail = []
lines = []


def check(label, ok, detail):
    lines.append("%-34s %s" % (label, detail))
    if not bool(ok):
        fail.append(label)


def obs(label, detail):
    lines.append("%-34s %s   [observation]" % (label, detail))


n = len(rows)
check("hours", n == 24 * len(FILES), "%d  want %d (24 per daily file)" % (n, 24 * len(FILES)))
check("smp_backend", meta["smp_backend"][0] == WANT,
      "%s with %s threads  want %s (Sequential would be one core)" % (meta["smp_backend"][0], meta["smp_backend"][1], WANT))
check("grid", all((x["nx"], x["ny"], x["nz"]) == (1440, 721, 37) for x in rows),
      "1440 x 721 x 37 every hour")
# NaN passes every one-sided bound, so finiteness is asserted on its own, first.
check("finite", all(x["finite"] == 1 for x in rows), "every value of T finite, every hour")
tmin = min(x["tmin"] for x in rows); tmax = max(x["tmax"] for x in rows)
check("physical_range", 150 < tmin and tmax < 350, "T in [%.2f, %.2f] K  want inside (150, 350)" % (tmin, tmax))
obs("ties", "%d grid values exactly equal %.2f" % (sum(x["ties"] for x in rows), ISO))
bad = [x for x in rows if x["V"] != x["edges_numpy"]]
check("vertices_eq_crossing_edges", not bad,
      "%d/%d hours: contour points == grid edges whose ends straddle 273.15 K (numpy)" % (n - len(bad), n))
bad = [x for x in rows if x["B"] != x["B_numpy"]]
check("boundary_edges_eq_boundary_crossings", not bad,
      "%d/%d hours: boundary edges == crossings on the domain faces (numpy)" % (n - len(bad), n))
bad = [x for x in rows if x["nonmanifold"] != 0]
check("manifold", not bad, "%d/%d hours with 0 non-manifold edges" % (n - len(bad), n))


def rel(a, b):
    return abs(float(a) - float(b)) / abs(float(b))


NC = 1439 * 720 * 36
TOL = NC * EPS     # worst-case relative error of a recursive double sum over NC cells
w = max(rel(x["vol_all"], x["vol_box"]) for x in rows)
check("volume_eq_box", w <= TOL, "worst %.2e relative  want <= N eps = %.2e" % (w, TOL))
# Clip output is integrated as tetrahedra with a SIGNED volume, and ERA5's latitude runs north to
# south, so both halves come back negative while the uncut grid (voxels, |dx dy dz|) is positive.
# The partition is therefore on magnitudes, with both halves required to share one sign.
sg = sorted(set((math.copysign(1, float(x["vol_lo"])), math.copysign(1, float(x["vol_hi"]))) for x in rows))
check("clip_halves_one_sign", all(a == b for a, b in sg), "sign(below), sign(above) of the T clip = %s every hour" % sg)
w = max(rel(abs(a + b), v) for _, a, b, v in planes) if planes else float("nan")
check("plane_clip_partitions_volume", len(planes) == len(FILES) and w <= TOL,
      "z = 512.3 hPa cut, |below + above| = whole, worst %.2e over %d hours  want <= %.2e" % (w, len(planes), TOL))
with open(os.path.join(OUT, "plane-clip.tsv"), "w") as fh:
    fh.write("file\tvol_below\tvol_above\tvol_all\n")
    for p_ in planes:
        fh.write("\t".join(repr(x) if isinstance(x, float) else x for x in p_) + "\n")
w = max(rel(abs(float(x["vol_lo"]) + float(x["vol_hi"])), x["vol_all"]) for x in rows)
obs("scalar_clip_partition_gap", "T = 273.15 K cut, |below + above| vs whole, worst %.2e relative "
    "(non-planar cut faces, triangulated independently on each side)" % w)
w = max(rel(x["intT_all"], x["intT_numpy"]) for x in rows)
check("integral_T_eq_numpy_trapezoid", w <= TOL, "worst %.2e relative  want <= %.2e" % (w, TOL))
w = max(rel(abs(float(x["intT_lo"]) + float(x["intT_hi"])), x["intT_all"]) for x in rows)
obs("clip_integral_T_partition", "below + above vs whole, worst %.2e relative "
    "(cut cells and uncut hexes are integrated on tetrahedra, not trilinearly)" % w)
chis = sorted(set(x["chi"] for x in rows))
obs("euler_characteristic", "V - E + F in [%g, %g] across hours" % (chis[0], chis[-1]))
obs("contour_size", "V %d..%d, F %d..%d" % (min(x["V"] for x in rows), max(x["V"] for x in rows),
                                             min(x["F"] for x in rows), max(x["F"] for x in rows)))
fr = [abs(float(x["vol_lo"])) / float(x["vol_all"]) for x in rows]
obs("below_freezing_fraction", "%.4f..%.4f of the (lon, lat, p) volume" % (min(fr), max(fr)))
good = 0
for i in range(n):
    im = Image.open(os.path.join(OUT, "frames", "f%03d.png" % i)).convert("RGB")
    colors = im.getcolors(10 ** 6) or []
    dom = max(cc for cc, _ in colors) / float(im.size[0] * im.size[1]) if colors else 1.0
    if im.size == (800, 450) and len(colors) > 50 and dom < 0.98:
        good += 1
check("frames", good == n, "%d/%d frames 800x450, > 50 colours, < 98%% one colour" % (good, n))
for k in ("t_read", "t_contour", "t_edges", "t_clip", "t_integrate", "t_render", "t_numpy", "t_hour"):
    obs(k, "%.1f s total, %.2f s/hour" % (sum(float(x[k]) for x in rows), sum(float(x[k]) for x in rows) / n))
obs("wall", "%.1f s for %d hours" % (WALL, n))
obs("peak_rss", "%.2f GiB (ru_maxrss)" % RSS)
open(os.path.join(OUT, "summary.tsv"), "w").write(
    "wall_s\t%.1f\npeak_rss_gib\t%.3f\nhours\t%d\n" % (WALL, RSS, n))
print("\n".join(lines), flush=True)
open(os.path.join(OUT, "smoke-check.txt"), "w").write("\n".join(lines) + "\n")
if fail:
    print("FAILED: " + ", ".join(fail), flush=True)
    sys.exit(1)
print("PARAVIEW REAL OK", flush=True)
