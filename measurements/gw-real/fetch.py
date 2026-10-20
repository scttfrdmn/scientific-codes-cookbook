#!/usr/bin/env python3
"""Fetch GW150914's public inputs ON AWS: the GWOSC O1 strain around the event (H1, L1; the
4096 s files at 4 kHz and 16 kHz) and the GWTC-2.1 PE data release for the event (Zenodo
10.5281/zenodo.6513631, the publisher's md5 checked). Writes inputs.sha256 beside them.
    fetch.py <out dir>
"""
import hashlib
import os
import sys
import urllib.request

OUT = sys.argv[1]
GWOSC = "https://gwosc.org/archive/data/"
FILES = [  # (url, publisher md5 or None)
    (GWOSC + "O1/1126170624/H-H1_LOSC_4_V1-1126256640-4096.hdf5", None),
    (GWOSC + "O1/1126170624/L-L1_LOSC_4_V1-1126256640-4096.hdf5", None),
    (GWOSC + "O1_16KHZ/1126170624/H-H1_LOSC_16_V1-1126256640-4096.hdf5", None),
    (GWOSC + "O1_16KHZ/1126170624/L-L1_LOSC_16_V1-1126256640-4096.hdf5", None),
    ("https://zenodo.org/api/records/6513631/files/"
     "IGWN-GWTC2p1-v2-GW150914_095045_PEDataRelease_mixed_nocosmo.h5/content",
     "f0e0436739c6f667d4331b275de17e94"),
]
os.makedirs(OUT, exist_ok=True)
rows = []
for url, md5 in FILES:
    parts = url.rstrip("/").split("/")
    name = parts[-2] if url.endswith("/content") else parts[-1]
    dst = os.path.join(OUT, name)
    for attempt in (1, 2):
        try:
            h256, h5, n = hashlib.sha256(), hashlib.md5(), 0
            with urllib.request.urlopen(url, timeout=900) as r, open(dst, "wb") as fh:
                while True:
                    b = r.read(1 << 22)
                    if not b:
                        break
                    fh.write(b)
                    h256.update(b)
                    h5.update(b)
                    n += len(b)
            break
        except Exception as e:
            print("retry %s: %s" % (name, e), flush=True)
            if attempt == 2:
                sys.exit("FAIL: could not fetch %s" % url)
    if md5 and h5.hexdigest() != md5:
        sys.exit("FAIL: %s md5 %s, Zenodo says %s" % (name, h5.hexdigest(), md5))
    print("%s\t%d bytes\tmd5 %s%s" % (name, n, h5.hexdigest(), " (== Zenodo)" if md5 else ""),
          flush=True)
    rows.append("%s  %s\n" % (h256.hexdigest(), name))
with open(os.path.join(OUT, "inputs.sha256"), "w") as fh:
    fh.writelines(sorted(rows, key=lambda r: r.split()[1]))
print("FETCH OK")
