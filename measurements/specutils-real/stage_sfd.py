#!/usr/bin/env python3
"""Fetch the SFD (1998) E(B-V) maps that DESI's emlinefit reads, on AWS, pinned by git blob id.

The maps come from github.com/kbarbary/sfddata at commit 7a5fe7fa (2016; the repo desiutil's SFD
reader is "taken in full from"). A git blob id is the SHA-1 of the file's bytes, so checking it
proves these are the bytes that commit holds. The sha256 printed here is what stage-inputs.sh and
the fit task then check. Run by stage-sfd.task.json; nothing passes through a laptop.
"""
import hashlib
import os
import sys
import urllib.request

COMMIT = "7a5fe7fadf086561ba4748756e59a4c51d0ec632"
BLOBS = {"SFD_dust_4096_ngp.fits": "df94534e1e2b92ff308295ee364d4682a23e64ae",
         "SFD_dust_4096_sgp.fits": "d783f46849d793b479b4962135c7ff520115d914"}
out = sys.argv[1]
os.makedirs(out, exist_ok=True)
lines = []
for name, blob in BLOBS.items():
    url = "https://raw.githubusercontent.com/kbarbary/sfddata/%s/%s" % (COMMIT, name)
    data = urllib.request.urlopen(url, timeout=300).read()
    got = hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()
    if got != blob:
        sys.exit("FAIL: %s git blob %s, pinned %s" % (name, got, blob))
    with open(os.path.join(out, name), "wb") as fh:
        fh.write(data)
    lines.append("%s  %s" % (hashlib.sha256(data).hexdigest(), name))
    print("ok", name, len(data), "bytes, blob", got, flush=True)
open(os.path.join(out, "sfd.sha256"), "w").write("\n".join(lines) + "\n")
print("\n".join(lines))
print("SFD STAGED")
