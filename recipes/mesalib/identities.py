#!/usr/bin/env python3
"""Headless software GL on Graviton4: exact pixels from Mesa llvmpipe.

Rendering is usually checked by eye, or by a heuristic like "the image has enough
colours". It does not have to be. On a SOFTWARE rasterizer, with lighting off,
multisampling off and an orthographic camera, a flat-shaded quad produces exactly one
RGB value over an exactly known number of pixels -- so every check here is an integer.

 1. THE RENDERER IS ASSERTED, NOT ASSUMED. "This is software GL" is worthless unless
    GL_VENDOR and GL_RENDERER are read back and checked, for the same reason an MPI
    recipe reads its own rank count: a context serviced by something else would still
    produce a plausible image, and the determinism claim below would no longer follow.

 2. THE DEPTH TEST HAS A CONTROL, because the obvious version of it is vacuous. Two
    full-viewport quads with "the nearer one wins" passes identically with depth testing
    DISABLED. So the quads only PARTLY overlap, and the same scene is rendered with the
    depths swapped -- the pixel counts must invert.

 3. EXPECTED COUNTS ARE DERIVED FROM GEOMETRY, then were confirmed on the instance
    before being asserted. Whether an edge landing on a pixel boundary fills that pixel
    is a fill-rule detail, so it was measured rather than predicted; llvmpipe is
    deterministic, which is what makes an exact count legitimate.

Called twice by the spec: `--backends` first with no DISPLAY, to record which GL backends
this env actually offers (a diagnostic, asserting nothing), then with no argument under
Xvfb for the real checks.

Run with `python3 -u` so a killed run still has its output in command.log.
"""
import hashlib
import os
import sys

ROWS = []


def rec(k, v):
    print("%s\t%s" % (k, v))
    ROWS.append((k, v))


def die(msg):
    print("FAIL: %s" % msg)
    sys.exit(1)


# ------------------------------------------------------------------ backend diagnostic
if "--backends" in sys.argv:
    # Deliberately NOT a check. It records whether a display-free context is possible in
    # this image. Asserting "it must fail" would break the day aarchsci adds osmesa --
    # a check that fails when the platform improves is a bad check.
    print("display_free_probe\tDISPLAY=%r" % os.environ.get("DISPLAY"))
    try:
        import vtk
        rw = vtk.vtkRenderWindow()
        rw.SetOffScreenRendering(1)
        rw.SetSize(32, 32)
        ren = vtk.vtkRenderer()
        rw.AddRenderer(ren)
        rw.Render()
        print("display_free_render\tSUCCEEDED (class %s)" % rw.GetClassName())
        print("display_free_caps\t%s" % " | ".join(
            l.strip() for l in rw.ReportCapabilities().splitlines()
            if "OpenGL renderer" in l or "OpenGL vendor" in l))
    except BaseException as exc:
        print("display_free_render\tFAILED: %s: %s" % (type(exc).__name__, exc))
    sys.exit(0)

import numpy as np
import vtk
from vtk.util.numpy_support import vtk_to_numpy

vtk.vtkObject.GlobalWarningDisplayOff()

rec("vtk", vtk.vtkVersion.GetVTKVersion())
rec("numpy", np.__version__)
rec("display", os.environ.get("DISPLAY", "<unset>"))

RED, GREEN, BLACK = (255, 0, 0), (0, 255, 0), (0, 0, 0)


