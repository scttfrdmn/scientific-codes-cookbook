#!/usr/bin/env python3
"""Runs inside a spawn task (stage.task.json): fetch PXD083239 from PRIDE straight into the task's
/tmp/out, which spawn stages to s3://<bucket>/inputs/proteomics-real/. Nothing touches a laptop.

PRIDE publishes no checksums for this project, so the sha256 of what it served is recorded in
out/source.sha256; stage-inputs.sh compares that to the copy committed beside this script, so a
changed file on PRIDE fails staging rather than silently becoming a different workload.

The published answer -- the target PSMs Proteome Discoverer 2.5 (SEQUEST + Percolator) accepted
at 1% FDR -- lives in the submitters' .pdResult files, which are SQLite. They are read here and
reduced to (mzML spectrum id, peptide) per run, because the task images ship no SQLite reader.
"""
import bisect
import concurrent.futures
import hashlib
import os
import re
import sqlite3
import sys
import time
import urllib.request

SRC = "https://ftp.pride.ebi.ac.uk/pride/data/archive/2026/08/PXD083239/"
RUNS = "GFP_C001_1 GFP_C001_2 GFP_C001_3 MCL1_c007_1 MCL1_c007_2 MCL1_c007_3".split()
OUT = "/tmp/out"


def fetch(name, dest):
    """GET with Range-resume: PRIDE's HTTPS front end drops long transfers."""
    req = urllib.request.Request(SRC + name, method="HEAD")
    want = int(urllib.request.urlopen(req, timeout=60).headers["Content-Length"])
    for attempt in range(60):
        have = os.path.getsize(dest) if os.path.exists(dest) else 0
        if have == want:
            break
        req = urllib.request.Request(SRC + name, headers={"Range": "bytes=%d-" % have})
        try:
            with urllib.request.urlopen(req, timeout=120) as r, open(dest, "ab") as f:
                while True:
                    b = r.read(1 << 20)
                    if not b:
                        break
                    f.write(b)
        except Exception as e:  # resume on the next pass
            print("  %s: retry %d after %r" % (name, attempt + 1, e), flush=True)
            time.sleep(5)
    if os.path.getsize(dest) != want:
        sys.exit("could not fetch %s" % name)
    h = hashlib.sha256()
    with open(dest, "rb") as f:
        for b in iter(lambda: f.read(1 << 22), b""):
            h.update(b)
    print("  %-34s %11d bytes  %s" % (name, want, h.hexdigest()[:16]), flush=True)
    return h.hexdigest()


def sha(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(1 << 22), b""):
            h.update(b)
    return h.hexdigest()


os.makedirs(OUT + "/ref", exist_ok=True)
t0 = time.time()
# One connection from PRIDE runs at ~0.6 MB/s even in-region, so all 13 files are fetched at
# once; the six .pdResult files (3.2 GB) then sit beside the mzML until read, which is why this
# task asks for 32 GiB (16 GiB of tmpfs) rather than the 16 the kept bytes would need.
jobs = [("SwissProt_Human_01062026.fasta", OUT + "/target.fasta")]
for r in RUNS:
    jobs.append(("02152024_%s.mzML" % r, "%s/%s.mzML" % (OUT, r)))
    jobs.append(("02152024_%s.pdResult" % r, "/tmp/%s.pdResult" % r))
with concurrent.futures.ThreadPoolExecutor(len(jobs)) as ex:
    got = dict(zip([n for n, _ in jobs], ex.map(lambda j: fetch(*j), jobs)))
print("fetched in %.0f s" % (time.time() - t0), flush=True)
src = [(got[n], n) for n, _ in jobs]
derived = [(got["SwissProt_Human_01062026.fasta"], "target.fasta")]


def mzml_spectra(path):
    """id ("scan=N") -> (scan start time, charge), streamed. These mzML are Proteome
    Discoverer's own export, MS2 only, and their scan=N is the exporter's numbering: it is
    neither the raw scan number (PD's FirstScan) nor PD's SpectrumID, which drift apart from it
    by the spectra the export skipped. So the join to PD's PSMs is by retention time."""
    out, cur, t = {}, None, None
    sid = re.compile(r'<spectrum [^>]*id="scan=(\d+)"')
    sst = re.compile(r'name="scan start time" value="([^"]+)"')
    sch = re.compile(r'name="charge state" value="(\d+)"')
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            m = sid.search(line)
            if m:
                cur, t = int(m.group(1)), None
                continue
            if cur is None:
                continue
            m = sst.search(line)
            if m:
                t = float(m.group(1))
                out[cur] = (t, 0)
                continue
            m = sch.search(line)
            if m and t is not None:
                out[cur] = (t, int(m.group(1)))
                cur = None
    return out


for r in RUNS:
    derived.append((got["02152024_%s.mzML" % r], "%s.mzML" % r))
    pd = "/tmp/%s.pdResult" % r
    db = sqlite3.connect("file:%s?mode=ro" % pd, uri=True)
    files = db.execute("select distinct SpectrumFileName from TargetPsms").fetchall()
    assert files == [("02152024_%s.raw" % r,)], files      # one run per pdResult, the right one
    acc = db.execute("select Sequence, RetentionTime, Charge from TargetPsms "
                     "where PercolatorqValue <= 0.01").fetchall()
    db.close()
    os.remove(pd)
    spectra = mzml_spectra("%s/%s.mzML" % (OUT, r))
    times = sorted((t, s) for s, (t, _) in spectra.items())
    tv = [t for t, _ in times]
    rows, miss, zdiff = [], 0, 0
    for seq, t, z in acc:
        # every accepted PSM must land on exactly one spectrum within 1e-6 min of PD's own time
        i = bisect.bisect_left(tv, t - 1e-6)
        hit = [times[j][1] for j in range(i, min(i + 3, len(tv))) if abs(tv[j] - t) <= 1e-6]
        if len(hit) != 1:
            miss += 1
            continue
        rows.append((hit[0], seq))
        zdiff += spectra[hit[0]][1] != z
    print("  %s: %d mzML spectra; %d of %d published PSMs without a unique retention-time match; "
          "%d whose PSM charge differs from the spectrum's precursor charge (reported)"
          % (r, len(spectra), miss, len(acc), zdiff), flush=True)
    assert miss == 0, miss
    rows.sort()
    ref = "%s/ref/ref_%s.tsv" % (OUT, r)
    with open(ref, "w") as f:
        for s, seq in rows:
            f.write("%d\t%s\n" % (s, seq))
    derived.append((sha(ref), "ref/ref_%s.tsv" % r))
    print("  published %-12s %6d PSMs at q<=0.01 on %6d spectra" % (r, len(rows), len({x[0] for x in rows})),
          flush=True)

with open(OUT + "/source.sha256", "w") as f:
    for h, n in src:
        f.write("%s  %s\n" % (h, n))
with open(OUT + "/derived.sha256", "w") as f:
    for h, n in derived:
        f.write("%s  %s\n" % (h, n))
print("staged in %.0f s" % (time.time() - t0))
print("STAGE OK")
