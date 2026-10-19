#!/usr/bin/env python3
"""Fetch SIESTA 5.4.2's test suite on AWS and check every file against the release tree.

    python3 -u fetch.py manifest.tsv <out dir>

manifest.tsv lists the files under Tests/ (path, git blob sha1) that the runnable suite needs:
the 18 numbered test directories and Dependency_Tests (inputs, committed Reference/*.out, restart
files), Pseudos/, and the suite's own comparison tools (test-cfg.yml, out_digest_yaml.awk,
yaml_compare.py). Each is taken from one archive of Tests/ at the 5.4.2 tag's COMMIT, not the tag, and kept only if
sha1("blob <size>\\0" + bytes) equals the blob id in that commit's tree -- git's own content
address. Writes <out>/data.sha256 for pins.sha256.
"""
import hashlib
import io
import os
import sys
import tarfile
import time
import urllib.request

COMMIT = "0e2722c2c1fa8dfe8b768b376eeebb4c64db969d"   # siesta-project/siesta tag 5.4.2
# One archive of Tests/ at the commit (raw per-file fetches hit gitlab.com's rate limit). The archive's
# own bytes are not stable -- the forge regenerates it -- so it is never pinned; every file in it is.
URL = ("https://gitlab.com/api/v4/projects/siesta-project%%2Fsiesta/repository/archive.tar.gz"
       "?sha=%s&path=Tests" % COMMIT)

man, out = sys.argv[1], sys.argv[2]
rows = [l.rstrip("\n").split("\t") for l in open(man) if l.strip() and not l.startswith("#")]
data = None
for attempt in range(5):
    try:
        data = urllib.request.urlopen(URL, timeout=300).read()
        break
    except Exception as e:  # noqa: BLE001 -- report and retry a transient fetch error
        print("retry archive: %s" % e)
        time.sleep(30)
if data is None:
    sys.exit("FAIL: could not fetch the Tests/ archive")
files = {}
with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tf:
    for m in tf.getmembers():
        if m.isfile():
            files[m.name.split("/", 1)[1]] = tf.extractfile(m).read()   # drop the archive's top dir
print("archive: %d bytes, %d files" % (len(data), len(files)))
sums = []
for path, blob in rows:
    if path not in files:
        sys.exit("FAIL: %s is not in the archive" % path)
    b = files[path]
    got = hashlib.sha1(b"blob %d\0" % len(b) + b).hexdigest()
    if got != blob:
        sys.exit("FAIL: %s has blob %s; the 5.4.2 tree says %s" % (path, got, blob))
    rel = "tests/" + path[len("Tests/"):]
    dst = os.path.join(out, rel)
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    with open(dst, "wb") as fh:
        fh.write(b)
    sums.append("%s  %s" % (hashlib.sha256(b).hexdigest(), rel))
with open(os.path.join(out, "data.sha256"), "w") as fh:
    fh.write("\n".join(sums) + "\n")
print("kept %d files, every one equal to its blob in siesta 5.4.2" % len(sums))
