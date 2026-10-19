#!/usr/bin/env python3
"""Fetch posteriordb 1.1.0's reference set on AWS and check every file against the release tree.

    python3 -u fetch.py manifest.tsv <out dir>

manifest.tsv lists the 256 files (path, git blob sha1, size) that the 57 posteriors with a
reference posterior need. Each is fetched from raw.githubusercontent.com at the release COMMIT,
not the tag, and kept only if sha1("blob <size>\\0" + bytes) equals the blob id in the release
tree, which is the content address git itself uses. Writes <out>/data.sha256 for pins.sha256.
"""
import hashlib
import os
import sys
import time
import urllib.request

COMMIT = "299b77cfbf813ba581620622b2826592512b5d98"   # posteriordb tag 1.1.0
BASE = "https://raw.githubusercontent.com/stan-dev/posteriordb/%s/posterior_database/" % COMMIT

man, out = sys.argv[1], sys.argv[2]
rows = [l.rstrip("\n").split("\t") for l in open(man) if l.strip() and not l.startswith("#")]
sums = []
for path, blob, size in rows:
    data = None
    for attempt in range(3):
        try:
            data = urllib.request.urlopen(BASE + path, timeout=120).read()
            break
        except Exception as e:  # noqa: BLE001 -- report and retry a transient fetch error
            print("retry %s: %s" % (path, e))
            time.sleep(5)
    if data is None:
        sys.exit("FAIL: could not fetch %s" % path)
    got = hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()
    if len(data) != int(size) or got != blob:
        sys.exit("FAIL: %s is %d bytes, blob %s; the release tree says %s bytes, blob %s"
                 % (path, len(data), got, size, blob))
    dst = os.path.join(out, path)
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    with open(dst, "wb") as fh:
        fh.write(data)
    sums.append("%s  pdb/%s" % (hashlib.sha256(data).hexdigest(), path))
with open(os.path.join(out, "data.sha256"), "w") as fh:
    fh.write("\n".join(sums) + "\n")
print("fetched %d files, every one equal to its blob in posteriordb 1.1.0" % len(sums))
