#!/usr/bin/env python3
"""yoda-real 00-fetch: Rivet 4.1.4's analyses/ tree -- every reference .yoda.gz Rivet ships.

    python3 -u fetch.py <outdir>

Source: GitLab's archive of hepcedar/rivet at the commit the rivet-4.1.4 tag names
(74816bd4cf29a9d71447f8c122887d498d3dea3a), restricted to analyses/. The commit is immutable;
the archive's gzip bytes are GitLab's to regenerate, so the tarball is pinned by its sha256 AND
by a manifest of every extracted reference file's own sha256, which depends only on the commit.
Runs on AWS; the spec's outputs write it to the bucket.
"""
import hashlib
import io
import os
import sys
import tarfile
import urllib.request

COMMIT = "74816bd4cf29a9d71447f8c122887d498d3dea3a"
URL = ("https://gitlab.com/hepcedar/rivet/-/archive/%s/rivet-%s.tar.gz?path=analyses"
       % (COMMIT, COMMIT))
OUT = sys.argv[1]
NAME = "rivet-4.1.4-analyses.tar.gz"
os.makedirs(OUT, exist_ok=True)

req = urllib.request.Request(URL, headers={"User-Agent": "scientific-codes-cookbook/yoda-real"})
blob = urllib.request.urlopen(req, timeout=600).read()
h = hashlib.sha256(blob).hexdigest()
with open(os.path.join(OUT, NAME), "wb") as fh:
    fh.write(blob)
with open(os.path.join(OUT, NAME + ".sha256"), "w") as fh:
    fh.write("%s  %s\n" % (h, NAME))
print("tarball %s bytes %d sha256 %s" % (NAME, len(blob), h))

refs = []
with tarfile.open(fileobj=io.BytesIO(blob), mode="r:gz") as t:
    for m in t.getmembers():
        if m.isfile() and m.name.endswith(".yoda.gz") and "/analyses/" in m.name:
            data = t.extractfile(m).read()
            refs.append((hashlib.sha256(data).hexdigest(), "analyses/" + m.name.split("/analyses/", 1)[1], len(data)))
refs.sort(key=lambda r: r[1])
with open(os.path.join(OUT, "refs.manifest"), "w") as fh:
    for d, n, _ in refs:
        fh.write("%s  %s\n" % (d, n))
with open(os.path.join(OUT, "refs.manifest"), "rb") as fh:
    man = hashlib.sha256(fh.read()).hexdigest()
print("reference files %d, %d bytes compressed; refs.manifest sha256 %s"
      % (len(refs), sum(r[2] for r in refs), man))
if len(refs) < 500:
    print("FAIL: expected Rivet's full reference set, found %d files" % len(refs))
    sys.exit(1)
print("FETCH OK")