def scene(size, quads):
    """Render flat-shaded, axis-aligned quads orthographically; return an HxWx3 uint8."""
    rw = vtk.vtkRenderWindow()
    rw.SetOffScreenRendering(1)
    rw.SetMultiSamples(0)          # no anti-aliasing: an AA edge would blend colours
    rw.SetSize(size, size)
    ren = vtk.vtkRenderer()
    ren.SetBackground(0.0, 0.0, 0.0)
    rw.AddRenderer(ren)
    for x0, x1, z, col in quads:
        pl = vtk.vtkPlaneSource()
        pl.SetOrigin(x0, -1.0, z)
        pl.SetPoint1(x1, -1.0, z)
        pl.SetPoint2(x0, 1.0, z)
        mp = vtk.vtkPolyDataMapper()
        mp.SetInputConnection(pl.GetOutputPort())
        ac = vtk.vtkActor()
        ac.SetMapper(mp)
        pr = ac.GetProperty()
        pr.SetColor(*[c / 255.0 for c in col])
        pr.SetAmbient(1.0)         # flat colour: all ambient, no diffuse or specular,
        pr.SetDiffuse(0.0)         # so the result cannot depend on light position
        pr.SetSpecular(0.0)
        pr.LightingOff()
        ren.AddActor(ac)
    cam = ren.GetActiveCamera()
    cam.ParallelProjectionOn()     # orthographic: no perspective foreshortening, so a
    cam.SetParallelScale(1.0)      # quad's screen coverage is exactly its world extent
    cam.SetPosition(0, 0, 5)
    cam.SetFocalPoint(0, 0, 0)
    cam.SetViewUp(0, 1, 0)
    rw.Render()
    f = vtk.vtkWindowToImageFilter()
    f.SetInput(rw)
    f.SetInputBufferTypeToRGB()
    f.ReadFrontBufferOff()
    f.Modified()
    f.Update()
    o = f.GetOutput()
    d = o.GetDimensions()
    if d[0] != size or d[1] != size:
        die("asked for a %dx%d buffer, got %s" % (size, size, d))
    return vtk_to_numpy(o.GetPointData().GetScalars()).reshape(d[1], d[0], 3)


def tally(arr):
    flat = arr.reshape(-1, 3)
    vals, counts = np.unique(flat, axis=0, return_counts=True)
    return {tuple(int(x) for x in v): int(c) for v, c in zip(vals, counts)}


# ------------------------------------- 1. the renderer: assert the path, don't assume it
rw = vtk.vtkRenderWindow()
rw.SetOffScreenRendering(1)
rw.SetMultiSamples(0)
rw.SetSize(32, 32)
rw.AddRenderer(vtk.vtkRenderer())
rw.Render()
caps = rw.ReportCapabilities()


def cap(key):
    for line in caps.splitlines():
        if line.strip().startswith(key):
            return line.split(":", 1)[1].strip()
    return ""


vendor, renderer, version = cap("OpenGL vendor"), cap("OpenGL renderer"), cap("OpenGL version")
rec("gl_vendor", vendor)
rec("gl_renderer", renderer)
rec("gl_version", version)
rec("render_window_class", rw.GetClassName())
if vendor != "Mesa":
    die("GL_VENDOR is %r, not Mesa -- this recipe's exactness and determinism claims "
        "rest on software rasterization" % vendor)
if "llvmpipe" not in renderer:
    die("GL_RENDERER is %r; llvmpipe is the software rasterizer this asserts against. A "
        "different renderer may anti-alias or reorder, so the integer counts below would "
        "no longer be the right claim" % renderer)
if "Mesa" not in version:
    die("GL_VERSION is %r, which does not name Mesa" % version)

# -------------------------- 2. a flat quad is exactly one colour over the whole viewport
full = scene(64, [(-1.0, 1.0, 0.0, RED)])
t = tally(full)
rec("fullscreen_colours", "%d" % len(t))
rec("fullscreen_red_px", "%d of %d" % (t.get(RED, 0), 64 * 64))
if len(t) != 1 or t.get(RED, 0) != 64 * 64:
    die("a full-viewport flat quad gave %d colours %s; expected exactly one, red, over "
        "all %d pixels" % (len(t), sorted(t), 64 * 64))

