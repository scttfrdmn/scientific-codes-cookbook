#!/usr/bin/env python3
"""Fetch the LHC Olympics 2020 R&D files from Zenodo into /tmp/out, on AWS, checked against the record.

    python3 -u fetch.py

Record: "R&D Dataset for LHC Olympics 2020 Anomaly Detection Challenge", Zenodo 6466204 (v5),
DOI 10.5281/zenodo.6466204. v5 is the version whose raw events file (events_anomalydetection_v2.h5)
is the one the features file describes; v1-v4 carry an older raw file that it does not. Zenodo
publishes each file's size and md5; both are checked over the downloaded bytes, and the sha256 that
pins.sha256 carries is written beside each file.

Zenodo ignores Range (HTTP 200 to a ranged request), so each file is one stream; a short read
restarts the file rather than being taken as the end.
"""
import hashlib
import os
import sys
import time
import urllib.request

REC = "https://zenodo.org/records/6466204/files/%s?download=1"
FILES = {
    "events_anomalydetection_v2.h5": (2908260409, "629789d55813be3860781b084ae7f1de"),
    "events_anomalydetection_v2.features.h5": (74315238, "271cf5e71fc756b2a8d2b32730689bdb"),
}
ATTEMPTS = 5
os.makedirs("/tmp/out", exist_ok=True)

for name, (size, md5) in FILES.items():
    dest = "/tmp/out/" + name
    for attempt in range(1, ATTEMPTS + 1):
        t0 = time.time()
        h5, h256, n = hashlib.md5(), hashlib.sha256(), 0
        try:
            with urllib.request.urlopen(REC % name, timeout=120) as r, open(dest, "wb") as fh:
                while True:
                    b = r.read(8 << 20)
                    if not b:
                        break
                    fh.write(b)
                    h5.update(b)
                    h256.update(b)
                    n += len(b)
        except OSError as e:
            print("%s: attempt %d: %s at byte %d" % (name, attempt, type(e).__name__, n), flush=True)
            time.sleep(20)
            continue
        dt = time.time() - t0
        print("%s: %d bytes in %.1f s (%.1f MB/s), md5 %s" % (name, n, dt, n / dt / 1e6, h5.hexdigest()),
              flush=True)
        if n == size and h5.hexdigest() == md5:
            break
        print("%s: attempt %d: size/md5 mismatch (record says %d / %s)" % (name, attempt, size, md5),
              flush=True)
        time.sleep(20)
    else:
        if os.path.exists(dest):
            os.remove(dest)
        sys.exit("FAIL: %s does not match Zenodo record 6466204 after %d attempts" % (name, ATTEMPTS))
    with open(dest + ".sha256", "w") as fh:
        fh.write("%s  %s\n" % (h256.hexdigest(), name))
    print("%s: sha256 %s" % (name, h256.hexdigest()), flush=True)
print("FETCH OK")
