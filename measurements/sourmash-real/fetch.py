#!/usr/bin/env python3
"""sourmash-real, task 0: fetch sourmash's prepared GTDB R226 species-representative database on AWS.

    python3 -u fetch.py <outdir>

The k=31 FracMinHash sketches of all GTDB R10-RS226 species representatives and their lineages,
from the sourmash project's database host. Each file is streamed straight to disk and hashed on the
way; the sha256 printed here is the pin (pins.sha256). Then the zip's own manifest is read so the
signature count, ksize and scaled are on record, and every sketch is checked to have a lineage.
"""
import csv, hashlib, os, subprocess, sys, time, urllib.request

BASE = "https://farm.cse.ucdavis.edu/~ctbrown/sourmash-db/gtdb-rs226/"
FILES = ["gtdb-rs226-reps.k31.sig.zip", "gtdb-rs226-reps.lineages.csv"]
out = sys.argv[1]
os.makedirs(out, exist_ok=True)
for name in FILES:
    t0 = time.time()
    h = hashlib.sha256()
    req = urllib.request.Request(BASE + name, headers={"User-Agent": "scientific-codes-cookbook"})
    with urllib.request.urlopen(req, timeout=600) as r, open(os.path.join(out, name), "wb") as f:
        hdr = {k: r.headers.get(k) for k in ("Content-Length", "Last-Modified", "ETag")}
        n = 0
        while True:
            b = r.read(1 << 22)
            if not b:
                break
            h.update(b); f.write(b); n += len(b)
    if hdr["Content-Length"] and int(hdr["Content-Length"]) != n:
        sys.exit("FAIL: %s got %d bytes, server said %s" % (name, n, hdr["Content-Length"]))
    print("%s  %s" % (h.hexdigest(), name))
    print("  %s bytes %d last_modified %s etag %s fetch_s %.0f"
          % (name, n, hdr["Last-Modified"], hdr["ETag"], time.time() - t0), file=sys.stderr)

zipf = os.path.join(out, FILES[0])
man = os.path.join(out, "manifest.csv")
subprocess.run(["sourmash", "sig", "manifest", zipf, "-o", man], check=True)
rows = list(csv.DictReader(l for l in open(man) if not l.startswith("#")))
ks = sorted(set(r["ksize"] for r in rows)); sc = sorted(set(r["scaled"] for r in rows))
mol = sorted(set(r["moltype"] for r in rows))
# sourmash tax matches on the first word of a sketch's name, with the version suffix dropped
lin = set(r[0].split(".")[0] for r in list(csv.reader(open(os.path.join(out, FILES[1]))))[1:])
idents = set(r["name"].split(" ")[0].split(".")[0] for r in rows)
print("manifest signatures %d distinct idents %d ksize %s scaled %s moltype %s" % (len(rows), len(idents), ks, sc, mol), file=sys.stderr)
print("lineages rows %d; representatives with no sketch %d; sketches with no lineage %d"
      % (len(lin), len(lin - idents), len(idents - lin)), file=sys.stderr)
# The lineages file lists every GTDB R226 species representative; the zip holds those sourmash
# could sketch. What tax needs is that every sketch has a lineage, not that every lineage has a sketch.
if idents - lin or len(idents) != len(rows) or ks != ["31"] or sc != ["1000"]:
    sys.exit("FAIL: a sketch with no lineage, a duplicate ident, or not k=31/scaled=1000")
