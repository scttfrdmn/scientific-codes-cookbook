#!/usr/bin/env python3
"""Convert the LHCO 2020 R&D HDF5 into flat numpy arrays the `hep` env can read, losslessly.

    python3 -u convert.py <events.h5> <features.h5> <outdir>

The `hep` env has no HDF5 reader for Python (no h5py, no pytables), so this one step runs in an
env that has pytables (aarch.science `md`). It writes, for all events in file order:

    lhco-counts.i32    particles per event (int32)
    lhco-pep.<dt>      (pt, eta, phi) of every particle, zero padding dropped, source dtype
    lhco-label.i8      the truth label (0 QCD, 1 signal)
    lhco-features.f8   the organisers' published jet features, row-aligned; columns in .columns
    lhco-meta.json     shapes, dtypes and the checks below

LOSSLESS, asserted per chunk: every row re-padded from the compact arrays equals the source row
exactly (np.array_equal), so the conversion drops nothing but zeros. The padding is checked to be
trailing (no zero-pt particle before the last real one), and the features file's index to equal the
events file's index row for row.
"""
import json
import sys
import time

import numpy as np
import pandas as pd
import tables

EV, FE, OUT = sys.argv[1], sys.argv[2], sys.argv[3]


def die(msg):
    print("FAIL: %s" % msg, flush=True)
    sys.exit(1)


for path in (EV, FE):
    with tables.open_file(path) as h:
        print("== %s" % path)
        for node in h.walk_nodes("/"):
            extra = ""
            if isinstance(node, tables.Leaf):
                extra = " shape=%s dtype=%s filters=%s" % (node.shape, node.dtype, node.filters)
            attrs = {k: node._v_attrs[k] for k in node._v_attrs._f_list("user")}
            print("  %s %s%s %s" % (node._v_pathname, type(node).__name__, extra,
                                    {k: str(v)[:80] for k, v in attrs.items()}), flush=True)

# The events file is pandas "fixed" format written by Python 2 (its attributes are bytes), which
# pandas 3 cannot open, so it is read through pytables directly: /df/block0_values is the
# (events, 2101) value block, /df/axis1 the row index, /df/block0_items the column labels.
EVH = tables.open_file(EV)
blk = EVH.root.df.block0_values
nrows = blk.shape[0]
items = EVH.root.df.block0_items[:]
axis0 = EVH.root.df.axis0[:]
if not (np.array_equal(items, axis0) and np.array_equal(items, np.arange(blk.shape[1]))):
    die("the value block's columns are not 0..%d in order" % (blk.shape[1] - 1))
print("events rows", nrows, "columns", blk.shape[1], "dtype", blk.dtype, flush=True)
cols = list(items)
if len(cols) % 3 != 1:
    die("%d columns is not 3 x particles + label" % len(cols))
NP = (len(cols) - 1) // 3
dt = blk.dtype
print(np.array2string(blk[0:3, :9], precision=6), flush=True)
print("particles per row %d, value dtype %s" % (NP, dt), flush=True)
EVINDEX = EVH.root.df.axis1[:]

counts = np.zeros(nrows, dtype=np.int32)
label = np.zeros(nrows, dtype=np.int8)
index = np.zeros(nrows, dtype=np.int64)
ext = {np.dtype("float64"): "f8", np.dtype("float32"): "f4"}.get(np.dtype(dt))
if ext is None:
    die("unexpected value dtype %s" % dt)
pep_path = "%s/lhco-pep.%s" % (OUT, ext)
fh = open(pep_path, "wb")
CH = 50000
t0 = time.time()
ntot = 0
for lo in range(0, nrows, CH):
    v = blk[lo:min(nrows, lo + CH)]
    if v.dtype != dt:
        die("chunk at %d has dtype %s" % (lo, v.dtype))
    n = v.shape[0]
    index[lo:lo + n] = EVINDEX[lo:lo + n]
    lab = v[:, -1]
    if not np.all((lab == 0) | (lab == 1)):
        die("a label outside {0,1} in the chunk at %d" % lo)
    label[lo:lo + n] = lab.astype(np.int8)
    p = v[:, :-1].reshape(n, NP, 3)
    if not np.all(np.isfinite(p)):
        die("a non-finite value in the chunk at %d" % lo)
    real = p[:, :, 0] > 0
    k = real.sum(axis=1)
    # trailing padding: the first k slots are real, everything after them is exactly zero
    pos = np.arange(NP)[None, :]
    if not np.array_equal(real, pos < k[:, None]):
        die("a zero-pt particle before the last real one in the chunk at %d" % lo)
    if np.any(p[~real] != 0):
        die("padding that is not exactly zero in the chunk at %d" % lo)
    comp = p[real]                                   # row-major: event order, then slot order
    # LOSSLESS: re-pad and compare to the source row exactly
    back = np.zeros_like(p)
    back[real] = comp
    if not np.array_equal(back.reshape(n, -1), v[:, :-1]):
        die("the compact form does not reproduce the chunk at %d" % lo)
    counts[lo:lo + n] = k
    fh.write(np.ascontiguousarray(comp).tobytes())
    ntot += comp.shape[0]
    print("rows %d-%d: %d particles, max %d per event, %.0f s" % (lo, lo + n, comp.shape[0], k.max(),
                                                               time.time() - t0), flush=True)
fh.close()
if counts.sum() != ntot:
    die("counts sum %d != %d particles written" % (counts.sum(), ntot))
counts.tofile("%s/lhco-counts.i32" % OUT)
label.tofile("%s/lhco-label.i8" % OUT)

fe = pd.read_hdf(FE)
print("features", fe.shape, list(fe.columns), flush=True)
print(fe.head(3).to_string())
if fe.shape[0] != nrows:
    die("features has %d rows, events %d" % (fe.shape[0], nrows))
if not np.array_equal(fe.index.values, index):
    die("the features index is not the events index row for row")
fv = fe.values.astype(np.float64)
if not np.array_equal(fv, fe.values):
    die("features did not survive the cast to float64")
fv.tofile("%s/lhco-features.f8" % OUT)
with open("%s/lhco-features.columns" % OUT, "w") as f:
    f.write("\n".join(map(str, fe.columns)) + "\n")
feat_dt = {str(k): str(v) for k, v in fe.dtypes.items()}

meta = {"events": int(nrows), "particles": int(ntot), "max_per_event": int(counts.max()),
        "slots_per_row": NP, "value_dtype": str(dt), "pep_file": pep_path.split("/")[-1],
        "label_counts": {"0": int((label == 0).sum()), "1": int((label == 1).sum())},
        "index_is_range": bool(np.array_equal(index, np.arange(nrows))),
        "features_columns": [str(c) for c in fe.columns], "features_dtypes": feat_dt,
        "lossless": "every chunk re-padded == source, exactly"}
with open("%s/lhco-meta.json" % OUT, "w") as f:
    json.dump(meta, f, indent=1)
print(json.dumps(meta, indent=1))
print("CONVERT OK")
