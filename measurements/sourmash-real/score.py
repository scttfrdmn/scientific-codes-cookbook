#!/usr/bin/env python3
"""sourmash-real: check one sample's sketch, gather and tax outputs, and write its fingerprints.

    python3 -u score.py <sample_dir> <acc> <zymo.tsv> <even|log>

Reads <dir>/<acc>.sig.zip (R1+R2 sketched together), <acc>.merged.sig.zip (R1 and R2 sketched
separately, then `sig merge`d), gather.csv, matches.zip and <acc>.summarized.csv. Writes
checks.txt, species.tsv and fingerprints.txt to the same directory. Exit 1 if any check fails.
"""
import csv, hashlib, re, sys
import sourmash

D, ACC, TRUTH, STD = sys.argv[1:5]
out, fails = [], 0


def check(name, ok, observed, want):
    global fails
    ok = bool(ok)
    fails += not ok
    line = "%s %-34s %s   want %s" % ("PASS" if ok else "FAIL", name, observed, want)
    out.append(line); print(line)


def load1(path):
    sigs = list(sourmash.load_file_as_signatures(path))
    assert len(sigs) == 1, (path, len(sigs))
    return sigs[0]


def content_sha(hashes):
    return hashlib.sha256("".join("%d\t%d\n" % (h, a) for h, a in sorted(hashes.items())).encode()).hexdigest()


# ---- the sketch: two routes to the same FracMinHash must give the same hashes and abundances
qs = load1("%s/%s.sig.zip" % (D, ACC))
q = qs.minhash
qh = dict(q.hashes)
m = load1("%s/%s.merged.sig.zip" % (D, ACC)).minhash
check("sketch(R1+R2) == merge(R1,R2)", dict(m.hashes) == qh,
      "%d hashes, %d weighted" % (len(qh), sum(qh.values())), "identical hash->abundance map")
SCALED = q.scaled

# ---- gather: a greedy partition of the query's hashes, replayed from the matched sketches
rows = list(csv.DictReader(open("%s/gather.csv" % D)))
rows.sort(key=lambda r: int(r["gather_result_rank"]))
check("gather ranks", [int(r["gather_result_rank"]) for r in rows] == list(range(len(rows))),
      "%d matches" % len(rows), "0..n-1")
r0 = rows[0]
check("query_n_hashes == sketch", int(r0["query_n_hashes"]) == len(qh), r0["query_n_hashes"], len(qh))
check("query_bp == hashes * scaled", int(r0["query_bp"]) == len(qh) * SCALED, r0["query_bp"], len(qh) * SCALED)
check("total_weighted_hashes == sum(abund)", int(r0["total_weighted_hashes"]) == sum(qh.values()),
      r0["total_weighted_hashes"], sum(qh.values()))

rem = int(r0["query_bp"]); chain = True
for r in rows:
    rem -= int(r["unique_intersect_bp"])
    chain &= rem == int(r["remaining_bp"])
check("remaining_bp chain", chain, "last %s" % rows[-1]["remaining_bp"], "query_bp - running sum(unique_intersect_bp)")

matches = {}
for s in sourmash.load_file_as_signatures("%s/matches.zip" % D):
    matches[s.md5sum()] = s.minhash
check("matches.zip holds every match", all(r["md5"] in matches for r in rows), len(matches), len(rows))
left = set(qh); replay = True; wsum = 0; cum_ok = True
for r in rows:
    mh = matches[r["md5"]]
    mine = left & set(mh.hashes)
    w = sum(qh[h] for h in mine)
    wsum += w
    replay &= len(mine) * SCALED == int(r["unique_intersect_bp"]) and w == int(r["n_unique_weighted_found"])
    cum_ok &= wsum == int(r["sum_weighted_found"])
    left -= mine
check("greedy replay, every row", replay, "%d rows" % len(rows), "|match & remaining| * scaled == unique_intersect_bp, abund == n_unique_weighted_found")
check("sum_weighted_found running total", cum_ok, rows[-1]["sum_weighted_found"], "running sum of n_unique_weighted_found")
union = set()
for mh in matches.values():
    union |= set(mh.hashes)
assigned = set(qh) & union
check("query & union(matches) == assigned", len(assigned) * SCALED == sum(int(r["unique_intersect_bp"]) for r in rows),
      len(assigned), "a hash in any chosen match was assigned to one")
f_w = sum(qh[h] for h in assigned) / sum(qh.values())

