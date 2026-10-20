#!/usr/bin/env python3
"""mesalib-real: a 103.7-million-triangle SRTM mosaic through VTK and Mesa llvmpipe, checked per pixel.

    python3 -u terrain.py backends                 # no DISPLAY: which display-free backends exist (diagnostic)
    python3 -u terrain.py prep  <out> <tile.hgt.gz>...   # decode + mosaic -> /tmp/work/dem.npy
    python3 -u terrain.py exact <out>              # the per-pixel identities (ID ladder, depth, flood, control)
    python3 -u terrain.py shade <out> <threads> <frames>  # shaded 4K turntable: timings and frame hashes

Each mode is its own process: LP_NUM_THREADS is read when the GL screen is created, and a display-free
VTK segfaults rather than raising, so neither can be varied or probed inside one interpreter.

THE GEOMETRY IS EXACT, SO THE PREDICTION IS. The mosaic's vertices sit at integer world (col, row),
each cell split NW-SE into two triangles. Under an orthographic top-down camera at 9/16, 9/8 and 9/4
pixels per cell, every vertex lands on a multiple of 1/16 px, which llvmpipe's 1/256-px snapping
represents exactly, and every pixel centre is (2p+1) * 2^j / 18 cells from the origin: an integer
numerator over 18. So which triangle owns a pixel centre is integer arithmetic, and so is the
elevation there (18*z is an integer). The rendered ID buffer is compared with that prediction at
every pixel. A centre exactly on an edge is a tie, owned by whichever side GL's fill rule picks: it
must be a triangle that contains the point, and which side won is reported per edge class.
"""
import gzip
import hashlib
import json
import os
import resource
import sys
import time

MODE = sys.argv[1]
ROWS = []
OUTDIR = sys.argv[2] if len(sys.argv) > 2 else "/tmp/out"
WORK = "/tmp/work"


def rec(k, v):
    v = str(v)                      # a description must never be able to fail the run
    print("%s\t%s" % (k, v), flush=True)
    ROWS.append((k, v))


def die(msg):
    rec("FAIL", msg)
    dump()
    sys.exit(1)


def dump():
    with open(os.path.join(OUTDIR, "%s.tsv" % TAG), "w") as fh:
        for k, v in ROWS:
            fh.write("%s\t%s\n" % (k, v))


def rss():
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 2 ** 20   # KiB -> GiB


TAG = MODE if MODE != "shade" else "shade-t%s" % sys.argv[3]

# --------------------------------------------------------------------------- backend diagnostic
if MODE == "backends":
    TAG = "backends"
    OUTDIR = sys.argv[2] if len(sys.argv) > 2 else "/tmp/out"
    rec("display", os.environ.get("DISPLAY", "<unset>"))
    try:
        import vtk
        rw = vtk.vtkRenderWindow()
        rw.SetOffScreenRendering(1)
        rw.SetSize(32, 32)
        rw.AddRenderer(vtk.vtkRenderer())
        rw.Render()
        rec("display_free_render", "SUCCEEDED (class %s)" % rw.GetClassName())
    except BaseException as exc:
        rec("display_free_render", "FAILED: %s: %s" % (type(exc).__name__, exc))
    dump()
    sys.exit(0)

import numpy as np

C = 7200                     # cells per side of the mosaic
NV = C + 1                   # vertices per side
NT = 2 * C * C               # triangles
M = 2 ** 24 - 2              # ID codes 0..M-1; 0xFFFFFE is the flood plane, 0xFFFFFF the background
PLANE, BG = M, M + 1
H0_NUM, H0_DEN = 72001, 36   # flood level 2000 + 1/36 m: 18*z is an integer, so no pixel can tie it

