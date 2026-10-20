#!/usr/bin/env python3
"""measurements/structural-variants-real 03-score, in aarch.bio's truvari 5.5.0 image (arm64; scoring
is not what is measured, so every chip's calls are scored on one box, by one truvari).

For each chip's manta and delly calls:
  - the recipe's SVLEN convention on every delly deletion (|SVLEN| = deleted length + 1);
  - truvari bench against GIAB HG002 Tier 1 v0.6 inside its BED, with GIAB's own published matching
    rules (README_SV_v0.6: --passonly --includebed, -r 2000, -p 0), identical for both callers, on
    their DEL and INS calls (the only types the benchmark holds);
  - the same with DUP added and read as INS (-d);
  - manta against delly directly: on precise deletion pairs, the deleted interval each claims.
Then the fixed points across chips. Writes /tmp/out/score.txt.
    score.py <chip> [<chip> ...]
"""
import bisect
import collections
import hashlib
import json
import os
import subprocess
import sys

import pysam

CHIPS = sys.argv[1:]
O = "/tmp/out"
W = "/var/tmp/score"
TRUTH, BED = "/tmp/HG002_SVs_Tier1_v0.6.vcf.gz", "/tmp/HG002_SVs_Tier1_v0.6.bed"
GIAB = ["--passonly", "-r", "2000", "-C", "2000", "-p", "0"]   # README_SV_v0.6's example, plus a chunk >= refdist
os.makedirs(O, exist_ok=True)
os.makedirs(W, exist_ok=True)
out, fails = [], []


def say(*a):
    line = "\t".join(str(x) for x in a)
    print(line, flush=True)
    out.append(line)


def check(ok, what):
    if not bool(ok):
        fails.append(what)
        say("FAIL", what)


def info1(rec, k):
    v = rec.info.get(k)
    return v[0] if isinstance(v, tuple) else v


def records_sha(path):
    h = hashlib.sha256()
    with pysam.VariantFile(path) as f:
        for r in f:
            h.update(("\t".join(str(r).split("\t")[:8]) + "\n").encode())
    return h.hexdigest()


def to_vcfgz(bcf, dst):
    with pysam.VariantFile(bcf) as f, pysam.VariantFile(dst, "wz", header=f.header) as g:
        for r in f:
            g.write(r)
    pysam.tabix_index(dst, preset="vcf", force=True)
    return dst


def bench(base, comp, name, extra):
    d = os.path.join(W, name)
    rc = subprocess.call(["truvari", "bench", "-b", base, "-c", comp, "-o", d] + extra,
                         stdout=open(d + ".log", "w"), stderr=subprocess.STDOUT)
    check(rc == 0, "truvari bench %s exited %d" % (name, rc))
    s = json.load(open(os.path.join(d, "summary.json")))
    by = {}
    for part in ("tp-base", "fn", "tp-comp", "fp"):
        c = collections.Counter()
        with pysam.VariantFile(os.path.join(d, part + ".vcf.gz")) as f:
            for r in f:
                c[info1(r, "SVTYPE") or "?"] += 1
        by[part] = c
    return s, by, d


# ---- the benchmark itself, counted independently of truvari ----
regions = collections.defaultdict(list)
for line in open(BED):
    c, a, b = line.split()[:3]
    regions[c].append((int(a), int(b)))
for c in regions:
    regions[c].sort()
starts = {c: [a for a, _ in v] for c, v in regions.items()}


def inside(chrom, start, end):   # both ends in one BED interval, the rule truvari's --includebed applies
    if chrom not in regions:
        return False
    i = bisect.bisect_right(starts[chrom], start) - 1
    return i >= 0 and regions[chrom][i][0] <= start and end <= regions[chrom][i][1]


nt = collections.Counter()
with pysam.VariantFile(TRUTH) as f:
    for r in f:
        if "PASS" not in r.filter.keys():
            continue
        t, size = info1(r, "SVTYPE"), abs(info1(r, "SVLEN") or 0)
        nt["pass_" + t] += 1
        if 50 <= size <= 50000 and inside(r.chrom, r.start, r.stop):
            nt["scored_" + t] += 1
say("truth_pass_DEL", nt["pass_DEL"])
say("truth_pass_INS", nt["pass_INS"])
say("truth_scored_DEL", nt["scored_DEL"])
say("truth_scored_INS", nt["scored_INS"])
say("bed_intervals", sum(len(v) for v in regions.values()))
say("bed_bases", sum(b - a for v in regions.values() for a, b in v))

def deleted(r, caller):
    """The deleted interval a DEL record claims, in each caller's own convention (the recipe's table):
    manta's END is the last deleted base; delly's explicit alleles end at the last deleted base too, but
    its symbolic <DEL> END is the base AFTER the deletion."""
    if caller == "delly" and r.alts[0].startswith("<"):
        return r.pos + 1, r.stop - 1
    return r.pos + 1, r.stop


def subset(src, dst, types):
    """PASS records of the types this benchmark can score at all: it holds only DEL and INS, so a BND or
    an INV can only ever be a false positive. Both callers get the same cut."""
    n = collections.Counter()
    with pysam.VariantFile(src) as f, pysam.VariantFile(dst, "wz", header=f.header) as g:
        for r in f:
            t = info1(r, "SVTYPE")
            if t in types:
                g.write(r)
            else:
                n[t] += 1
    pysam.tabix_index(dst, preset="vcf", force=True)
    return dst, n


