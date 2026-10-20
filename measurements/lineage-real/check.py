#!/usr/bin/env python3
"""Task 02's checks: pangolin and Nextclade on the same genomes, against each other and against
pango-designation's own labels for those samples. Writes out/check.json and out/digests.tsv.

Identities (exact, every row, every chip):
  P1  each pangolin output has one row per query genome, no other, no repeats
  P2  every row reports pangolin 4.4 and pangolin-data PUSHER-v1.38
  P3  UShER's placement histogram is a partition: the counts in "L(a/b) ..." sum to b, every b
      is the same, the called lineage is in it, and conflict == (b - a_called) / b
  P4  a genome failing pangolin's QC is Unassigned
  P5  the report's is_designated is exactly "this genome's aligned-sequence hash is in
      pangolin-data's designation hash table" (hash_map.csv + designation_status.csv)
  P6  batch and thread invariance: the first SUB genomes alone at 1 thread == their rows in the
      whole-batch run at T threads (UShER places with -n, so a placement must not depend on
      what else is in the batch)
  N1  one Nextclade row per genome
  N2  Nextclade's totals equal its own lists: substitutions, deletions, insertions, missing,
      non-ACGTN, unknown aa, aa substitutions and deletions, private = reversion + labeled +
      unlabeled; coverage == (end - start + 1 - missing - nonACGTN) / 29903, bit for bit
  N3  the first SUB genomes at -j 1 == their rows of the -j T run (index column included)
  A1  the two alias tables (v1.38 in the image, v1.41 staged by recipes/lineage) agree on every
      shared prefix -- a Pango alias is never reassigned
Agreement (floors, each argued in the write-up):
  T1  pangolin (UShER) vs the designated lineage, hierarchy-compatible after unaliasing
  X1  Nextclade vs pangolin, hierarchy-compatible (recipes/lineage's own 0.80 floor)
Exact agreement rates, Nextclade vs designation, and the hash table's labels vs lineages.csv
are reported.
"""
import csv
import gzip
import hashlib
import json
import os
import re
import sys

import pangolin_data

OUT = "out"
UNASSIGNED = {"", "Unassigned", "None", "unclassified"}
fails = []
res = {}


def check(name, ok, detail):
    ok = bool(ok)
    res[name] = {"ok": ok, "detail": detail}
    print("%-4s %-4s %s" % (name, "ok" if ok else "FAIL", detail), flush=True)
    if not ok:
        fails.append(name)


def fasta_ids(path):
    ids = []
    with gzip.open(path, "rt") as fh:
        for line in fh:
            if line.startswith(">"):
                ids.append(line[1:].split()[0])
    return ids


def read_pango(path):
    with open(path, newline="") as fh:
        rows = list(csv.DictReader(fh))
    return rows


QUERY = os.environ.get("QUERY", "query.fasta.gz")
qids = fasta_ids(QUERY)
sids = fasta_ids("sub.fasta.gz")
truth = {}
with open("truth.tsv", newline="") as fh:
    for r in csv.DictReader(fh, delimiter="\t"):
        truth[r["seq"]] = r
qset = set(qids)
check("Q0", len(qset) == len(qids) and qset <= set(truth) and set(sids) <= qset,
      "%d query genomes, all distinct, all labelled; %d in the invariance subset" % (len(qids), len(sids)))

# ---------------- pangolin ----------------
P = {}
for lab in ("full", "sub_t1"):
    rows = read_pango("%s/pangolin_%s.csv" % (OUT, lab))
    taxa = [r["taxon"] for r in rows]
    want = sids if lab == "sub_t1" else qids
    ok = len(taxa) == len(set(taxa)) and set(taxa) == set(want) and len(taxa) == len(want)
    check("P1_" + lab, ok, "%s: %d rows for %d genomes, %d distinct" % (lab, len(taxa), len(want), len(set(taxa))))
    P[lab] = {r["taxon"]: r for r in rows}

vers = {(r["pangolin_version"], r["version"]) for lab in P for r in P[lab].values()}
check("P2", vers == {("4.4", "PUSHER-v1.38")}, "versions reported: %s" % sorted(vers))

HIST = re.compile(r"^([A-Z0-9.]+)\((\d+)/(\d+)\)$")
nh = bad = 0
for lab in ("full", "sub_t1"):
    for r in P[lab].values():
        note = r["note"]
        if "Usher placements:" not in note:
            continue
        nh += 1
        items = note.split("Usher placements:")[1].split(";")[0].split()
        parsed = [HIST.match(i) for i in items]
        if not all(parsed):
            bad += 1
            continue
        a = {m.group(1): int(m.group(2)) for m in parsed}
        bs = {int(m.group(3)) for m in parsed}
        b = bs.pop() if len(bs) == 1 else None
        if b is None or sum(a.values()) != b or r["lineage"] not in a:
            bad += 1
            continue
        if float(r["conflict"]) != (b - a[r["lineage"]]) / b:
            bad += 1