# ------------------------------------------------------------------------------- decode + mosaic
if MODE == "prep":
    tiles = {}
    for f in sys.argv[3:]:
        name = os.path.basename(f).split(".")[0]
        raw = gzip.open(f, "rb").read()
        if len(raw) != 3601 * 3601 * 2:
            die("%s: %d bytes, a 1-arc-second .hgt is 3601*3601*2 = %d" % (name, len(raw), 3601 * 3601 * 2))
        a = np.frombuffer(raw, dtype=">i2").reshape(3601, 3601).astype(np.int16)
        nvoid = int((a == -32768).sum())
        rec("tile_%s" % name, "3601x3601 min %d max %d voids %d sha256(raw) %s"
            % (a.min(), a.max(), nvoid, hashlib.sha256(raw).hexdigest()))
        if nvoid:
            die("%s has %d void cells (-32768); the skadi tiles are void-filled" % (name, nvoid))
        tiles[name] = a
    nw, ne, sw, se = tiles["N46E006"], tiles["N46E007"], tiles["N45E006"], tiles["N45E007"]
    # Adjacent .hgt tiles duplicate their shared edge: N46E006's south row IS N45E006's north row
    # (latitude 46), and N45E006's east column IS N45E007's west column (longitude 7).
    shared = {"lat46_west": (nw[-1, :], sw[0, :]), "lat46_east": (ne[-1, :], se[0, :]),
              "lon7_north": (nw[:, -1], ne[:, 0]), "lon7_south": (sw[:, -1], se[:, 0])}
    for k, (p, q) in shared.items():
        nd = int((p != q).sum())
        rec("shared_edge_%s" % k, "%d of 3601 differ (max |d| %d m)" % (nd, int(np.abs(p.astype(int) - q).max())))
    dem = np.zeros((NV, NV), dtype=np.int16)
    dem[:3601, :3601] = nw
    dem[:3601, 3600:] = ne
    dem[3600:, :3601] = sw
    dem[3600:, 3600:] = se
    rec("mosaic", "%dx%d vertices, %d triangles, elevation %d..%d m" % (NV, NV, NT, dem.min(), dem.max()))
    os.makedirs(WORK, exist_ok=True)
    np.save(os.path.join(WORK, "dem.npy"), dem)
    rec("peak_rss_gib", "%.2f" % rss())
    dump()
    sys.exit(0)

# ------------------------------------------------------------------------------ VTK scene parts
import vtk
from vtkmodules.util.numpy_support import numpy_to_vtk, vtk_to_numpy, numpy_to_vtkIdTypeArray

vtk.vtkObject.GlobalWarningDisplayOff()
dem = np.load(os.path.join(WORK, "dem.npy"))
rec("vtk", vtk.vtkVersion.GetVTKVersion())
rec("numpy", getattr(np, "__version__", "unknown"))
rec("lp_num_threads", os.environ.get("LP_NUM_THREADS", "<unset>"))


def connectivity():
    """Triangles of cell (r, c), vertex v(r, c) = r*NV + c: T0 = (a, d, e) holds the SW corner, T1 =
    (a, e, b) the NE one, a/b/d/e = NW/NE/SW/SE. Triangle k = 2*(r*C + c) + t."""
    r, c = np.meshgrid(np.arange(C, dtype=np.int64), np.arange(C, dtype=np.int64), indexing="ij")
    a = (r * NV + c).ravel()
    b, d, e = a + 1, a + NV, a + NV + 1
    conn = np.empty((C * C, 2, 3), dtype=np.int64)
    conn[:, 0, 0], conn[:, 0, 1], conn[:, 0, 2] = a, d, e
    conn[:, 1, 0], conn[:, 1, 1], conn[:, 1, 2] = a, e, b
    return conn.reshape(-1)


def polydata(zsign=1.0, zscale=1.0, id_colours=True):
    r, c = np.meshgrid(np.arange(NV, dtype=np.float32), np.arange(NV, dtype=np.float32), indexing="ij")
    pts = np.empty((NV * NV, 3), dtype=np.float32)
    pts[:, 0] = c.ravel()
    pts[:, 1] = (C - r).ravel()                      # row 0 is the north edge; y points north
    pts[:, 2] = zsign * zscale * dem.ravel().astype(np.float32)
    del r, c
    vp = vtk.vtkPoints()
    vp.SetData(numpy_to_vtk(pts, deep=1))
    del pts
    ca = vtk.vtkCellArray()
    offs = np.arange(0, 3 * NT + 1, 3, dtype=np.int64)
    ca.SetData(numpy_to_vtkIdTypeArray(offs, deep=1), numpy_to_vtkIdTypeArray(connectivity(), deep=1))
    pd = vtk.vtkPolyData()
    pd.SetPoints(vp)
    pd.SetPolys(ca)
    if id_colours:
        code = np.arange(NT, dtype=np.int64) % M
        col = np.empty((NT, 3), dtype=np.uint8)
        col[:, 0], col[:, 1], col[:, 2] = code & 255, (code >> 8) & 255, (code >> 16) & 255
        del code
        arr = numpy_to_vtk(col, deep=1, array_type=vtk.VTK_UNSIGNED_CHAR)
        arr.SetName("id")
        pd.GetCellData().SetScalars(arr)
    return pd


