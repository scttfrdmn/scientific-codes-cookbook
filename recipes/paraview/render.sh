#!/usr/bin/env bash
# The paraview task, staged by stage-inputs.sh and pinned by sha256 in 01-render.task.json
# (inline, the spec exceeds EC2's 16 KiB user-data limit).
set -eo pipefail
# The env's root: /opt/conda in the aarchsci image, $CONDA_PREFIX in the x86 twin.
export PATH="${CONDA_PREFIX:-/opt/conda}/bin:$PATH"
export LIBGL_ALWAYS_SOFTWARE=1 GALLIUM_DRIVER=llvmpipe XDG_CACHE_HOME=/tmp HOME=/tmp
export TMPDIR=/tmp LC_ALL=C
cd /tmp

echo "== write the pipeline: analytic sphere -> contour -> integrate -> render =="
# Nothing is staged: the field is r = |x| sampled on a uniform grid, so the isosurface at
# r = R is a sphere whose area and volume are known in closed form. That is what makes the
# convergence ladder below possible -- there is an exact answer to converge TO.
cat > iso.py <<'EOF'
from paraview.simple import (Wavelet, Calculator, Contour, IntegrateVariables, Clip,
                             GetActiveViewOrCreate, Show, ColorBy, GetDisplayProperties,
                             Render, SaveScreenshot, servermanager)
import math, sys, time, json

def peak_rss_mib():
    try:
        for line in open("/proc/self/status"):
            if line.startswith("VmHWM"):
                return int(line.split()[1]) / 1024.0
    except OSError:
        pass
    return -1.0

rows = []
finest = None
for n in (32, 64, 128, 256):
    # R scales with the grid so that cells-per-radius doubles with n, and the sphere stays
    # clear of the boundary. Wavelet's point coordinates ARE its extent indices, so a fixed
    # R with a growing extent would change the resolution and the geometry at once.
    R = 0.6 * n
    t0 = time.time()
    w = Wavelet(); w.WholeExtent = [-n, n, -n, n, -n, n]
    calc = Calculator(Input=w); calc.ResultArrayName = "r"; calc.Function = "mag(coords)"
    c = Contour(Input=calc, ContourBy=["POINTS", "r"], Isosurfaces=[R])
    c.UpdatePipeline()
    poly = servermanager.Fetch(c)
    V, F = poly.GetNumberOfPoints(), poly.GetNumberOfCells()
    iv = IntegrateVariables(Input=c); iv.UpdatePipeline()
    area = servermanager.Fetch(iv).GetCellData().GetArray("Area").GetValue(0)
    t_surf = time.time() - t0
    cl = Clip(Input=calc); cl.ClipType = "Scalar"; cl.Scalars = ["POINTS", "r"]
    cl.Value = R; cl.Invert = 1
    iv2 = IntegrateVariables(Input=cl); iv2.UpdatePipeline()
    vol = servermanager.Fetch(iv2).GetCellData().GetArray("Volume").GetValue(0)
    rows.append({"n": 2 * n + 1, "points": (2 * n + 1) ** 3, "R": R,
                 "V": V, "F": F, "chi": V - F / 2.0,
                 "area": area, "area_exact": 4 * math.pi * R * R,
                 "vol": vol, "vol_exact": 4.0 / 3.0 * math.pi * R ** 3,
                 "t_surface": t_surf, "t_total": time.time() - t0,
                 "peak_rss_mib": peak_rss_mib()})
    print(f"{2*n+1}^3 done, {time.time()-t0:.1f}s", flush=True)
    finest = c

view = GetActiveViewOrCreate("RenderView"); view.ViewSize = [400, 300]
Show(finest, view)
ColorBy(GetDisplayProperties(finest, view), ("POINTS", "r"))
Render()
SaveScreenshot("/tmp/render.png", view)
json.dump(rows, open("/tmp/iso.json", "w"), indent=1)
print("RENDER_OK", flush=True)
EOF

echo "== headless: Xvfb + llvmpipe GLX, no GPU, no display =="
Xvfb :99 -screen 0 1024x768x24 >/dev/null 2>&1 &
XVFB=$!
for i in $(seq 1 200); do [ -S /tmp/.X11-unix/X99 ] && break; sleep 0.05; done
export DISPLAY=:99
# Capture pvbatch's output whatever happens: if this dies at TTL the log is the only
# evidence, and the smoke check reads the JSON rather than trusting an exit code.
pvbatch /tmp/iso.py > pvbatch.out 2>&1 && PV_RC=0 || PV_RC=$?
kill "$XVFB" 2>/dev/null || true
tail -8 pvbatch.out
echo "pvbatch rc=$PV_RC"