check("P3", nh > 0 and bad == 0, "%d placement histograms, %d not a partition or conflict != (b-a)/b" % (nh, bad))

qcf = [r for r in P["full"].values() if r["qc_status"] == "fail"]
check("P4", all(r["lineage"] == "Unassigned" for r in qcf), "%d QC fails, all Unassigned" % len(qcf))

name2hash = {}
with open(OUT + "/full_hash_map.csv", newline="") as fh:
    for r in csv.DictReader(fh, delimiter="\t"):
        name2hash[r["name"].split()[0]] = r["hash"]
hashlab = {}
with open(OUT + "/full_designation_status.csv", newline="") as fh:
    for r in csv.DictReader(fh):
        if r["designated"] == "True":
            hashlab[r["hash"]] = r["lineage"]
HL = {t: hashlab[name2hash[t]] for t in qids if name2hash.get(t) in hashlab}
flag = {t for t, r in P["full"].items() if r["is_designated"] == "True"}
check("P5", set(name2hash) == qset and flag == set(HL),
      "%d of %d genomes hash to a designated sequence; is_designated set on exactly those: %s"
      % (len(HL), len(qids), flag == set(HL)))

d6 = [t for t in sids if P["sub_t1"][t] != P["full"][t]]
check("P6", not d6, "%d genomes alone at 1 thread vs in the batch at T threads: %d rows differ" % (len(sids), len(d6)))
for t in d6[:5]:
    print("  P6 differs", t, P["sub_t1"][t]["lineage"], P["full"][t]["lineage"])

# ---------------- Nextclade ----------------
with gzip.open("nextclade.tsv.gz", "rt", newline="") as fh:
    ncrows = list(csv.DictReader(fh, delimiter="\t"))
with open("nextclade_sub.tsv", newline="") as fh:
    ncsub = list(csv.DictReader(fh, delimiter="\t"))
nck = [r["seqName"].split()[0] for r in ncrows]
check("N1", len(nck) == len(set(nck)) == len(qids) and set(nck) == qset,
      "%d rows for %d genomes, %d distinct" % (len(nck), len(qids), len(set(nck))))
NC = {r["seqName"].split()[0]: r for r in ncrows}


def rl(s):
    n = 0
    for t in filter(None, s.split(",")):
        if "-" in t:
            a, b = t.split("-")
            n += int(b) - int(a) + 1
        else:
            n += 1
    return n


def lst(s):
    return [t for t in s.split(",") if t]


L = 29903
ident = {k: 0 for k in ("sub", "del", "ins", "mis", "nonacgtn", "unkaa", "aasub", "aadel", "priv", "cov")}
nerr = nchk = 0
for x in ncrows:
    if x["errors"]:
        nerr += 1
        continue
    nchk += 1
    c = {
        "sub": int(x["totalSubstitutions"]) == len(lst(x["substitutions"])),
        "del": int(x["totalDeletions"]) == rl(x["deletions"]),
        "ins": int(x["totalInsertions"]) == sum(len(t.split(":")[1]) for t in lst(x["insertions"])),
        "mis": int(x["totalMissing"]) == rl(x["missing"]),
        "nonacgtn": int(x["totalNonACGTNs"]) == sum(rl(t.split(":")[1]) for t in lst(x["nonACGTNs"])),
        "unkaa": int(x["totalUnknownAa"]) == sum(rl(t.split(":")[1]) for t in lst(x["unknownAaRanges"])),
        "aasub": int(x["totalAminoacidSubstitutions"]) == len(lst(x["aaSubstitutions"])),
        "aadel": int(x["totalAminoacidDeletions"]) == len(lst(x["aaDeletions"])),
        "priv": int(x["privateNucMutations.totalPrivateSubstitutions"]) ==
        int(x["privateNucMutations.totalReversionSubstitutions"]) +
        int(x["privateNucMutations.totalLabeledSubstitutions"]) +
        int(x["privateNucMutations.totalUnlabeledSubstitutions"]),
        "cov": float(x["coverage"]) == (int(x["alignmentEnd"]) - int(x["alignmentStart"]) + 1 -
                                        int(x["totalMissing"]) - int(x["totalNonACGTNs"])) / L,
    }
    for k, v in c.items():
        if not v:
            ident[k] += 1
check("N2", nchk > 0 and not any(ident.values()),
      "%d rows checked (%d with errors skipped); violations per identity: %s" % (nchk, nerr, ident))

subd = [r for r in ncsub if r != NC.get(r["seqName"].split()[0])]
check("N3", len(ncsub) == len(sids) and not subd,
      "%d genomes at -j 1 vs their rows at -j T: %d differ" % (len(ncsub), len(subd)))

