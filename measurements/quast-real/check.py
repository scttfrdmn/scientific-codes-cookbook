#!/usr/bin/env python3
"""Assert the quast-real claims from QUAST's own reports, and recompute its length statistics.

    python3 -I check.py <quast outdir> <reference.fna.gz> <reference.gff.gz> <truth.json> <spades-real report.tsv> \
        <label=fasta> ...

1. Every length statistic QUAST reports (contig counts and total lengths at each threshold, largest
   contig, GC, N's, N50/N90/NG50/NG90, L50..LG90, auN, auNG) equals an independent computation from
   the same FASTA bytes, for every assembly. This is plain Python, not QUAST's code.
2. The three planted controls report exactly what was planted (truth.json, from mkcontrols.py).
3. The SPAdes contigs report exactly what spades-real's QUAST run recorded on the same bytes.
4. Every published MG1655 genome covers >= 95% of U00096.3 (same strain, closed genomes).
Prints one row per claim; exits 1 if any fails.
"""
import gzip
import json
import os
import sys

qdir, ref_path, gff_path, truth_path, spades_report = sys.argv[1:6]
files = dict(a.split("=", 1) for a in sys.argv[6:])
MIN = 500                       # QUAST's default --min-contig
THRESH = [0, 1000, 5000, 10000, 25000, 50000]
fails = 0


def row(ok, who, what, detail):
    global fails
    if not bool(ok):
        fails += 1
    print("%-17s %-52s %s (%s)" % (who, what, "ok  " if ok else "FAIL", detail), flush=True)


def table(path):
    with open(path) as fh:
        rows = [line.rstrip("\n").split("\t") for line in fh]
    head = rows[0][1:]
    return {r[0].strip(): dict(zip(head, r[1:])) for r in rows[1:] if r and r[0]}


def read_fasta(path):
    op = gzip.open if path.endswith(".gz") else open
    seqs, cur = [], []
    with op(path, "rt") as fh:
        for line in fh:
            if line.startswith(">"):
                if cur or seqs:
                    seqs.append("".join(cur))
                cur = []
            else:
                cur.append(line.strip().upper())
    seqs.append("".join(cur))
    return seqs


def nx(lengths, total, frac):
    c = 0
    for k, x in enumerate(sorted(lengths, reverse=True), 1):
        c += x
        if c >= total * frac:
            return str(x), str(k)
    return "-", "-"


ref = "".join(read_fasta(ref_path))
R = len(ref)


def gc(s):
    acgt = sum(s.count(b) for b in "ACGT")
    return "%.2f" % (100.0 * (s.count("G") + s.count("C")) / acgt) if acgt else "-"


def stats(seqs):
    lens = [len(s) for s in seqs]
    big = [s for s in seqs if len(s) >= MIN]
    bl = [len(s) for s in big]
    T = sum(bl)
    d = {}
    for t in THRESH:
        d["# contigs (>= %d bp)" % t] = str(sum(1 for x in lens if x >= t))
        d["Total length (>= %d bp)" % t] = str(sum(x for x in lens if x >= t))
    d["# contigs"] = str(len(bl))
    d["Largest contig"] = str(max(bl))
    d["Total length"] = str(T)
    d["Reference length"] = str(R)
    d["GC (%)"] = gc("".join(big))
    d["Reference GC (%)"] = gc(ref)
    d["N50"], d["L50"] = nx(bl, T, 0.5)
    d["N90"], d["L90"] = nx(bl, T, 0.9)
    d["NG50"], d["LG50"] = nx(bl, R, 0.5)
    d["NG90"], d["LG90"] = nx(bl, R, 0.9)
    d["auN"] = "%.1f" % (sum(x * x for x in bl) / T)
    d["auNG"] = "%.1f" % (sum(x * x for x in bl) / R)
    d["# N's per 100 kbp"] = "%.2f" % (100000.0 * "".join(big).count("N") / T)
    return d


rep = table(os.path.join(qdir, "report.tsv"))
mis = table(os.path.join(qdir, "contigs_reports", "misassemblies_report.tsv"))
truth = json.load(open(truth_path))

# 1. independent length statistics
for label, path in files.items():
    mine = stats(read_fasta(path))
    bad = ["%s: quast %s, independent %s" % (k, rep.get(k, {}).get(label), v)
           for k, v in mine.items() if rep.get(k, {}).get(label) != v]
    row(not bad, label, "%d length statistics == independent recomputation" % len(mine),
        "; ".join(bad[:4]) or "N50 %s, total %s, GC %s" % (mine["N50"], mine["Total length"], mine["GC (%)"]))


def get(label, key):
    for t in (rep, mis):
        if key in t and label in t[key]:
            return t[key][label]
    return None


# 2. planted truth
for label in ("self", "planted", "chimera"):
    for k, want in sorted(truth[label].items()):
        if k == "length":
            got = str(sum(len(s) for s in read_fasta(files[label])))
        else:
            got = get(label, k)
        row(got == str(want), label, "%s == planted" % k, "want %s, got %s" % (want, got))

# 2b. the annotation, counted from the GFF itself: every feature line, the chromosome-long `region` too (QUAST loads type ANY). The
# reference contains each one whole; the chimera breaks exactly the features that cross a cut.
feats = []
with gzip.open(gff_path, "rt") as fh:
    for line in fh:
        f = line.rstrip("\n").split("\t")
        if line.startswith("#") or len(f) < 9:
            continue
        feats.append((int(f[3]), int(f[4])))
n = len(feats)
# a cut at 0-based c separates 1-based bases c and c+1
cross = sum(1 for s, e in feats if any(s <= c < e for c in truth["chimera_cuts"]))
for label, want in (("self", "%d + 0 part" % n), ("planted", "%d + 0 part" % n),
                    ("chimera", "%d + %d part" % (n - cross, cross))):
    got = get(label, "# genomic features")
    row(got == want, label, "# genomic features == count from the GFF", "want %s, got %s" % (want, got))

# 3. the SPAdes contigs, same bytes and QUAST version as spades-real's evaluation
prev = table(spades_report)
keys = [k for k in prev if k not in ("Assembly",)]
bad = ["%s: now %s, spades-real %s" % (k, get("spades_contigs", k), prev[k]["arm64"])
       for k in keys if get("spades_contigs", k) != prev[k]["arm64"]]
row(not bad, "spades_contigs", "all %d metrics == spades-real's QUAST report" % len(keys),
    "; ".join(bad[:4]) or "genome fraction %s, NGA50 %s" % (get("spades_contigs", "Genome fraction (%)"),
                                                             get("spades_contigs", "NGA50")))

# 4. published closed genomes of the same strain
for label in files:
    if label in ("self", "planted", "chimera") or label.startswith("spades"):
        continue
    g = get(label, "Genome fraction (%)")
    row(g not in (None, "-") and float(g) >= 95.0, label, "genome fraction >= 95% of U00096.3", g)

print("check_failures %d" % fails, flush=True)
sys.exit(1 if fails else 0)
