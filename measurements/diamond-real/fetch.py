#!/usr/bin/env python3
"""Fetch UniProt release 2026_03's UniRef50, Swiss-Prot and human id mapping into /tmp/out, on AWS.

    python3 -u fetch.py

UniProt publishes each file's size and md5 in the RELEASE.metalink beside it; both are checked over
the downloaded bytes, and the sha256 that pins.sha256 carries is written beside each file. The
release notes are fetched too and must name 2026_03: the URLs are UniProt's current_release paths,
so after the next release they serve different bytes, and this fails instead of fetching them.
"""
import hashlib
import os
import sys
import time
import urllib.request

RELEASE = "2026_03"
MIRRORS = ["https://ftp.uniprot.org/pub/databases/uniprot/current_release/",
           "https://ftp.ebi.ac.uk/pub/databases/uniprot/current_release/"]
FILES = {  # name: (path under current_release, size, md5 from that directory's RELEASE.metalink)
    "uniref50.fasta.gz": ("uniref/uniref50/uniref50.fasta.gz", 8780552383,
                          "0492e3cf4093276ae4319ba24d52514f"),
    "uniprot_sprot.fasta.gz": ("knowledgebase/complete/uniprot_sprot.fasta.gz", 93801562,
                               "bc9d398533e6df582b563c6c03093bd0"),
    "HUMAN_9606_idmapping_selected.tab.gz": (
        "knowledgebase/idmapping/by_organism/HUMAN_9606_idmapping_selected.tab.gz", 66318306,
        "87d9fd9156a557a8c684cf2b3be40dbe"),
}
NOTES = {"uniref50.release_note": "uniref/uniref50/uniref50.release_note",
         "relnotes.txt": "relnotes.txt"}
ATTEMPTS = 4
os.makedirs("/tmp/out", exist_ok=True)


def get(path, dest):
    """One streamed download; returns (bytes, md5, sha256) or None on a network error."""
    for m in MIRRORS:
        h5, h256, n, t0 = hashlib.md5(), hashlib.sha256(), 0, time.time()
        try:
            with urllib.request.urlopen(m + path, timeout=120) as r, open(dest, "wb") as fh:
                while True:
                    b = r.read(8 << 20)
                    if not b:
                        break
                    fh.write(b)
                    h5.update(b)
                    h256.update(b)
                    n += len(b)
        except OSError as e:
            print("%s from %s: %s at byte %d" % (path, m, type(e).__name__, n), flush=True)
            continue
        dt = max(time.time() - t0, 1e-3)
        print("%s: %d bytes from %s in %.1f s (%.1f MB/s)" % (path, n, m, dt, n / dt / 1e6), flush=True)
        return n, h5.hexdigest(), h256.hexdigest()
    return None


for name, path in NOTES.items():
    r = get(path, "/tmp/out/" + name)
    if r is None:
        sys.exit("FAIL: could not fetch %s" % path)
    text = open("/tmp/out/" + name).read()
    if ("Release: %s" % RELEASE) not in text and ("Release %s" % RELEASE) not in text:
        sys.exit("FAIL: %s does not name release %s -- current_release has moved on" % (name, RELEASE))
    print("%s names release %s" % (name, RELEASE), flush=True)

for name, (path, size, md5) in FILES.items():
    dest = "/tmp/out/" + name
    for attempt in range(1, ATTEMPTS + 1):
        r = get(path, dest)
        if r and r[0] == size and r[1] == md5:
            break
        print("%s: attempt %d: %s (metalink says %d / %s)" % (name, attempt, r and r[:2], size, md5),
              flush=True)
        time.sleep(20)
    else:
        if os.path.exists(dest):
            os.remove(dest)
        sys.exit("FAIL: %s does not match UniProt %s's metalink after %d attempts" % (name, RELEASE, ATTEMPTS))
    with open(dest + ".sha256", "w") as fh:
        fh.write("%s  %s\n" % (r[2], name))
    print("%s: md5 %s == metalink, sha256 %s" % (name, r[1], r[2]), flush=True)
print("FETCH OK")