# ---- tax metagenome: a tree of fractions over the gather partition
tax = list(csv.DictReader(open("%s/%s.summarized.csv" % (D, ACC))))
ranks = []
for t in tax:
    if t["rank"] not in ranks:
        ranks.append(t["rank"])
worst = 0.0
for rk in ranks:
    s = sum(float(t["f_weighted_at_rank"]) for t in tax if t["rank"] == rk)
    worst = max(worst, abs(s - 1.0))
check("each rank's f_weighted sums to 1", worst <= 1e-9, "%d ranks, worst %.1e" % (len(ranks), worst), "<= 1e-9 (float sum)")
cls = sum(float(t["f_weighted_at_rank"]) for t in tax if t["rank"] == "species" and t["lineage"] != "unclassified")
check("classified species == gather's weighted cover", abs(cls - f_w) <= 1e-9, "%.9f vs %.9f" % (cls, f_w), "<= 1e-9")
tree = 0.0; parents = 0
for i, rk in enumerate(ranks[:-1]):
    child = ranks[i + 1]
    for t in tax:
        if t["rank"] != rk or t["lineage"] == "unclassified":
            continue
        kids = sum(float(c["f_weighted_at_rank"]) for c in tax if c["rank"] == child and c["lineage"].startswith(t["lineage"] + ";"))
        tree = max(tree, abs(kids - float(t["f_weighted_at_rank"]))); parents += 1
check("every clade == sum of its children", tree <= 1e-9, "%d parents, worst %.1e" % (parents, tree), "<= 1e-9")

# ---- the Zymo standard: a closed list with a published composition
truth = list(csv.DictReader(open(TRUTH), delimiter="\t"))
col = "even_pct" if STD == "even" else "log_pct"
must = "must_even" if STD == "even" else "must_log"
sp = {t["lineage"].split(";s__")[-1]: float(t["f_weighted_at_rank"]) for t in tax
      if t["rank"] == "species" and t["lineage"] != "unclassified"}


def found(names):
    pats = [re.compile("^" + re.escape(n) + "(_[A-Z]+)?$") for n in names.split(",")]
    return [s for s in sp if any(p.match(s) for p in pats)]


claimed = set(); req = []; lines = ["species\ttruth_pct\tsourmash_pct\tgtdb_species"]
for t in truth:
    hit = found(t["gtdb_names"])
    claimed.update(hit)
    pct = 100 * sum(sp[h] for h in hit)
    lines.append("%s\t%s\t%.6f\t%s" % (t["species"], t[col], pct, ",".join(hit) or "-"))
    if t[must] == "yes":
        req.append((t["species"], bool(hit)))
check("required Zymo species detected", all(ok for _, ok in req),
      "%d/%d" % (sum(ok for _, ok in req), len(req)), "all")
unexp = sorted(((v, s) for s, v in sp.items() if s not in claimed), reverse=True)
big = [(v, s) for v, s in unexp if v > 0.01]
check("unexpected species above 1%", not big, "%d (top: %s)" % (len(big), "; ".join("%s %.3f%%" % (s, 100 * v) for v, s in unexp[:3])), "0")
if STD == "log":
    top = sorted(sp.items(), key=lambda kv: -kv[1])[:2]
    lm, pa = found("Listeria monocytogenes"), found("Pseudomonas aeruginosa")
    check("top two == the 89.1% and 8.9% species", len(top) == 2 and top[0][0] in lm and top[1][0] in pa,
          ", ".join("%s %.2f%%" % (s, 100 * v) for s, v in top), "L. monocytogenes > P. aeruginosa > all")
for v, s in unexp:
    lines.append("(unexpected)\t-\t%.6f\t%s" % (100 * v, s))
lines.append("(unclassified)\t-\t%.6f\t-" % (100 * (1 - f_w)))
open("%s/species.tsv" % D, "w").write("\n".join(lines) + "\n")
print("\n".join(lines))

with open("%s/fingerprints.txt" % D, "w") as f:
    f.write("sketch_md5 %s content_sha256 %s hashes %d weighted %d\n" % (qs.md5sum(), content_sha(qh), len(qh), sum(qh.values())))
    for name in ("gather.csv", "%s.summarized.csv" % ACC):
        f.write("%s_sha256 %s\n" % (name, hashlib.sha256(open("%s/%s" % (D, name), "rb").read()).hexdigest()))
print(open("%s/fingerprints.txt" % D).read(), end="")
open("%s/checks.txt" % D, "w").write("\n".join(out) + "\n")
sys.exit(1 if fails else 0)