def flat_actor(pd, rgb=None):
    mp = vtk.vtkPolyDataMapper()
    mp.SetInputData(pd)
    if rgb is None:
        mp.SetScalarModeToUseCellData()
        mp.SetColorModeToDirectScalars()            # the bytes are the colour; no lookup table
        mp.ScalarVisibilityOn()
    else:
        mp.ScalarVisibilityOff()
    ac = vtk.vtkActor()
    ac.SetMapper(mp)
    pr = ac.GetProperty()
    if rgb is not None:
        pr.SetColor(*[x / 255.0 for x in rgb])
    pr.SetAmbient(1.0)            # flat colour: all ambient, so light position cannot enter
    pr.SetDiffuse(0.0)
    pr.SetSpecular(0.0)
    pr.LightingOff()
    pr.SetInterpolationToFlat()
    return ac


def window(w, h):
    rw = vtk.vtkRenderWindow()
    rw.SetOffScreenRendering(1)
    rw.SetMultiSamples(0)         # no anti-aliasing: an AA edge pixel would blend two IDs
    rw.SetSize(w, h)
    ren = vtk.vtkRenderer()
    rw.AddRenderer(ren)
    return rw, ren


def caps(rw):
    out = {}
    for line in rw.ReportCapabilities().splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            out[k.strip()] = v.strip()
    return out


def read_rgb(rw, w, h):
    f = vtk.vtkWindowToImageFilter()
    f.SetInput(rw)
    f.SetInputBufferTypeToRGB()
    f.ReadFrontBufferOff()
    f.Modified()
    f.Update()
    o = f.GetOutput()
    d = o.GetDimensions()
    if d[0] != w or d[1] != h:
        die("asked for a %dx%d buffer, got %s" % (w, h, str(d)))
    return vtk_to_numpy(o.GetPointData().GetScalars()).reshape(h, w, 3)   # row 0 = bottom


def read_z(rw, n):
    za = vtk.vtkFloatArray()
    rw.GetZbufferData(0, 0, n - 1, n - 1, za)
    return vtk_to_numpy(za).reshape(n, n)


def topdown(ren, n_cells_half, zc, near, far):
    cam = ren.GetActiveCamera()
    cam.ParallelProjectionOn()
    cam.SetFocalPoint(C / 2.0, C / 2.0, 0.0)
    cam.SetPosition(C / 2.0, C / 2.0, zc)
    cam.SetViewUp(0.0, 1.0, 0.0)
    cam.SetParallelScale(n_cells_half)
    cam.SetClippingRange(near, far)