per = {}
for chip in CHIPS:
    m = "/tmp/%s.diploidSV.vcf.gz" % chip
    d = to_vcfgz("/tmp/%s.delly.bcf" % chip, os.path.join(W, chip + ".delly.vcf.gz"))
    k = {"manta_records_sha256": records_sha(m), "delly_records_sha256": records_sha(d)}

    # delly's convention, on every deletion: |SVLEN| is the deleted length PLUS ONE (the recipe's
    # 1001 for a planted 1000), whichever way the record is written.
    dc = collections.Counter()
    with pysam.VariantFile(d) as f:
        for r in f:
            t = info1(r, "SVTYPE")
            dc["calls_" + t] += 1
            if "PASS" in r.filter.keys():
                dc["pass_" + t] += 1
            if t == "DEL":
                a, b = deleted(r, "delly")
                dc["del_symbolic" if r.alts[0].startswith("<") else "del_explicit"] += 1
                dc["del_svlen_mismatch"] += int(abs(info1(r, "SVLEN")) != b - a + 2)
    for x in sorted(dc):
        k["delly_" + x] = dc[x]
    check(dc["del_svlen_mismatch"] == 0, "%s: a delly DEL whose |SVLEN| is not its deleted length + 1" % chip)

    for caller, path in (("manta", m), ("delly", d)):
        for mode, types, extra in (("giab", ("DEL", "INS"), []), ("dup2ins", ("DEL", "INS", "DUP"), ["-d"])):
            name = "%s.%s.%s" % (chip, caller, mode)
            c, cut = subset(path, os.path.join(W, name + ".comp.vcf.gz"), types)
            s, by, _ = bench(TRUTH, c, name, ["--includebed", BED] + GIAB + extra)
            p = "%s_%s_" % (caller, mode)
            k[p + "not_scorable"] = ",".join("%s:%d" % kv for kv in sorted(cut.items()))
            for x in ("base cnt", "comp cnt", "TP-base", "TP-comp", "FP", "FN"):
                k[p + x.replace(" ", "_")] = s[x]
            for x in ("precision", "recall", "f1"):
                k[p + x] = "%.4f" % s[x]
            for t in ("DEL", "INS"):
                tp, fn = by["tp-base"][t], by["fn"][t]
                k[p + "recall_" + t] = "%.4f" % (tp / (tp + fn) if tp + fn else 0)
            k[p + "fp_types"] = ",".join("%s:%d" % kv for kv in sorted(by["fp"].items()))
            # The one claim about accuracy: a working caller's PASS calls inside the benchmark's confident
            # regions are mostly real. Recall is reported, never asserted: the gap between the two
            # callers is the finding, and a shared floor would be tuned to one of them.
            check(s["precision"] >= 0.8, "%s: precision %.4f < 0.8" % (name, s["precision"]))
            # truvari's base count must be the independent count of the benchmark, per type
            check(by["tp-base"]["DEL"] + by["fn"]["DEL"] == nt["scored_DEL"]
                  and by["tp-base"]["INS"] + by["fn"]["INS"] == nt["scored_INS"],
                  "%s: truvari scored a different benchmark than the BED holds" % name)
            check(s["TP-base"] + s["FN"] == s["base cnt"] and s["TP-comp"] + s["FP"] == s["comp cnt"],
                  "%s: truvari's counts do not add up" % name)

    # manta against delly, genome-wide, PASS calls >= 50 bp, same matching rules. truvari's MatchId
    # pairs a tp-base record (manta) with its tp-comp (delly). On precise deletion pairs, the claim is
    # the deleted interval; the raw END offset follows from how delly wrote the record.
    _, _, dd = bench(m, d, chip + ".manta_vs_delly", GIAB + ["-S", "50"])
    pair = {}
    for part, who in (("tp-base", "manta"), ("tp-comp", "delly")):
        with pysam.VariantFile(os.path.join(dd, part + ".vcf.gz")) as f:
            for r in f:
                if info1(r, "SVTYPE") == "DEL" and "IMPRECISE" not in r.info.keys():
                    pair.setdefault(info1(r, "MatchId"), {})[who] = (deleted(r, who), r.stop, r.alts[0].startswith("<"))
    same, diff, off = 0, 0, collections.Counter()
    for v in pair.values():
        if "manta" in v and "delly" in v:
            if v["manta"][0] == v["delly"][0]:
                same += 1
                off[("symbolic" if v["delly"][2] else "explicit", v["delly"][1] - v["manta"][1])] += 1
            else:
                diff += 1
    k["precise_del_pairs"] = same + diff
    k["precise_del_pairs_same_interval"] = same
    k["same_interval_raw_end_offset"] = ",".join("delly-%s:%+d:%d" % (a, b, n) for (a, b), n in sorted(off.items()))
    check(same > diff, "%s: precise deletion pairs mostly disagree on the deleted interval" % chip)
    check(all(b == (1 if a == "symbolic" else 0) for a, b in off), "%s: an END offset other than the convention's" % chip)
    for f in os.listdir(W):
        if os.path.isdir(os.path.join(W, f)):
            os.makedirs(os.path.join(O, "truvari", f), exist_ok=True)
            subprocess.call(["cp", os.path.join(W, f, "summary.json"), os.path.join(O, "truvari", f)])
            subprocess.call(["cp", os.path.join(W, f + ".log"), os.path.join(O, "truvari", f)])
    per[chip] = k
    for x in sorted(k):
        say(chip, x, k[x])

# ---- across chips: every value above is a fixed point of the same bytes ----
if "arm64" in per:
    for chip in per:
        if chip == "arm64":
            continue
        for x in sorted(per["arm64"]):
            a, b = per["arm64"][x], per[chip].get(x)
            say("crosschip", chip, x, "same" if a == b else "differs: %s vs Graviton4 %s" % (b, a))

say("SCORE CHECKS PASSED" if not fails else "SCORE CHECKS FAILED: %d" % len(fails))
open(os.path.join(O, "score.txt"), "w").write("\n".join(out) + "\n")
sys.exit(1 if fails else 0)