# ---------------- aliases, then agreement ----------------
a38 = json.load(open(os.path.join(os.path.dirname(pangolin_data.__file__), "data", "alias_key.json")))
a41 = json.load(open("alias_key_v1.41.json"))
shared = set(a38) & set(a41)
check("A1", all(a38[k] == a41[k] for k in shared) and len(shared) == len(a38),
      "%d aliases in v1.38, all present and unchanged in v1.41 (%d entries)" % (len(a38), len(a41)))
ALIAS = dict(a41)


def unalias(lin):
    if not lin or lin in UNASSIGNED:
        return lin
    parts = lin.split(".")
    v = ALIAS.get(parts[0])
    if isinstance(v, str) and v:
        return ".".join([v] + parts[1:])
    return lin


def compatible(a, b):
    pa, pb = unalias(a).split("."), unalias(b).split(".")
    n = min(len(pa), len(pb))
    return pa[:n] == pb[:n]


def agree(name, call, ref, keys):
    keys = list(keys)
    both = [k for k in keys if call(k) not in UNASSIGNED and ref(k) not in UNASSIGNED]
    ex = sum(1 for k in both if unalias(call(k)) == unalias(ref(k)))
    co = sum(1 for k in both if compatible(call(k), ref(k)))
    finer = sum(1 for k in both if compatible(call(k), ref(k)) and len(unalias(call(k))) > len(unalias(ref(k))))
    coarser = sum(1 for k in both if compatible(call(k), ref(k)) and len(unalias(call(k))) < len(unalias(ref(k))))
    d = {"genomes": len(keys), "both_assigned": len(both), "unassigned": len(keys) - len(both),
         "exact": ex, "exact_rate": round(ex / len(both), 6) if both else None,
         "compatible": co, "compatible_rate": round(co / len(both), 6) if both else None,
         "compatible_finer": finer, "compatible_coarser": coarser}
    res[name] = d
    print("%-4s      %s" % (name, d), flush=True)
    return d, [k for k in both if not compatible(call(k), ref(k))]


des = lambda k: truth[k]["designated"]
pus = lambda k: P["full"][k]["lineage"]
hlb = lambda k: HL[k]
ncp = lambda k: (NC[k]["Nextclade_pango"] or "").strip()

t1, t1bad = agree("t_pangolin_vs_designation", pus, des, qids)
agree("t_pangolin_vs_designation_hash_matched", pus, des, [k for k in qids if k in HL])
agree("t_pangolin_vs_designation_not_hash_matched", pus, des, [k for k in qids if k not in HL])
agree("t_hash_table_label_vs_designation", hlb, des, [k for k in qids if k in HL])
for route in ("strain", "cog"):
    agree("t_pangolin_vs_designation_route_%s" % route, pus, des, [k for k in qids if truth[k]["route"] == route])
agree("t_nextclade_vs_designation", ncp, des, qids)
x1, x1bad = agree("x_nextclade_vs_pangolin", ncp, pus, qids)
for k in t1bad[:10]:
    print("  T1 incompatible %-12s pangolin=%-14s designated=%-14s %s" % (k, pus(k), des(k), P["full"][k]["note"][:60]))

T1_FLOOR, X1_FLOOR = float(os.environ.get("T1_FLOOR", "0.95")), 0.80
check("T1", t1["compatible_rate"] is not None and t1["compatible_rate"] >= T1_FLOOR,
      "pangolin (UShER) hierarchy-compatible with the designation: %.4f (floor %.2f)" % (t1["compatible_rate"] or 0, T1_FLOOR))
check("X1", x1["compatible_rate"] is not None and x1["compatible_rate"] >= X1_FLOOR,
      "Nextclade hierarchy-compatible with pangolin: %.4f (floor %.2f)" % (x1["compatible_rate"] or 0, X1_FLOOR))

# ---------------- digests for the cross-chip comparison ----------------
def canon(rows, cols=None):
    h = hashlib.sha256()
    for line in sorted("\t".join(r[c] for c in (cols or list(r.keys()))) for r in rows):
        h.update(line.encode() + b"\n")
    return h.hexdigest()


calls = {
    "pangolin_all": canon(P["full"].values()),
    "pangolin_lineage": canon(P["full"].values(), ["taxon", "lineage"]),
    "pangolin_sub_t1_all": canon(P["sub_t1"].values()),
    "nextclade_all": canon(ncrows),
    "nextclade_pango": canon(ncrows, ["seqName", "Nextclade_pango"]),
}
with open(OUT + "/digests.tsv", "w") as fh:
    fh.write("".join("%s\t%s\n" % kv for kv in calls.items()))
res["digests"] = calls
res["failed"] = fails
json.dump(res, open(OUT + "/check.json", "w"), indent=1)
print("FAILED: %s" % fails if fails else "ALL CHECKS PASS")
sys.exit(1 if fails else 0)
