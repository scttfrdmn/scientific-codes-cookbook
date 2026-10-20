#!/usr/bin/env python3
"""Fetch the AlphaFold DB v6 human proteome archive from EMBL-EBI, on AWS.

    python3 -u fetch.py <out dir>

The archive is UP000005640_9606_HUMAN_v6.tar, the versioned v6 release directory on
ftp.ebi.ac.uk (CC BY 4.0). It is kept only if its size equals the size_bytes EBI publishes in
download_metadata.json and it holds exactly the published num_predicted_structures (23,586) mmCIF
models. EBI publishes no checksum, so this writes <out>/data.sha256 (which pins.sha256 must equal)
and <out>/members.tsv (every member's name and size) on first fetch.
"""
import concurrent.futures
import hashlib
import os
import sys
import tarfile
import time
import urllib.request

URL = "https://ftp.ebi.ac.uk/pub/databases/alphafold/v6/UP000005640_9606_HUMAN_v6.tar"
NAME, SIZE, NMODELS = "UP000005640_9606_HUMAN_v6.tar", 5177506304, 23586
UA = {"User-Agent": "scientific-codes-cookbook foldseek-real"}

out = sys.argv[1]
os.makedirs(out, exist_ok=True)
path = os.path.join(out, NAME)
# One HTTPS stream from EBI runs at ~1 MB/s (measured), so the archive comes down as 64 MiB byte
# ranges over 32 connections, each retried on its own; the sha256 is taken over the assembled file.
PART, NCONN = 64 << 20, 32


def get(rng):
    a, b = rng
    for attempt in range(5):
        try:
            req = urllib.request.Request(URL, headers=dict(UA, Range="bytes=%d-%d" % (a, b)))
            with urllib.request.urlopen(req, timeout=300) as r:
                data = r.read()
            if len(data) == b - a + 1:
                with open(path, "r+b") as f:
                    f.seek(a)
                    f.write(data)
                return len(data)
            print("short range %d-%d: %d bytes" % (a, b, len(data)))
        except Exception as e:  # noqa: BLE001 -- report and retry a transient fetch error
            print("retry %d-%d (%d): %s" % (a, b, attempt, e))
        time.sleep(5)
    raise SystemExit("FAIL: range %d-%d" % (a, b))


with urllib.request.urlopen(urllib.request.Request(URL, headers=UA, method="HEAD"), timeout=60) as r:
    print("Last-Modified", r.headers.get("Last-Modified"), "ETag", r.headers.get("ETag"),
          "Content-Length", r.headers.get("Content-Length"))
    if int(r.headers.get("Content-Length")) != SIZE:
        sys.exit("FAIL: %s is not EBI's published size %d" % (NAME, SIZE))
with open(path, "wb") as f:
    f.truncate(SIZE)
t0 = time.time()
ranges = [(a, min(a + PART, SIZE) - 1) for a in range(0, SIZE, PART)]
done = 0
with concurrent.futures.ThreadPoolExecutor(NCONN) as ex:
    for i, n in enumerate(ex.map(get, ranges)):
        done += n
        if i % 10 == 0:
            print("  %d / %d MB, %.0f s" % (done >> 20, SIZE >> 20, time.time() - t0))
dt = time.time() - t0
print("%s %d bytes in %.1f s (%.1f MB/s)" % (NAME, done, dt, done / dt / 1e6))
if done != SIZE or os.path.getsize(path) != SIZE:
    sys.exit("FAIL: %s does not match EBI's published size" % NAME)
h256 = hashlib.sha256()
with open(path, "rb") as f:
    for b in iter(lambda: f.read(1 << 24), b""):
        h256.update(b)

ncif, kinds = 0, {}
with tarfile.open(path) as t, open(os.path.join(out, "members.tsv"), "w") as m:
    for e in t:
        m.write("%s\t%d\n" % (e.name, e.size))
        k = e.name.split("-")[-1] if "-" in e.name else e.name
        kinds[k] = kinds.get(k, 0) + 1
        if e.name.endswith(".cif.gz"):
            ncif += 1
print("member kinds:", sorted(kinds.items()))
print("mmCIF models", ncif)
if ncif != NMODELS:
    sys.exit("FAIL: %d mmCIF models, EBI publishes %d" % (ncif, NMODELS))
with open(os.path.join(out, "data.sha256"), "w") as fh:
    fh.write("%s  %s\n" % (h256.hexdigest(), NAME))
print("sha256 %s" % h256.hexdigest())
print("FETCH OK")
