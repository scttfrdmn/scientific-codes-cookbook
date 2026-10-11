#!/usr/bin/env python3
"""datashader on Graviton4: rasterization held to exact counts.

Rasterizing a point cloud is counting, so none of this needs a tolerance. Every check is
an integer identity, an exact invariance, or an elementwise match against an independent
implementation of the same binning.

Three design points, each measured on the instance before it was written down:

 1. THE ORIENTATION IS NOT THE SAME AS numpy's. datashader returns an xarray DataArray
    with dims ('y', 'x') and y ASCENDING -- a point at the lower-left corner lands at
    index [0, 0]. np.histogram2d returns [x, y]. So the comparison is against the
    TRANSPOSE, and that was read off a fixture with single points at known corners rather
    than assumed. Guessing here is how the yt and warpx recipes lost time.

 2. THE BIN-EDGE RULE MATCHES histogram2d, which is what makes the comparison fair rather
    than a coincidence. Probed with points at 0.0, 0.999, 1.0, 1.001, 3.999, 4.0 and
    4.001: both include the upper edge and both drop 4.001, giving [2, 2, 0, 2]. If the
    rules had differed, a disagreement would have been a convention difference and not a
    bug in either tool.

 3. MOST POINTS ARE DELIBERATELY OUTSIDE THE CANVAS. The cloud spans [-1, 2]^2 against a
    [0, 1]^2 canvas, so ~89% of input is out of range. A conservation check on a cloud
    that fitted entirely inside would pass for an implementation that ignored the range.

Run with `python3 -u` so a killed run still has its output in command.log.
"""
import sys
import time

import numpy as np
import pandas as pd
import datashader as ds

ROWS = []


def rec(k, v):
    print("%s\t%s" % (k, v))
    ROWS.append((k, v))


def die(msg):
    print("FAIL: %s" % msg)
    sys.exit(1)


rec("datashader", getattr(ds, "__version__", "unknown"))
rec("pandas", pd.__version__)
rec("numpy", np.__version__)
try:
    import numba
    rec("numba", getattr(numba, "__version__", "unknown"))
except Exception as exc:                                  # a version print must never be
    rec("numba", "unavailable (%s)" % type(exc).__name__)  # able to fail the run

SEED = 20261011
N = 50_000_000
RES = 1024
XR = YR = (0.0, 1.0)

# ---------------------------------------------------------- the orientation, from a fixture
# Asserted, not assumed: single points at known corners of a 4x4 canvas over [0,4]^2.
cvs4 = ds.Canvas(plot_width=4, plot_height=4, x_range=(0, 4), y_range=(0, 4))
CORNERS = {"lower_left": ((0.5, 0.5), (0, 0)),
           "upper_left": ((0.5, 3.5), (3, 0)),
           "lower_right": ((3.5, 0.5), (0, 3))}
for name, ((px, py), want) in CORNERS.items():
    a = cvs4.points(pd.DataFrame({"x": [px], "y": [py]}), "x", "y", ds.count())
    got = np.argwhere(np.asarray(a) == 1)
    if got.shape[0] != 1 or tuple(got[0]) != want:
        die("a point at %s landed at %s, expected index %s -- the orientation is not "
            "dims=('y','x') with y ascending" % ((px, py), got.tolist(), want))
    rec("orientation_%s" % name, "index %s" % (list(want),))
    if tuple(a.dims) != ("y", "x"):
        die("dims are %s, not ('y','x')" % (a.dims,))
rec("orientation", "dims=('y','x'), y ascending; numpy comparison uses the TRANSPOSE")

# ------------------------------------------------- the bin-edge rule, also from a fixture
edge_pts = np.array([0.0, 0.999, 1.0, 1.001, 3.999, 4.0, 4.001])
edf = pd.DataFrame({"x": edge_pts, "y": np.full(edge_pts.size, 0.5)})
ds_edge = np.asarray(cvs4.points(edf, "x", "y", ds.count())).sum(axis=0)
H_edge, _, _ = np.histogram2d(edf["x"], edf["y"], bins=[4, 4], range=[[0, 4], [0, 4]])
np_edge = H_edge.sum(axis=1)
rec("edge_rule_datashader", ",".join(str(int(v)) for v in ds_edge))
rec("edge_rule_histogram2d", ",".join(str(int(v)) for v in np_edge))
if not np.array_equal(ds_edge.astype("int64"), np_edge.astype("int64")):
    die("datashader bins the edge fixture as %s, histogram2d as %s -- the binning "
        "conventions differ, so any elementwise comparison would measure the convention "
        "and not the implementation" % (ds_edge.tolist(), np_edge.tolist()))

# --------------------------------------------------------------------- the point cloud
rng = np.random.default_rng(SEED)
x = rng.uniform(-1.0, 2.0, N)
y = rng.uniform(-1.0, 2.0, N)
w = np.ones(N)
df = pd.DataFrame({"x": x, "y": y, "w": w})
inside = int(((x >= XR[0]) & (x <= XR[1]) & (y >= YR[0]) & (y <= YR[1])).sum())
rec("points_total", "%d" % N)
rec("points_inside_canvas", "%d" % inside)
rec("points_outside_fraction", "%.4f" % (1.0 - inside / N))
if not 0.05 < inside / N < 0.5:
    die("%.1f%% of points are inside the canvas; the fixture is meant to put most of "
        "them OUTSIDE so the range is load-bearing" % (100.0 * inside / N))

