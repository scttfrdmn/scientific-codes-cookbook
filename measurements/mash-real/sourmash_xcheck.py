#!/usr/bin/env python3
"""mash-real, task 02 (the sourmash image, on every chip): the same genome bytes through a second
MinHash code, against the same GTDB truth and against mash's own answer from task 01 on this chip.

    python3 -u sourmash_xcheck.py <threads>

Reads /tmp/genomes.tar /tmp/genomes.tsv /tmp/species.tsv /tmp/mash_nearest.tsv. Writes /tmp/out/.
k=21 matches mash. mash's bottom-10000 sketch and sourmash's FracMinHash (scaled=1000) are different
estimators of the same k-mer Jaccard, and each maps Jaccard to ANI by its own formula, so the
comparison is by rank and by which representative each picks, never by raw value.
"""
import hashlib
import math
import os
import resource
import subprocess
import sys
import time

T = int(sys.argv[1])
K, SCALED = 21, 1000
O, W = "/tmp/out", "/var/tmp/s"
os.makedirs(O, exist_ok=True)
os.makedirs(W + "/g", exist_ok=True)
os.makedirs(W + "/sig", exist_ok=True)
R = open(O + "/results.txt", "w")
C = open(O + "/checks.txt", "w")
fails = 0


def rec(k, v):
    line = "%-28s %s" % (k, str(v))
    print(line)
    R.write(line + "\n")
    R.flush()


def check(ok, name, claim, got):
    global fails
    ok = bool(ok)
    fails += 0 if ok else 1
    line = "%-26s %-62s %s (%s)" % (name, claim, "ok  " if ok else "FAIL", got)
    print(line)
    C.write(line + "\n")
    C.flush()


import sourmash  # noqa: E402  (after the helpers, so a failed import still leaves results.txt)

rec("arch", os.uname().machine)
rec("sourmash_version", getattr(sourmash, "VERSION", "unknown"))
rec("python", sys.version.split()[0])
rec("threads", T)

subprocess.run(["tar", "-xf", "/tmp/genomes.tar", "-C", W + "/g"], check=True)
files = sorted(os.listdir(W + "/g"))
gen = [l.rstrip("\n").split("\t") for l in open("/tmp/genomes.tsv")]
check([g[0] for g in gen] == files, "genomes", "the tar's files == genomes.tsv, one each", len(files))
rep_of = {g[0]: g[3] + ".fna.gz" for g in gen}
isrep = {g[0]: g[4] == "t" for g in gen}
sp_of = {g[0]: g[2] for g in gen}
reps = [f for f in files if isrep[f]]
queries = [f for f in files if not isrep[f]]

# sketch in T parallel chunks; a FracMinHash sketch of a file does not depend on its batch
chunks = [files[i::T] for i in range(T)]
t0 = time.time()
procs = []
for i, ch in enumerate(chunks):
    lst = "%s/chunk%02d.txt" % (W, i)
    with open(lst, "w") as fh:
        fh.write("".join(W + "/g/" + f + "\n" for f in ch))
    procs.append(subprocess.Popen(
        ["sourmash", "sketch", "dna", "-p", "k=%d,scaled=%d" % (K, SCALED), "--from-file", lst,
         "-o", "%s/sig/chunk%02d.zip" % (W, i)], stderr=open("%s/sketch%02d.log" % (O, i), "w")))
rcs = [p.wait() for p in procs]
rec("sketch_s", "%.2f" % (time.time() - t0))
check(all(r == 0 for r in rcs), "sketch", "every sourmash sketch process exited 0", rcs.count(0))
if fails:
    sys.exit(1)

t0 = time.time()
mh = {}
for i in range(T):
    for s in sourmash.load_file_as_signatures("%s/sig/chunk%02d.zip" % (W, i), ksize=K):
        mh[os.path.basename(s.filename)] = s.minhash
check(sorted(mh) == files, "signatures", "one per genome, k=21 scaled=1000", len(mh))
check(all(mh[f].jaccard(mh[f]) == 1.0 for f in files), "self similarity", "jaccard(a, a) == 1, every genome", len(files))

