#!/usr/bin/env python3
"""Fetch the SuiteSparse Matrix Collection matrices for petsc-real, on AWS, and record their sha256.

    python3 -u fetch.py <out dir>

Each matrix is the collection's own Matrix Market archive, <group>/<name>.tar.gz, from the collection's
original University of Florida host (www.cise.ufl.edu/research/sparse/MM/). The Texas A&M file host
behind sparse.tamu.edu has answered 403 to every download since 2026-10-14 (the collection's notice:
offline because of excessive downloads from AWS jobs), so this fetches each archive once and the
bucket copy, pinned by sha256, is what every run reads.
The archive is kept only if its .mtx header has the collection's published size and entry count
(the matrix page's Num Rows and Nonzeros). Writes <out>/data.sha256, which pins.sha256 must equal.
"""
import hashlib
import io
import os
import sys
import tarfile
import time
import urllib.request

# group/name, collection id, rows, nonzeros (full matrix, as the collection publishes them)
MATRICES = [
    ("Wissgott/parabolic_fem", 1853, 525825, 3674625),
    ("GHS_psdef/apache2", 1423, 715176, 4817870),
    ("McRae/ecology2", 1883, 999999, 4995991),
    ("Schmid/thermal2", 1403, 1228045, 8580313),
    ("AMD/G3_circuit", 1421, 1585478, 7660826),
    ("GHS_psdef/audikw_1", 1252, 943695, 77651847),
]
UA = {"User-Agent": "Mozilla/5.0 (X11; Linux) scientific-codes-cookbook petsc-real"}

out = sys.argv[1]
os.makedirs(out, exist_ok=True)
sums = []
for path, mid, rows, nnz in MATRICES:
    url = "https://www.cise.ufl.edu/research/sparse/MM/%s.tar.gz" % path
    data = None
    for attempt in range(3):
        try:
            resp = urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=600)
            data = resp.read()
            print("%s Last-Modified %s" % (url, resp.headers.get("Last-Modified")))
            break
        except Exception as e:  # noqa: BLE001 -- report and retry a transient fetch error
            print("retry %s: %s" % (url, e))
            time.sleep(10)
    if data is None:
        sys.exit("FAIL: could not fetch %s" % url)
    name = path.split("/")[1]
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tf:
        mtx = tf.extractfile("%s/%s.mtx" % (name, name))
        banner = mtx.readline().decode().split()
        line = mtx.readline().decode()
        while line.startswith("%"):
            line = mtx.readline().decode()
        r, c, ent = (int(v) for v in line.split())
    sym = banner[4]
    full = 2 * ent - r if sym == "symmetric" else ent   # every symmetric matrix here has a full diagonal
    print("%s id %d: %s, %d x %d, %d stored entries (%s), %d bytes" % (path, mid, " ".join(banner), r, c, ent, sym, len(data)))
    if r != rows or c != rows or sym != "symmetric":
        sys.exit("FAIL: %s is %d x %d %s; the collection publishes %d x %d symmetric" % (path, r, c, sym, rows, rows))
    with open(os.path.join(out, name + ".tar.gz"), "wb") as fh:
        fh.write(data)
    sums.append("%s  mm/%s.tar.gz" % (hashlib.sha256(data).hexdigest(), name))
    print("  stored entries %d -> full nonzeros %d if the diagonal is full; published %d" % (ent, full, nnz))
with open(os.path.join(out, "data.sha256"), "w") as fh:
    fh.write("\n".join(sums) + "\n")
print("fetched %d matrices" % len(sums))