cvs = ds.Canvas(plot_width=RES, plot_height=RES, x_range=XR, y_range=YR)

t0 = time.time()
a_cnt = cvs.points(df, "x", "y", ds.count())
t_cold = time.time() - t0
t0 = time.time()
a_cnt_warm = cvs.points(df, "x", "y", ds.count())
t_warm = time.time() - t0
rec("aggregate_cold_s", "%.3f" % t_cold)
rec("aggregate_warm_s", "%.3f" % t_warm)
rec("canvas", "%dx%d over [0,1]^2" % (RES, RES))

A = np.asarray(a_cnt).astype("int64")
if not np.array_equal(A, np.asarray(a_cnt_warm).astype("int64")):
    die("two identical aggregations of the same frame differ")

# ------------------------------------------- 1. count conservation: an integer identity
total = int(A.sum())
rec("raster_count_total", "%d" % total)
if total != inside:
    die("the raster holds %d points but numpy says %d fall inside the canvas (diff %d)"
        % (total, inside, total - inside))
rec("conservation_count", "raster total == numpy inside-count exactly")

# ------------------------------------------------ 2. sum, and mean == sum/count exactly
a_sum = cvs.points(df, "x", "y", ds.sum("w"))
S = np.asarray(a_sum)
if not np.all(np.isfinite(S[A > 0])):
    die("sum() produced a non-finite value in a non-empty cell")
s_total = float(np.nansum(S))
rec("raster_sum_total", "%.17g" % s_total)
if s_total != float(inside):
    die("sum('w') with w==1 totals %.17g, not the %d points inside" % (s_total, inside))

a_mean = cvs.points(df, "x", "y", ds.mean("w"))
M = np.asarray(a_mean)
nonempty = A > 0
resid = float(np.abs(M[nonempty] - S[nonempty] / A[nonempty]).max())
rec("mean_minus_sum_over_count", "%.3e" % resid)
if resid != 0.0:
    die("mean != sum/count on non-empty cells (max |diff| %.3e)" % resid)
rec("empty_cells", "%d" % int((~nonempty).sum()))
if int((~nonempty).sum()) == 0:
    die("every cell is occupied, so the empty-cell behaviour below is untested")
if not np.isnan(M[~nonempty]).all():
    die("mean() is not NaN in empty cells, so empty and zero are indistinguishable")
rec("empty_cell_mean", "NaN (distinguishable from a true zero)")

# ----------------- 3. the cross-check: an independent implementation of the same binning
H, _, _ = np.histogram2d(df["x"], df["y"], bins=[RES, RES],
                         range=[list(XR), list(YR)])
H = H.astype("int64")
if A.T.shape != H.shape:
    die("datashader gives %s and histogram2d %s; the transpose does not line up"
        % (A.shape, H.shape))
diff = int(np.abs(A.T - H).max())
ncells = int((A.T != H).sum())
rec("cells_compared", "%d" % H.size)
rec("vs_histogram2d_max_abs_diff", "%d" % diff)
rec("vs_histogram2d_cells_differing", "%d" % ncells)
if diff != 0 or ncells != 0:
    die("datashader and np.histogram2d disagree in %d of %d cells (max |diff| %d)"
        % (ncells, H.size, diff))
rec("cross_validation", "bit-identical to np.histogram2d over %d cells" % H.size)

# the control: a wrong orientation must be LOUDLY wrong, or the match proves nothing
for name, cand in (("as_is", A), ("flipud", np.flipud(A.T)), ("fliplr", np.fliplr(A.T))):
    if cand.shape != H.shape:
        rec("control_%s" % name, "shape %s != %s (rejected on shape)" % (cand.shape, H.shape))
        continue
    nd = int((cand != H).sum())
    rec("control_%s_cells_differing" % name, "%d" % nd)
    if nd == 0:
        die("orientation %r also matches histogram2d exactly, so the elementwise check "
            "cannot tell orientation apart" % name)

# ---------------------------------------------------- 4. invariances, both exact
# (a) the raster cannot depend on input row order
sub = df.iloc[: N // 10].reset_index(drop=True)
A_sub = np.asarray(cvs.points(sub, "x", "y", ds.count())).astype("int64")
perm = rng.permutation(len(sub))
A_shuf = np.asarray(cvs.points(sub.iloc[perm].reset_index(drop=True),
                               "x", "y", ds.count())).astype("int64")
same = bool(np.array_equal(A_sub, A_shuf))
rec("invariance_row_order", "identical" if same else "DIFFERS")
if not same:
    die("permuting %d input rows changed the raster" % len(sub))

# (b) the total cannot depend on canvas resolution -- only its distribution can
rec("invariance_resolution_base", "%d at %d" % (total, RES))
for res in (128, 256, 512):
    t = int(np.asarray(ds.Canvas(plot_width=res, plot_height=res,
                                 x_range=XR, y_range=YR)
                       .points(df, "x", "y", ds.count())).sum())
    rec("invariance_resolution_%d" % res, "%d" % t)
    if t != total:
        die("at %dx%d the raster holds %d points, at %dx%d it holds %d -- resolution "
            "must not change the total" % (res, res, t, RES, RES, total))
rec("invariance_resolution", "total unchanged from 128 to %d" % RES)

with open("score.tsv", "w") as fh:
    fh.write("observable\tvalue\n")
    for k, v in ROWS:
        fh.write("%s\t%s\n" % (k, v))

print("DATASHADER OK")
