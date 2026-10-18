#!/usr/bin/env python3
"""Fetch the CMS Open Data record 12341 file into /tmp/out, on AWS, checked against the record.

    python3 -u fetch.py

Record: "DoubleMuParked dataset from 2012 in NanoAOD format reduced on muons",
DOI 10.7483/OPENDATA.CMS.LVG5.QT81. The record publishes the size and an adler32; both are
checked over the assembled file, and the sha256 that pins.sha256 carries is written beside it.

The file is fetched as 32 byte ranges in parallel, each resumed where it stopped: a single
stream from opendata.cern.ch to us-west-2 ran at 0.5 MB/s and ended early at 465 MB without an
error, so a short read is treated as a resume point, never as the end.
"""
import hashlib
import os
import sys
import time
import urllib.request
import zlib
from concurrent.futures import ThreadPoolExecutor

NAME = "Run2012BC_DoubleMuParked_Muons.root"
# the record's own /record/12341/files/ link ignores Range (HTTP 200); its EOS path serves 206
URL = "https://opendata.cern.ch/eos/opendata/cms/derived-data/AOD2NanoAODOutreachTool/" + NAME
SIZE = 2244449133
ADLER32 = "1fa61aca"
PARTS, ATTEMPTS = 32, 40

os.makedirs("/tmp/out", exist_ok=True)
dest = "/tmp/out/" + NAME
fd = os.open(dest, os.O_RDWR | os.O_CREAT | os.O_TRUNC, 0o644)
os.ftruncate(fd, SIZE)
step = -(-SIZE // PARTS)


def part(i):
    lo, hi = i * step, min(SIZE, (i + 1) * step) - 1
    pos, tries = lo, 0
    while pos <= hi:
        tries += 1
        if tries > ATTEMPTS:
            raise RuntimeError("range %d: gave up at byte %d of %d-%d" % (i, pos, lo, hi))
        try:
            req = urllib.request.Request(URL, headers={"Range": "bytes=%d-%d" % (pos, hi)})
            with urllib.request.urlopen(req, timeout=60) as r:
                if r.status != 206:
                    raise RuntimeError("range %d: HTTP %d, not 206" % (i, r.status))
                while pos <= hi:
                    b = r.read(1 << 20)
                    if not b:
                        break
                    os.pwrite(fd, b, pos)
                    pos += len(b)
        except (OSError, ValueError) as e:
            print("range %d: %s at byte %d, resuming" % (i, type(e).__name__, pos), flush=True)
            time.sleep(2)
    return tries


t0 = time.time()
try:
    with ThreadPoolExecutor(PARTS) as ex:
        tries = list(ex.map(part, range(PARTS)))
except BaseException:
    os.close(fd)
    os.remove(dest)       # never stage out a half-filled file
    raise
os.close(fd)
dt = time.time() - t0
print("bytes %d in %.1f s (%.1f MB/s), %d requests for %d ranges"
      % (SIZE, dt, SIZE / dt / 1e6, sum(tries), PARTS), flush=True)

sha, adl, n = hashlib.sha256(), 1, 0
with open(dest, "rb") as fh:
    while True:
        b = fh.read(8 << 20)
        if not b:
            break
        sha.update(b)
        adl = zlib.adler32(b, adl)
        n += len(b)
got = "%08x" % (adl & 0xFFFFFFFF)
print("size %d (record says %d), adler32 %s (record says %s)" % (n, SIZE, got, ADLER32))
if n != SIZE or got != ADLER32:
    os.remove(dest)
    sys.exit("FAIL: the download does not match CERN Open Data record 12341")
with open("/tmp/out/%s.sha256" % NAME, "w") as fh:
    fh.write("%s  %s\n" % (sha.hexdigest(), NAME))
print("sha256 %s" % sha.hexdigest())
print("FETCH OK")
