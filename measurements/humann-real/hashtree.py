#!/usr/bin/env python3
"""Hash every file under a directory tree, in parallel, or check a tree against such a manifest.

    python3 -u hashtree.py write <root> <manifest>
    python3 -u hashtree.py check <root> <manifest>

The manifest is `sha256  relative/path`, sorted by path, one line per file, so the manifest's own
sha256 pins the whole tree (that is what pins.sha256 records). Python's hashlib rather than
sha256sum: the x86 image's BusyBox sha256sum is several times slower on the 53 GB of databases.
A check fails on a missing, extra or changed file.
"""
import hashlib
import os
import sys
import time
from multiprocessing import Pool


def digest(args):
    root, rel = args
    h = hashlib.sha256()
    with open(os.path.join(root, rel), "rb") as fh:
        for b in iter(lambda: fh.read(1 << 22), b""):
            h.update(b)
    return rel, h.hexdigest()


def tree(root):
    out = []
    for d, _, fs in os.walk(root):
        out += [os.path.relpath(os.path.join(d, f), root) for f in fs]
    return sorted(out)


def main():
    mode, root, manifest = sys.argv[1:4]
    t0 = time.time()
    files = tree(root)
    with Pool(os.cpu_count()) as p:
        got = dict(p.map(digest, [(root, f) for f in files], chunksize=1))
    nbytes = sum(os.path.getsize(os.path.join(root, f)) for f in files)
    if mode == "write":
        with open(manifest, "w") as fh:
            fh.writelines("%s  %s\n" % (got[f], f) for f in files)
        bad = 0
    else:
        want = dict((l.rstrip("\n").split("  ", 1)[::-1]) for l in open(manifest))
        bad = sum(1 for f in want if got.get(f) != want[f]) + sum(1 for f in got if f not in want)
    print("hashtree %s files %d bytes %d mismatches %d wall_s %.0f"
          % (mode, len(files), nbytes, bad, time.time() - t0))
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
