#!/usr/bin/env python3
"""Fetch the plumed-real cohort from RODA's Conformational Space of Short Peptides into one bundle.

    python3 -u fetch.py <outdir> [limit]

The cohort is every Ala-capped pentapeptide N-ALA-X-Y-Z-C-ALA -- all 8,000 central tripeptides
over the 20 amino acids -- replicas R2 and R3, the two that ship their own tleap topology (R1
packages carry none). 16,000 sander trajectories. Source: s3://toyokounqpeptides/5pep/ (RODA,
us-west-2, public, "not updated").

Every object's MD5 is checked against the ETag RODA serves for it (single-part uploads, so the
ETag is the MD5), and its sha256 is recorded. The bundle is a plain tar of the untouched
.tar.bz2 objects in sorted order with zeroed metadata, so its own sha256 is reproducible.
"""
import concurrent.futures as cf
import hashlib
import io
import os
import sys
import tarfile
import time
import urllib.request

AA = ("ALA ARG ASN ASP CYS GLN GLU GLY HIS ILE LEU LYS MET PHE PRO SER THR TRP TYR VAL").split()
URL = "https://toyokounqpeptides.s3.us-west-2.amazonaws.com/5pep/%s"


def names():
    out = []
    for x in AA:
        for y in AA:
            for z in AA:
                for r in ("R2", "R3"):
                    out.append("NALA%s%s%sCALA-%s.tar.bz2" % (x, y, z, r))
    return sorted(out)


def get(name):
    for attempt in range(4):
        try:
            with urllib.request.urlopen(URL % name, timeout=60) as r:
                data = r.read()
                etag = r.headers.get("ETag", "").strip('"')
            md5 = hashlib.md5(data).hexdigest()
            if md5 != etag:
                raise ValueError("md5 %s != ETag %s" % (md5, etag))
            return name, data, etag, hashlib.sha256(data).hexdigest()
        except Exception as e:  # transient network error: retry, then fail loudly
            err = e
            time.sleep(2 * (attempt + 1))
    raise RuntimeError("%s: %s" % (name, err))


def main():
    outdir = sys.argv[1]
    limit = int(sys.argv[2]) if len(sys.argv) > 2 else 0
    want = names()
    if limit:
        step = max(1, len(want) // limit)
        want = want[::step][:limit]
    os.makedirs(outdir, exist_ok=True)
    got = {}
    t0 = time.time()
    with cf.ThreadPoolExecutor(32) as ex:
        for i, (n, data, etag, sha) in enumerate(ex.map(get, want)):
            got[n] = (data, etag, sha)
            if (i + 1) % 2000 == 0:
                print("fetched %d/%d in %.0f s" % (i + 1, len(want), time.time() - t0))
    bundle = os.path.join(outdir, "cohort.tar")
    with tarfile.open(bundle, "w", format=tarfile.USTAR_FORMAT) as tf, \
         open(os.path.join(outdir, "manifest.tsv"), "w") as man:
        man.write("name\tbytes\tmd5_etag\tsha256\n")
        for n in want:
            data, etag, sha = got[n]
            ti = tarfile.TarInfo(n)
            ti.size = len(data); ti.mtime = 0; ti.mode = 0o644; ti.uid = ti.gid = 0
            ti.uname = ti.gname = ""
            tf.addfile(ti, io.BytesIO(data))
            man.write("%s\t%d\t%s\t%s\n" % (n, len(data), etag, sha))
    h = hashlib.sha256()
    with open(bundle, "rb") as fh:
        for b in iter(lambda: fh.read(1 << 20), b""):
            h.update(b)
    total = sum(len(v[0]) for v in got.values())
    print("objects %d bytes %d md5==ETag for all; fetch %.0f s" % (len(want), total, time.time() - t0))
    print("%s  cohort.tar" % h.hexdigest())
    with open(os.path.join(outdir, "cohort.sha256"), "w") as fh:
        fh.write("%s  cohort.tar\n" % h.hexdigest())


if __name__ == "__main__":
    main()
