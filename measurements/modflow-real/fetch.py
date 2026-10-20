#!/usr/bin/env python3
"""Fetch the Mobile Bay MODFLOW 6 model archive (USGS data release, doi:10.5066/P1XKGCSZ), on AWS.

    python3 -u fetch.py <out dir>

Each file is ScienceBase's own attachment on item 674f7ffdd34e5a48687db29b, kept only if its MD5
equals the checksum ScienceBase publishes for it (hard-coded below, read from the item's JSON
record on 2026-10-20). Writes <out>/data.sha256 (which pins.sha256 must equal) and a listing of
every zip's entries (<name>.list: size, CRC-32, path) so the archive's layout is readable without
downloading it.
"""
import hashlib
import os
import sys
import time
import urllib.request
import zipfile

ITEM = "674f7ffdd34e5a48687db29b"
BASE = "https://www.sciencebase.gov/catalog/file/get/%s?f=__disk__" % ITEM
# name, ScienceBase path-on-disk, size, MD5 as ScienceBase publishes it
FILES = [
    ("model.zip", "cc%2F24%2Fe5%2Fcc24e596bd75eb158a2b13205408e2828e30b5f0", 48586751, "b00d1adf7f914c6379a926c2c2849c3e"),
    ("output.zip", "f3%2F51%2Fea%2Ff351ea873ce7628287ff8147e747b48119748a87", 323006652, "39cf46ba1d7e88ddc9a39279d316fd58"),
    ("mobile_bay_readme.txt", "74%2F97%2Ffa%2F7497fadbcd74d275c5a15bef2a800711286b0a4a", 8425, "9586a96537a36143460c11b5db38e08d"),
    ("mobile_bay_metadata.xml", "af%2F45%2F54%2Faf45540fcdc70dbc5c3b46150321eb37f8169d7c", 12833, "063a21d3129f5c317ecf95a9defc4ba4"),
]
UA = {"User-Agent": "scientific-codes-cookbook modflow-real"}

out = sys.argv[1]
os.makedirs(out, exist_ok=True)
sums = []
for name, disk, size, md5 in FILES:
    path = os.path.join(out, name)
    ok = False
    for attempt in range(3):
        t0 = time.time()
        try:
            h5, h256, n = hashlib.md5(), hashlib.sha256(), 0
            with urllib.request.urlopen(urllib.request.Request(BASE + disk, headers=UA), timeout=900) as r, open(path, "wb") as f:
                while True:
                    b = r.read(1 << 22)
                    if not b:
                        break
                    f.write(b); h5.update(b); h256.update(b); n += len(b)
        except Exception as e:  # noqa: BLE001 -- report and retry a transient fetch error
            print("retry %s: %s" % (name, e))
            time.sleep(10)
            continue
        print("%-24s %11d bytes in %5.1fs  md5 %s" % (name, n, time.time() - t0, h5.hexdigest()))
        if n == size and h5.hexdigest() == md5:
            ok = True
            break
        print("  mismatch: want %d bytes md5 %s" % (size, md5))
    if not ok:
        sys.exit("FAIL: %s does not match ScienceBase's published size and MD5" % name)
    sums.append("%s  archive/%s" % (h256.hexdigest(), name))
    if name.endswith(".zip"):
        with zipfile.ZipFile(path) as z, open(os.path.join(out, name[:-4] + ".list"), "w") as L:
            bad = z.testzip()
            if bad:
                sys.exit("FAIL: %s entry %s fails its CRC" % (name, bad))
            for i in z.infolist():
                L.write("%d\t%08x\t%s\n" % (i.file_size, i.CRC, i.filename))
        print("  %s: every entry's CRC-32 verified" % name)
with open(os.path.join(out, "data.sha256"), "w") as f:
    f.write("\n".join(sums) + "\n")
print(open(os.path.join(out, "data.sha256")).read())
print("FETCH OK")
