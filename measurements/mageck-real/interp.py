#!/usr/bin/env python3
"""Is `mageck count`'s speed the chip or the image's Python build? (attribution probe)

    python3 -u interp.py <fastq.gz>

Prints the interpreter's build flags and zlib, then times the two halves of mageck count's inner
loop separately, single-threaded: (1) gzip.open(..., 'rt') line iteration over a whole FASTQ,
exactly as mageckCountIO reads it, and (2) the per-read work on in-memory reads -- 9 offset
slices and dict lookups -- with no I/O. Same code, same bytes, on each image.
"""
import gzip
import platform
import sys
import sysconfig
import time
import zlib

fq = sys.argv[1]
print("python\t%s %s" % (platform.python_implementation(), sys.version.replace("\n", " ")))
for k in ("CONFIG_ARGS", "PY_CFLAGS_NODIST", "CC"):
    print("%s\t%s" % (k, str(sysconfig.get_config_var(k))))
print("zlib\t%s (runtime %s)" % (zlib.ZLIB_VERSION, getattr(zlib, "ZLIB_RUNTIME_VERSION", "?")))

t0 = time.time()
n, seqs = 0, []
with gzip.open(fq, "rt") as f:
    for line in f:
        n += 1
        if n % 4 == 2 and len(seqs) < 5000000:
            seqs.append(line.strip())
t1 = time.time()
print("gzip_rt_lines\t%d lines in %.1f s (%.2f M lines/s)" % (n, t1 - t0, n / (t1 - t0) / 1e6))

lib = {}
for l in open("/tmp/data/brunello.tsv"):
    p = l.strip().split("\t")
    lib.setdefault(p[1], p[0])
offs = list(range(22, 31))
t0 = time.time()
hit = 0
for _ in range(2):
    for s in seqs:
        for t in offs:
            if s[t:t + 20] in lib:
                hit += 1
                break
t1 = time.time()
print("slice_lookup\t%d reads x2 in %.1f s (%.2f M reads/s), %d hits" % (len(seqs), t1 - t0, 2 * len(seqs) / (t1 - t0) / 1e6, hit))
print("INTERP OK")