# =============================================================================== exact identities
if MODE == "exact":
    LADDER = [(4, 4050), (3, 8100), (2, 16200)]     # 2^j: pixel centre = (2p+1) * 2^j / 18 cells
    ZC, NEAR, FAR = 10000.0, 4000.0, 16000.0
    # Depth VALUES are not exact and GL does not ask them to be: llvmpipe interpolates each triangle's
    # depth plane in float32, and the rounding grows with the triangle's slope. So depth values are
    # REPORTED against the geometry (worst error, and that error in units of u32 * (slope * frame + 1)),
    # and what is asserted is the depth TEST: occlusion must follow the depth buffer exactly, and may
    # depart from exact geometry only where the depth buffer's own error explains it.
    U32, DEPTH_K = 2.0 ** -24, 1.0
    meta = {}
    T = {}

    def axis(n, j):
        """For pixel index p along an axis: the integer numerator X = (2p+1)*2^j over 18."""
        X = (2 * np.arange(n, dtype=np.int64) + 1) * (2 ** j)
        return X // 18, X % 18                      # cell index, position in 18ths

    def predict_rows(j, n, py0, py1):
        """Predicted code, 18*z, tie class for pixel rows [py0, py1) (row 0 = bottom = south)."""
        c, u = axis(n, j)
        Y = (2 * np.arange(py0, py1, dtype=np.int64) + 1) * (2 ** j)
        R = C * 18 - Y                              # distance from the north edge, in 18ths
        r, w = R // 18, R % 18
        cc, uu = c[None, :], u[None, :]
        rr, ww = r[:, None], w[:, None]
        t1 = ww < uu                                # above the NW-SE diagonal: the NE triangle
        k = 2 * (rr * C + cc) + t1
        # 18*z by barycentric interpolation on the owning triangle (on a tie both sides agree)
        rr_ = np.minimum(rr, C - 1)
        cc_ = np.minimum(cc, C - 1)
        za = dem[rr_, cc_].astype(np.int64)
        zb = dem[rr_, cc_ + 1].astype(np.int64)
        zd = dem[rr_ + 1, cc_].astype(np.int64)
        ze = dem[rr_ + 1, cc_ + 1].astype(np.int64)
        z18 = np.where(t1, 18 * za + uu * (zb - za) + ww * (ze - zb),
                       18 * za + ww * (zd - za) + uu * (ze - zd))
        tie_v = (uu == 0) & (ww != 0)
        tie_h = (ww == 0) & (uu != 0)
        tie_d = (uu == ww) & (uu != 0)
        tie_x = (uu == 0) & (ww == 0)
        slope = np.where(t1, np.abs(zb - za) + np.abs(ze - zb), np.abs(ze - zd) + np.abs(zd - za))
        zbnd = DEPTH_K * U32 * (slope * float(C) / (FAR - NEAR) + 1.0)   # metres/cell * cells / range
        return k, z18, tie_v, tie_h, tie_d, tie_x, rr, cc, uu, ww, zbnd

    pd = polydata()
    rec("triangles", pd.GetNumberOfCells())
    rec("points", pd.GetNumberOfPoints())
    if pd.GetNumberOfCells() != NT:
        die("polydata holds %d triangles, %d built" % (pd.GetNumberOfCells(), NT))
    # VTK's coincident-topology resolution would offset depth (glPolygonOffset semantics). It is
    # recorded and forced off, so the depth buffer is compared with the geometry itself.
    rct = vtk.vtkMapper.GetResolveCoincidentTopology()
    try:
        fr, ur = vtk.reference(0.0), vtk.reference(0.0)
        vtk.vtkMapper.GetResolveCoincidentTopologyPolygonOffsetParameters(fr, ur)
        po = (float(fr), float(ur))
    except Exception as e:      # a description must never fail the run
        po = ("unknown (%s)" % type(e).__name__, "")
    rec("vtk_resolve_coincident_topology", "%s (polygon offset factor %s units %s)" % (rct, po[0], po[1]))
    vtk.vtkMapper.SetResolveCoincidentTopologyToOff()
    act = flat_actor(pd)

    def render(n, actors, zsign=1.0):
        rw, ren = window(n, n)
        ren.SetBackground(1.0, 1.0, 1.0)            # 0xFFFFFF, no triangle's code
        for a in actors:
            ren.AddActor(a)
        topdown(ren, C / 2.0, ZC, NEAR, FAR)
        t0 = time.time()
        rw.Render()
        t1 = time.time()
        rgb = read_rgb(rw, n, n)
        code = rgb[..., 0].astype(np.int32) | (rgb[..., 1].astype(np.int32) << 8) | (rgb[..., 2].astype(np.int32) << 16)
        return rw, ren, code, t1 - t0

    def depth_err(zb, z18, zbnd):
        err = np.abs(zb.astype(np.float64) - (ZC - z18 / 18.0 - NEAR) / (FAR - NEAR))
        return float(err.max()), int((err > zbnd).sum()), float((err / (zbnd / DEPTH_K)).max()), \
            int((err > 2.0 ** -20).sum())

    for j, n in LADDER:
        rw, ren, code, tr = render(n, [act])
        if j == 4:
            cp = caps(rw)
            for key in ("OpenGL vendor string", "OpenGL renderer string", "OpenGL version string"):
                rec(key.replace(" ", "_").lower(), cp.get(key, "?"))
            if cp.get("OpenGL vendor string") != "Mesa" or "llvmpipe" not in cp.get("OpenGL renderer string", ""):
                die("the GL context is not Mesa llvmpipe: %s / %s; every exactness claim rests on it"
                    % (cp.get("OpenGL vendor string"), cp.get("OpenGL renderer string")))
            rec("render_window_class", rw.GetClassName())
        rec("id_%d_render_s" % n, "%.2f" % tr)
        T["id_%d_render_s" % n] = tr
        rec("id_%d_sha256" % n, hashlib.sha256(code.tobytes()).hexdigest())
        bad = 0
        bg = int((code == BG).sum())
        ties = {"v": [0, 0, 0], "h": [0, 0, 0], "d": [0, 0, 0], "x": [0, 0]}   # [first side, second side, other]
        zerr, zbad, zratio, zflat = 0.0, 0, 0.0, 0
        zbuf = read_z(rw, n) if j == 3 else None
        for py0 in range(0, n, 1024):
            py1 = min(n, py0 + 1024)
            k, z18, tv, th, td, tx, rr, cc, uu, ww, zbnd = predict_rows(j, n, py0, py1)
            got = code[py0:py1]
            strict = ~(tv | th | td | tx)
            bad += int(((got != (k % M)) & strict).sum())
            # ties: the two triangles sharing the edge (vertical: T1 of the cell to the west vs T0
            # here; horizontal: T0 of the cell to the north vs T1 here; diagonal: T0 vs T1 here)
            kv0, kv1 = (2 * (rr * C + cc - 1) + 1) % M, (2 * (rr * C + cc)) % M
            kh0, kh1 = (2 * ((rr - 1) * C + cc)) % M, (2 * (rr * C + cc) + 1) % M
            kd0, kd1 = (2 * (rr * C + cc)) % M, (2 * (rr * C + cc) + 1) % M
            for cls, m, a0, a1 in (("v", tv, kv0, kv1), ("h", th, kh0, kh1), ("d", td, kd0, kd1)):
                g0 = m & (got == a0)
                g1 = m & (got == a1)
                ties[cls][0] += int(g0.sum())
                ties[cls][1] += int(g1.sum())
                ties[cls][2] += int((m & ~g0 & ~g1).sum())
            # a vertex is shared by six triangles: any of them contains the point
            near6 = np.zeros(got.shape, dtype=bool)
            for dr, dc, t in ((0, 0, 0), (0, 0, 1), (0, -1, 1), (-1, 0, 0), (-1, -1, 0), (-1, -1, 1)):
                near6 |= got == (2 * ((rr + dr) * C + cc + dc) + t) % M
            ties["x"][0] += int((tx & near6).sum())
            ties["x"][1] += int((tx & ~near6).sum())
            if zbuf is not None:
                e, nb, ra, nf = depth_err(zbuf[py0:py1], z18, zbnd)
                zerr, zratio = max(zerr, e), max(zratio, ra)
                zbad += nb
                zflat += nf
        total = n * n
        nt = sum(sum(v) for v in ties.values())
        rec("id_%d" % n, "%d px, %d strict, %d mismatched, %d background" % (total, total - nt, bad, bg))
        for cls in ("v", "h", "d"):
            rec("id_%d_ties_%s" % (n, cls), "%d / %d / other %d" % tuple(ties[cls]))
        rec("id_%d_ties_vertex" % (n), "%d in one of the 6 / other %d" % tuple(ties["x"]))
        meta["id_%d" % n] = {"strict_mismatch": bad, "background": bg, "ties": ties, "pixels": total}
        if bg:
            die("%d background pixels at %dx%d: the mosaic covers the whole viewport, so GL left gaps" % (bg, n, n))
        if bad:
            die("%d of %d strictly-interior pixel centres at %dx%d hold a triangle other than the one that "
                "contains them" % (bad, total - nt, n, n))
        other = ties["v"][2] + ties["h"][2] + ties["d"][2] + ties["x"][1]
        if other:
            die("%d tie pixels at %dx%d hold a triangle that does not touch the point" % (other, n, n))
        if zbuf is not None:
            rec("depth_%d" % n, "max |err| %.3e normalized (%.3f m); worst err = %.1f u32 * (slope * frame + 1); "
                "%d of %d over 2^-20 (reported, not asserted)" % (zerr, zerr * (FAR - NEAR), zratio, zflat, n * n))
            meta["depth"] = {"max_err": zerr, "worst_ratio": zratio, "over_2^-20": zflat}
        del code, zbuf
        rw.Finalize()
        del rw, ren
        rec("rss_after_id_%d_gib" % n, "%.2f" % rss())

    # ---- the flood plane: occlusion at every pixel, and the control that makes it mean anything
    def plane(z):
        p = vtk.vtkPlaneSource()
        p.SetOrigin(0.0, 0.0, z)
        p.SetPoint1(float(C), 0.0, z)
        p.SetPoint2(0.0, float(C), z)
        p.Update()
        return flat_actor(p.GetOutput(), rgb=(PLANE & 255, (PLANE >> 8) & 255, PLANE >> 16))

    h0 = H0_NUM / H0_DEN
    j, n = 3, 8100
    neg = vtk.vtkActor()                            # the same buffers, mirrored by the model matrix:
    neg.SetMapper(act.GetMapper())                  # a scale of -1 is exact in floating point
    neg.SetProperty(act.GetProperty())
    neg.SetScale(1.0, 1.0, -1.0)
    shown = {}
    for name, ta, zsign in (("flood", act, 1.0), ("control", neg, -1.0)):
        # the terrain alone first: its depth buffer is what the occlusion has to follow
        rw, ren, code, tr = render(n, [ta])
        zt = read_z(rw, n).copy()
        rw.Finalize()
        del rw, ren, code
        rw, ren, code, tr = render(n, [ta, plane(zsign * h0)])
        rec("%s_render_s" % name, "%.2f" % tr)
        zf = read_z(rw, n)
        is_plane = code == PLANE
        pv = np.unique(zf[is_plane])
        rec("%s_plane_depth" % name, "%d distinct value(s), %s" % (len(pv), " ".join("%.9g" % v for v in pv[:4])))
        if len(pv) != 1:
            die("%s: a flat plane rendered at %d distinct depths" % (name, len(pv)))
        dp = float(pv[0])
        # identity 1: a shown terrain pixel keeps exactly the depth it had alone
        nz = int((zf[~is_plane] != zt[~is_plane]).sum())
        # identity 2: the depth test. Terrain shows iff its depth is below the plane's; equal is a tie
        ties = zt == np.float32(dp)
        wrong_test = int(((~is_plane) != (zt < np.float32(dp)))[~ties].sum())
        rec("%s_depth_test" % name, "%d pixels against the depth buffer, %d ties (either side legal), "
            "%d terrain pixels changed depth" % (wrong_test, int(ties.sum()), nz))
        if nz or wrong_test:
            die("%s: occlusion does not follow the depth buffer (%d wrong, %d changed depth)" % (name, wrong_test, nz))
        # identity 3: exact geometry, departing only where the depth buffer's own error explains it
        h0n = (ZC - zsign * h0 - NEAR) / (FAR - NEAR)
        dis = unexplained = idbad = 0
        for py0 in range(0, n, 1024):
            py1 = min(n, py0 + 1024)
            k, z18, tv, th, td, tx = predict_rows(j, n, py0, py1)[:6]
            above = z18 * H0_DEN > 18 * H0_NUM                    # 18 z > 18 h0 = 36000.5, in integers
            geo = above if zsign > 0 else ~above
            ip = is_plane[py0:py1]
            d = (~ip) != geo
            pred = (ZC - zsign * z18 / 18.0 - NEAR) / (FAR - NEAR)
            slack = np.abs(zt[py0:py1].astype(np.float64) - pred) + abs(dp - h0n)
            dis += int(d.sum())
            unexplained += int((d & (np.abs(pred - h0n) > slack)).sum())
            strict = ~(tv | th | td | tx)
            idbad += int((~ip & strict & (code[py0:py1] != (k % M))).sum())
        rec("%s_%d" % (name, n), "terrain %d plane %d; %d differ from exact geometry, %d of them unexplained by "
            "the depth error; %d wrong IDs" % (int((~is_plane).sum()), int(is_plane.sum()), dis, unexplained, idbad))
        if unexplained or idbad:
            die("%s: %d pixels depart from exact geometry by more than the depth buffer's error, %d wrong IDs"
                % (name, unexplained, idbad))
        shown[name] = ~is_plane
        rw.Finalize()
        del rw, ren, code, zf, zt
    swap = int((shown["flood"] == shown["control"]).sum())
    rec("flood_control", "negating z exchanges terrain and plane at all but %d of %d pixels" % (swap, n * n))
    rec("above_2000m_fraction", "%.6f" % (shown["flood"].sum() / float(n * n)))
    rec("peak_rss_gib", "%.2f" % rss())
    json.dump(meta, open(os.path.join(OUTDIR, "exact.json"), "w"), indent=1)
    rec("EXACT", "OK")
    dump()
    sys.exit(0)