# every genome against every representative: sourmash's nearest rep, and its ANI to its GTDB rep
near, ani_rep = {}, {}
for q in queries:
    best, bani = None, -1.0
    for r in reps:
        a = mh[q].jaccard_ani(mh[r]).ani
        a = 0.0 if a is None else a
        if r == rep_of[q]:
            ani_rep[q] = a
        if a > bani:
            best, bani = r, a
    near[q] = (best, bani)
rec("screen_s", "%.2f" % (time.time() - t0))

mash = {}
for l in open("/tmp/mash_nearest.tsv"):
    f = l.rstrip("\n").split("\t")
    mash[f[0]] = (f[3], float(f[5]))          # mash's nearest rep, mash distance to the GTDB rep
check(sorted(mash) == sorted(queries), "mash's screen", "task 01's nearest.tsv covers the same genomes", len(mash))

n = len(queries)
gtdb = sum(near[q][0] == rep_of[q] for q in queries)
both = sum(near[q][0] == mash[q][0] for q in queries)
# floors fixed before the run: backstops, not fits
check(gtdb / n >= 0.95, "nearest rep == GTDB's", ">= 0.95 of non-representative genomes (as mash)", "%d of %d = %.6f" % (gtdb, n, gtdb / n))
check(both / n >= 0.98, "nearest rep == mash's", ">= 0.98: the two codes pick the same representative", "%d of %d = %.6f" % (both, n, both / n))


def ranks(v):
    o = sorted(range(len(v)), key=lambda i: v[i])
    r = [0.0] * len(v)
    i = 0
    while i < len(o):
        j = i
        while j + 1 < len(o) and v[o[j + 1]] == v[o[i]]:
            j += 1
        for k in range(i, j + 1):
            r[o[k]] = (i + j) / 2.0 + 1
        i = j + 1
    return r


def spearman(x, y):
    rx, ry = ranks(x), ranks(y)
    mx, my = sum(rx) / len(rx), sum(ry) / len(ry)
    num = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    den = math.sqrt(sum((a - mx) ** 2 for a in rx) * sum((b - my) ** 2 for b in ry))
    return num / den if den else float("nan")


rho = spearman([ani_rep[q] for q in queries], [1 - mash[q][1] for q in queries])
check(rho >= 0.9, "ANI to own rep vs mash", "Spearman >= 0.9 over every member-to-rep pair", "rho %.4f over %d" % (rho, n))
diffs = sorted(abs(100 * ani_rep[q] - 100 * (1 - mash[q][1])) for q in queries)
rec("median_abs_ani_diff_mash", "%.3f ANI points (sourmash jaccard_ani vs mash 1-d)" % diffs[n // 2])

# per species, against GTDB's published mean intra-species ANI -- the same comparison task 01 makes
sp = {l.split("\t")[0]: l.rstrip("\n").split("\t") for l in open("/tmp/species.tsv")}
acc = {}
for q in queries:
    acc.setdefault(rep_of[q], []).append(100 * ani_rep[q])
rows = []
for r, v in sorted(acc.items()):
    key = ("RS_" if r.startswith("GCF") else "GB_") + r[:-7]
    s = sp[key]
    if int(s[8]) >= 3:
        rows.append((r, sum(v) / len(v), float(s[4])))
rho2 = spearman([x[1] for x in rows], [x[2] for x in rows])
check(rho2 >= 0.6, "tracks GTDB's mean ANI", "Spearman >= 0.6, species with >= 3 genomes (as mash)", "rho %.4f over %d species" % (rho2, len(rows)))

with open(O + "/nearest.tsv", "w") as fh:
    for q in queries:
        fh.write("%s\t%s\t%s\t%s\t%.6f\t%.6f\t%s\n" % (q, sp_of[q], rep_of[q], near[q][0], near[q][1], ani_rep[q], mash[q][0]))
with open(O + "/fingerprints.txt", "w") as fh:
    fh.write("%s  nearest.tsv\n" % hashlib.sha256(open(O + "/nearest.tsv", "rb").read()).hexdigest())
self_ = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
kids = resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss
rec("peak_rss_kib_self", self_)
rec("peak_rss_kib_largest_child", kids)
rec("failures", fails)
R.close()
C.close()
sys.exit(1 if fails else 0)
