#!/usr/bin/env python3
"""Fetch scVelo's mouse gastrulation atlas (Pijuan-Sala et al. 2019) from figshare, on AWS.

    python3 -u fetch.py <out dir>

The file is figshare article 14627496 v1 (doi:10.6084/m9.figshare.14627496.v1, CC BY 4.0, posted
by scVelo's author), file id 28095525 -- the URL `scvelo.datasets.gastrulation()` downloads. It is
kept only if its size and MD5 equal what figshare publishes for that file (read from
api.figshare.com/v2/articles/14627496 on 2026-10-20). Writes <out>/data.sha256, which pins.sha256
must equal.
"""
import hashlib
import os
import sys
import time
import urllib.request

URL = "https://ndownloader.figshare.com/files/28095525"
NAME, SIZE, MD5 = "gastrulation.h5ad", 4690839537, "aa364e67bb8d77fb0fcd0c1aa54ce20d"
UA = {"User-Agent": "scientific-codes-cookbook scvelo-real"}

out = sys.argv[1]
os.makedirs(out, exist_ok=True)
path = os.path.join(out, NAME)
for attempt in range(3):
    t0 = time.time()
    h5, h256, n = hashlib.md5(), hashlib.sha256(), 0
    try:
        with urllib.request.urlopen(urllib.request.Request(URL, headers=UA), timeout=900) as r, open(path, "wb") as f:
            while True:
                b = r.read(1 << 22)
                if not b:
                    break
                f.write(b)
                h5.update(b)
                h256.update(b)
                n += len(b)
    except Exception as e:  # noqa: BLE001 -- report and retry a transient fetch error
        print("retry %d: %s" % (attempt, e))
        time.sleep(10)
        continue
    dt = time.time() - t0
    print("%s %d bytes in %.1f s (%.0f MB/s) md5 %s" % (NAME, n, dt, n / dt / 1e6, h5.hexdigest()))
    if n == SIZE and h5.hexdigest() == MD5:
        with open(os.path.join(out, "data.sha256"), "w") as fh:
            fh.write("%s  %s\n" % (h256.hexdigest(), NAME))
        print("sha256 %s" % h256.hexdigest())
        print("FETCH OK")
        sys.exit(0)
    print("  mismatch: want %d bytes md5 %s" % (SIZE, MD5))
sys.exit("FAIL: %s does not match figshare's published size and MD5" % NAME)
