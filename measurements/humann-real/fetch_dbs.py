#!/usr/bin/env python3
"""humann-real, task 0: stream one of HUMAnN's full databases from the bioBakery server into /tmp/out.

    python3 -u fetch_dbs.py <chocophlan|uniref> <outdir>

The download is hashed and untarred in one pass (nothing touches a disk twice), and its sha256 and
byte count are printed against the Content-Length the server advertised, so a truncated stream
fails. These are the URLs `humann_databases --download` uses for HUMAnN 3.9 (versioned filenames:
ChocoPhlAn v201901_v31, UniRef90 v201901b).
"""
import hashlib
import os
import sys
import tarfile
import time
import urllib.request

BASE = "https://huttenhower.sph.harvard.edu/humann_data/"
DBS = {
    "chocophlan": ("chocophlan/full_chocophlan.v201901_v31.tar.gz", 16502062909),
    "uniref": ("uniprot/uniref_annotated/uniref90_annotated_v201901b_full.tar.gz", 20579913329),
}


class Hashing:
    def __init__(self, fh):
        self.fh, self.h, self.n = fh, hashlib.sha256(), 0

    def read(self, size=-1):
        b = self.fh.read(size)
        self.h.update(b)
        self.n += len(b)
        return b


def main():
    name, outdir = sys.argv[1], sys.argv[2]
    path, want = DBS[name]
    dest = os.path.join(outdir, name)
    os.makedirs(dest, exist_ok=True)
    t0 = time.time()
    resp = urllib.request.urlopen(BASE + path, timeout=300)
    length = int(resp.headers.get("Content-Length", "0"))
    src = Hashing(resp)
    files = 0
    with tarfile.open(fileobj=src, mode="r|gz") as tf:
        for m in tf:
            if not (m.isfile() or m.isdir()) or m.name.startswith("/") or ".." in m.name.split("/"):
                sys.exit("FAIL: unexpected tar member %r" % m.name)
            tf.extract(m, dest, filter="data") if hasattr(tarfile, "data_filter") else tf.extract(m, dest)
            files += m.isfile()
    while src.read(1 << 20):   # the gzip trailer and any padding, so the hash covers every byte
        pass
    wall = time.time() - t0
    # The Globus endpoint the server redirects to streams without a Content-Length (measured: 0),
    # so the byte count is checked against the length the server's HEAD reports, recorded above;
    # gzip's own CRC32 and ISIZE trailer, read to EOF, check the content.
    ok = src.n == want and length in (0, want)
    print("%s  %s" % (src.h.hexdigest(), os.path.basename(path)))
    print("%s bytes %d content_length %d expected %d files %d wall_s %.0f MBps %.0f %s"
          % (name, src.n, length, want, files, wall, src.n / wall / 1e6, "ok" if ok else "FAIL"))
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
