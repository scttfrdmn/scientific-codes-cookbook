#!/usr/bin/env python3
"""Fetch SU2 8.5.0's NACA 0012 validation inputs on AWS and check each against its release tree.

    python3 -u fetch.py manifest.tsv <out dir>

manifest.tsv lists each file (repo, commit, path, git blob sha1, size). Each is fetched from
raw.githubusercontent.com at the v8.5.0 COMMIT of su2code/TestCases or su2code/SU2, and kept only
if sha1("blob <size>\\0" + bytes) equals the blob id in that tree -- the content address git itself
uses. Writes <out>/data.sha256, which must equal pins.sha256's data lines.
"""
import hashlib
import os
import sys
import time
import urllib.request

man, out = sys.argv[1], sys.argv[2]
rows = [l.rstrip("\n").split("\t") for l in open(man) if l.strip() and not l.startswith("#")]
sums = []
for repo, commit, path, blob, size in rows:
    url = "https://raw.githubusercontent.com/%s/%s/%s" % (repo, commit, path)
    data = None
    for attempt in range(3):
        try:
            data = urllib.request.urlopen(url, timeout=300).read()
            break
        except Exception as e:  # noqa: BLE001 -- report and retry a transient fetch error
            print("retry %s: %s" % (path, e))
            time.sleep(5)
    if data is None:
        sys.exit("FAIL: could not fetch %s" % url)
    got = hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()
    if len(data) != int(size) or got != blob:
        sys.exit("FAIL: %s is %d bytes, blob %s; the v8.5.0 tree says %s bytes, blob %s"
                 % (path, len(data), got, size, blob))
    name = os.path.basename(path)
    with open(os.path.join(out, name), "wb") as fh:
        fh.write(data)
    sums.append("%s  data/%s" % (hashlib.sha256(data).hexdigest(), name))
with open(os.path.join(out, "data.sha256"), "w") as fh:
    fh.write("\n".join(sums) + "\n")
print("\n".join(sums))
print("fetched %d files, every one equal to its blob at v8.5.0" % len(sums))
