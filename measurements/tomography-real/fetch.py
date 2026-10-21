#!/usr/bin/env python3
"""Fetch the BBii sandstone scan (SLS TOMCAT, 2013) from Zenodo, on AWS.

    python3 -u fetch.py <out dir>

Zenodo record 4912435 v1.0 (doi:10.5281/zenodo.4912435, CC BY 4.0): proj.zip holds every raw
TIFF of the scan (30 darks, 100 flats, 1501 projections over 0-180 deg inclusive, 100 flats) and
rec.zip every slice of the depositors' own GridRec reconstruction. Each file is kept only if its
size and MD5 equal what Zenodo publishes (api/records/4912435, read 2026-10-20). Writes
<out>/data.sha256, which pins.sha256 must contain. A dropped connection resumes with a Range
request rather than restarting.
"""
import hashlib
import os
import sys
import threading
import time
import urllib.request

REC = "https://zenodo.org/api/records/4912435/files/%s/content"
FILES = {"proj.zip": (17104540917, "026ca4569ece6cf9f0e9675a2930c0a1"),
         "rec.zip": (25257818414, "cb5ab7c979c227a8a72d091adb3061dd")}
UA = "scientific-codes-cookbook tomography-real"
out = sys.argv[1]
os.makedirs(out, exist_ok=True)
RESULT = {}


def fetch(name):
    size, md5 = FILES[name]
    path = os.path.join(out, name)
    t0 = time.time()
    n = 0
    with open(path, "wb") as f:
        for attempt in range(20):
            if n >= size:
                break
            req = urllib.request.Request(REC % name, headers={"User-Agent": UA, "Range": "bytes=%d-" % n})
            try:
                with urllib.request.urlopen(req, timeout=300) as r:
                    while True:
                        b = r.read(1 << 22)
                        if not b:
                            break
                        f.write(b)
                        n += len(b)
            except Exception as e:  # noqa: BLE001 -- resume a transient error from where it stopped
                print("%s: resume %d at %d bytes: %s" % (name, attempt, n, e), flush=True)
                time.sleep(15)
    dt = time.time() - t0
    h5, h256 = hashlib.md5(), hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            b = f.read(1 << 24)
            if not b:
                break
            h5.update(b)
            h256.update(b)
    ok = n == size and h5.hexdigest() == md5
    print("%s %d bytes in %.0f s (%.0f MB/s) md5 %s %s" % (name, n, dt, n / dt / 1e6, h5.hexdigest(),
                                                           "OK" if ok else "MISMATCH"), flush=True)
    RESULT[name] = (ok, h256.hexdigest())


ts = [threading.Thread(target=fetch, args=(k,)) for k in FILES]
for t in ts:
    t.start()
for t in ts:
    t.join()
if not all(RESULT.get(k, (False,))[0] for k in FILES):
    sys.exit("FAIL: a file does not match Zenodo's published size and MD5")
with open(os.path.join(out, "data.sha256"), "w") as fh:
    for k in sorted(FILES):
        fh.write("%s  %s\n" % (RESULT[k][1], k))
print(open(os.path.join(out, "data.sha256")).read(), end="")
print("FETCH OK")