# ------------------- 3. half coverage is exactly half the pixels, at every resolution
for size in (64, 128, 256):
    a = scene(size, [(-1.0, 0.0, 0.0, RED)])
    t = tally(a)
    want = size * size // 2
    rec("half_%d_red_px" % size, "%d of %d" % (t.get(RED, 0), size * size))
    if len(t) != 2:
        die("a half-viewport quad at %d gave %d colours %s -- an anti-aliased edge would "
            "add blends, and multisampling is supposed to be off"
            % (size, len(t), sorted(t)))
    if t.get(RED, 0) != want or t.get(BLACK, 0) != want:
        die("at %dx%d a half-viewport quad covers %d red / %d black pixels; exactly %d "
            "each is the geometry" % (size, size, t.get(RED, 0), t.get(BLACK, 0), want))
rec("half_coverage", "exactly N^2/2 at 64, 128 and 256")

# ------------------------- 4. the depth test, with the control that makes it mean anything
# red spans x in [-1, 1/3] and green x in [-1/3, 1]: each covers 2/3 of the width and they
# overlap over the middle third. Whichever is nearer owns the overlap, so the counts are
# 2/3 and 1/3 -- and they must INVERT when the depths swap.
NEAR, FAR = 0.0, -1.0
for size in (96, 192):
    n = size * size
    if n % 3:
        die("a %dx%d viewport does not divide into thirds, so the expected counts would "
            "not be integers" % (size, size))
    two3, one3 = 2 * n // 3, n // 3

    a = scene(size, [(-1.0, 1.0 / 3.0, NEAR, RED), (-1.0 / 3.0, 1.0, FAR, GREEN)])
    t = tally(a)
    rec("depth_%d_red_near" % size, "red %d green %d" % (t.get(RED, 0), t.get(GREEN, 0)))
    if len(t) != 2:
        die("the overlap scene at %d gave %d colours %s; two flat quads filling the "
            "viewport should give exactly two" % (size, len(t), sorted(t)))
    if t.get(RED, 0) != two3 or t.get(GREEN, 0) != one3:
        die("with red nearer at %dx%d: red %d green %d, expected %d and %d"
            % (size, size, t.get(RED, 0), t.get(GREEN, 0), two3, one3))

    # THE CONTROL. Same geometry, depths exchanged. If the counts do NOT invert, the
    # check above was not testing the depth buffer at all -- it would also pass with
    # depth testing disabled, where the last-drawn quad simply wins.
    b = scene(size, [(-1.0, 1.0 / 3.0, FAR, RED), (-1.0 / 3.0, 1.0, NEAR, GREEN)])
    tb = tally(b)
    rec("depth_%d_green_near" % size,
        "red %d green %d" % (tb.get(RED, 0), tb.get(GREEN, 0)))
    if tb.get(GREEN, 0) != two3 or tb.get(RED, 0) != one3:
        die("swapping the depths at %dx%d gave red %d green %d, expected the inverse "
            "(%d green, %d red) -- the depth buffer is not deciding the overlap"
            % (size, size, tb.get(RED, 0), tb.get(GREEN, 0), two3, one3))
rec("depth_control", "counts invert exactly when the depths swap")

# --------------------------------------------- 5. determinism, across fresh GL contexts
digs = []
for _ in range(3):
    a = scene(96, [(-1.0, 1.0 / 3.0, NEAR, RED), (-1.0 / 3.0, 1.0, FAR, GREEN)])
    digs.append(hashlib.sha256(a.tobytes()).hexdigest())
rec("render_sha256", digs[0])
rec("determinism", "identical across 3 fresh render windows"
    if len(set(digs)) == 1 else "DIFFERS")
if len(set(digs)) != 1:
    die("three renders of the same scene gave %d distinct images: %s"
        % (len(set(digs)), [d[:16] for d in digs]))

with open("score.tsv", "w") as fh:
    fh.write("observable\tvalue\n")
    for k, v in ROWS:
        fh.write("%s\t%s\n" % (k, v))

print("MESALIB OK")