python3 -u - <<'PYEOF' > smoke-check.txt 2>&1 || { cat smoke-check.txt; exit 1; }
import json, os, sys
fail = []
def check(label, ok, detail):
    print(f"{label:30s} {detail}")
    if not ok: fail.append(label)
def obs(label, detail):
    print(f"{label:30s} {detail}   [observation]")

out = open("/tmp/pvbatch.out").read()
# RENDER_OK is printed on the last line of the script, so it is a completion sentinel: a
# run killed part-way through the ladder cannot print it however many rungs finished.
check("pvbatch_completed", "RENDER_OK" in out, "RENDER_OK present  want present")
rows = json.load(open("/tmp/iso.json"))
check("ladder_complete", len(rows) == 4,
      f"{len(rows)} resolutions  want 4 (65/129/257/513 cubed)")

print(f"\n{'grid':>8s} {'points':>12s} {'V':>8s} {'F':>8s} {'chi':>6s} "
      f"{'area err %':>11s} {'vol err %':>11s} {'s':>6s}")
for r in rows:
    ae = 100 * (r["area"] / r["area_exact"] - 1)
    ve = 100 * (r["vol"] / r["vol_exact"] - 1)
    print(f"{r['n']:6d}^3 {r['points']:12,} {r['V']:8d} {r['F']:8d} "
          f"{r['chi']:+6.1f} {ae:+11.4f} {ve:+11.4f} {r['t_total']:6.1f}")
print()

# The Euler characteristic of a closed triangle mesh is V - E + F, and a closed triangle
# mesh has E = 3F/2, so chi = V - F/2. For a topological sphere that is exactly 2 -- an
# INTEGER identity, independent of resolution, and the one check here with no tolerance at
# all. A surface that leaked through the boundary, or came back as two components, or
# carried unmerged duplicate points, would not give 2.
chis = [r["chi"] for r in rows]
check("euler_characteristic", all(c == 2.0 for c in chis),
      f"V - F/2 = {chis} at {len(rows)} resolutions  want exactly 2 at every one")

# Marching cubes on a smooth surface is second-order accurate, so halving the cell size
# must quarter the error. Asserting the RATE rather than a value is what makes this a
# method check instead of a band: a correct-but-first-order implementation would still sit
# inside any single-resolution tolerance you picked, and would fail here.
def ratios(key, exact_key):
    errs = [abs(r[key] / r[exact_key] - 1) for r in rows]
    return errs, [errs[i] / errs[i + 1] for i in range(len(errs) - 1)]

for label, key, exact_key, bound in (("area", "area", "area_exact", 5e-5),
                                     ("volume", "vol", "vol_exact", 1e-4)):
    errs, rs = ratios(key, exact_key)
    obs(f"{label}_errors", "  ".join(f"{e:.2e}" for e in errs))
    check(f"{label}_second_order", all(3.5 <= x <= 4.5 for x in rs),
          f"error ratio per doubling {[round(x, 2) for x in rs]}  "
          f"want 3.5-4.5 (O(h^2) predicts 4)")
    check(f"{label}_finest_accurate", errs[-1] < bound,
          f"{errs[-1]:.2e} relative at {rows[-1]['n']}^3  want < {bound:.0e}")

# ---- the render actually drew something -------------------------------------------
from PIL import Image
path = "/tmp/render.png"
check("png_exists", os.path.isfile(path) and os.path.getsize(path) > 1000,
      f"{os.path.getsize(path) if os.path.isfile(path) else 0} B  want > 1000")
im = Image.open(path).convert("RGB")
check("png_size", im.size == (400, 300), f"{im.size}  want (400, 300)")
colors = im.getcolors(10 ** 6) or []
check("real_geometry", len(colors) > 50,
      f"{len(colors)} distinct colours  want > 50 (a shaded surface, not a flat fill)")
dominant = max(c for c, _ in colors) / float(400 * 300) if colors else 1.0
check("not_blank", dominant < 0.98,
      f"{dominant:.1%} is one colour  want < 98% (something drew)")
lo, hi = im.convert("L").getextrema()
check("luminance_range", hi - lo > 40, f"{lo}..{hi}  want range > 40")

obs("peak_rss", f"{max(r['peak_rss_mib'] for r in rows):.0f} MiB at "
                f"{rows[-1]['n']}^3 ({rows[-1]['points']:,} points)")
obs("wall_total", f"{sum(r['t_total'] for r in rows):.1f}s over the whole ladder")

if fail:
    print("FAILED: " + ", ".join(fail)); sys.exit(1)
PYEOF
cat smoke-check.txt
echo "SMOKE CHECK PASSED"