# ================================================================================= shaded frames
if MODE == "shade":
    nthreads, nframes = sys.argv[3], int(sys.argv[4])
    W, H = 3840, 2160
    EXAG = 1.5 / 25.0           # metres -> cells (~25 m), with 1.5x vertical exaggeration
    t0 = time.time()
    pd = polydata(zscale=EXAG, id_colours=False)
    # point normals from the grid itself: central differences, the same for every chip
    z = dem.astype(np.float32) * EXAG
    gy, gx = np.gradient(z)     # gy along rows (southward)
    nrm = np.empty((NV * NV, 3), dtype=np.float32)
    nrm[:, 0], nrm[:, 1], nrm[:, 2] = -gx.ravel(), gy.ravel(), 1.0
    nrm /= np.linalg.norm(nrm, axis=1)[:, None]
    del z, gx, gy
    na = numpy_to_vtk(nrm, deep=1)
    na.SetName("Normals")
    pd.GetPointData().SetNormals(na)
    el = numpy_to_vtk(dem.ravel().astype(np.float32), deep=1)
    el.SetName("elevation")
    pd.GetPointData().SetScalars(el)
    del nrm
    lut = vtk.vtkColorTransferFunction()
    for v, rgb in ((0, (0.15, 0.35, 0.15)), (800, (0.45, 0.6, 0.3)), (1600, (0.6, 0.5, 0.35)),
                   (2600, (0.55, 0.5, 0.48)), (3200, (0.95, 0.95, 0.97)), (4810, (1.0, 1.0, 1.0))):
        lut.AddRGBPoint(v, *rgb)
    mp = vtk.vtkPolyDataMapper()
    mp.SetInputData(pd)
    mp.SetLookupTable(lut)
    mp.SetScalarModeToUsePointData()
    mp.ScalarVisibilityOn()
    ac = vtk.vtkActor()
    ac.SetMapper(mp)
    ac.GetProperty().SetInterpolationToGouraud()
    rw, ren = window(W, H)
    ren.SetBackground(0.55, 0.7, 0.9)
    ren.AddActor(ac)
    t_build = time.time() - t0
    rec("build_s", "%.2f" % t_build)
    cam = ren.GetActiveCamera()
    cam.SetFocalPoint(C / 2.0, C / 2.0, 1500 * EXAG)
    cam.SetViewUp(0.0, 0.0, 1.0)
    cam.SetViewAngle(30.0)
    times, shas = [], []
    for i in range(nframes):
        a = 2 * np.pi * i / nframes
        cam.SetPosition(C / 2.0 + 9000 * np.sin(a), C / 2.0 - 9000 * np.cos(a), 4500.0)
        ren.ResetCameraClippingRange()
        t1 = time.time()
        rw.Render()
        rgb = read_rgb(rw, W, H)
        times.append(time.time() - t1)
        shas.append(hashlib.sha256(rgb.tobytes()).hexdigest())
        rec("frame_%02d" % i, "%.3f s %s" % (times[-1], shas[-1]))
        if i == 0:
            if len(np.unique(rgb.reshape(-1, 3)[::97], axis=0)) < 50:
                die("frame 0 has under 50 distinct colours in a sample: a shaded terrain does not render flat")
            if nthreads == os.environ.get("SAVE_FRAME_AT", ""):
                wr = vtk.vtkPNGWriter()
                f = vtk.vtkWindowToImageFilter()
                f.SetInput(rw)
                f.ReadFrontBufferOff()
                f.Update()
                wr.SetInputConnection(f.GetOutputPort())
                wr.SetFileName(os.path.join(OUTDIR, "frame00.png"))
                wr.Write()
    # frame 0 again, after the others: the same context must give the same bytes
    cam.SetPosition(C / 2.0, C / 2.0 - 9000.0, 4500.0)
    ren.ResetCameraClippingRange()
    rw.Render()
    again = hashlib.sha256(read_rgb(rw, W, H).tobytes()).hexdigest()
    rec("frame_00_again", again)
    if again != shas[0]:
        die("re-rendering frame 0 gave different bytes")
    first, rest = times[0], times[1:]
    rec("first_frame_s", "%.3f" % first)
    rec("mean_frame_s", "%.3f" % (sum(rest) / max(1, len(rest))))
    rec("peak_rss_gib", "%.2f" % rss())
    json.dump({"threads": nthreads, "build_s": t_build, "times": times, "sha256": shas},
              open(os.path.join(OUTDIR, "shade-t%s.json" % nthreads), "w"), indent=1)
    rec("SHADE", "OK")
    dump()
    sys.exit(0)

die("unknown mode %s" % MODE)
