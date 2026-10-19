#!/usr/bin/env python3
"""Fetch QASMBench at commit 357b942 ON AWS and keep only files whose git blob id matches the
commit's own tree (qasmbench-357b942.gitblobs, from the GitHub tree API). A git blob id is
sha1("blob <size>\\0" + bytes), so this checks every byte against the commit itself; the sha256
manifest written alongside is what later tasks re-check.
    fetch.py <gitblobs manifest> <out dir>
"""
import hashlib
import io
import os
import sys
import tarfile
import urllib.request

COMMIT = "357b942396d5c2b7cbc1c229c585a6ef5ccaebac"
URL = "https://codeload.github.com/pnnl/QASMBench/tar.gz/" + COMMIT
manifest, out = sys.argv[1], sys.argv[2]
want = {}
for line in open(manifest):
    sha, size, path = line.rstrip("\n").split("\t")
    want[path] = (sha, int(size))
buf = io.BytesIO()
with urllib.request.urlopen(URL, timeout=900) as r:
    while True:
        b = r.read(1 << 20)
        if not b:
            break
        buf.write(b)
print("tarball bytes", buf.tell(), "sha256", hashlib.sha256(buf.getvalue()).hexdigest(), flush=True)
buf.seek(0)
got = {}
with tarfile.open(fileobj=buf, mode="r:gz") as tf:
    for m in tf:
        if not m.isfile():
            continue
        path = m.name.split("/", 1)[1]
        if path not in want:
            continue
        data = tf.extractfile(m).read()
        blob = hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()
        if (blob, len(data)) != want[path]:
            sys.exit("FAIL: %s blob %s size %d, the commit's tree says %s %d"
                     % (path, blob, len(data), want[path][0], want[path][1]))
        dst = os.path.join(out, path)
        os.makedirs(os.path.dirname(dst) or out, exist_ok=True)
        with open(dst, "wb") as fh:
            fh.write(data)
        got[path] = hashlib.sha256(data).hexdigest()
missing = sorted(set(want) - set(got))
if missing:
    sys.exit("FAIL: %d files missing from the tarball: %s" % (len(missing), missing[:5]))
with open(os.path.join(out, "qasmbench.sha256"), "w") as fh:
    for p in sorted(got):
        fh.write("%s  %s\n" % (got[p], p))
print("verified %d files against the commit's git blob ids, %d bytes"
      % (len(got), sum(s for _, s in want.values())), flush=True)
