#!/usr/bin/env python3
"""Score one kraken2 run of a ZymoBIOMICS mock: identities first, then the published composition.

    python3 -u score.py <report> <kraken2.log> <per-read-tally> <ENA read_count> <ENA base_count> \
                        <even|log> <zymo.tsv> <outdir>

Writes <outdir>/checks.txt and <outdir>/score.tsv; exits non-zero if any check fails.
  * input      pairs processed == ENA read_count, and the Mbp kraken2 prints == ENA base_count
  * books      classified + unclassified == processed; the report's root and unclassified rows
               are the log's two counts; taxon_reads over every row sums to processed
  * tree       every clade == its own taxon_reads + the sum of its children's clade_reads
  * two paths  the per-read output, tallied by taxid, equals the report's taxon_reads for every
               taxid -- the same classification counted by two different writers
  * truth      the published composition (see zymo.tsv), scored at genus rank
"""
import re
import sys

rep_p, log_p, tally_p, want_pairs, want_bases, kind, truth_p, outdir = sys.argv[1:9]
want_pairs, want_bases = int(want_pairs), int(want_bases)
lines, FAILS = [], []


def check(ok, name, want, got):
    ok = bool(ok)
    lines.append("%-52s %-34s %s" % (name, want, ("ok   (%s)" if ok else "FAIL (%s)") % (got,)))
    if not ok:
        FAILS.append(name)


# --- kraken2.log: "N sequences (X Mbp) processed in ...", then classified / unclassified
log = open(log_p).read()
m = re.search(r"(\d+) sequences \(([\d.]+) Mbp\) processed", log)
proc, mbp = (int(m.group(1)), m.group(2)) if m else (-1, "?")
cl = re.search(r"(\d+) sequences classified", log)
un = re.search(r"(\d+) sequences unclassified", log)
ncl, nun = (int(cl.group(1)) if cl else -1), (int(un.group(1)) if un else -1)
check(proc == want_pairs, "pairs processed == ENA read_count", "want %d" % want_pairs, proc)
check(mbp == "%.2f" % (want_bases / 1e6), "Mbp processed == ENA base_count", "want %.2f" % (want_bases / 1e6), mbp)
check(ncl + nun == proc, "classified + unclassified == processed", "want %d" % proc, ncl + nun)

# --- the report: pct, clade_reads, taxon_reads, rank, taxid, name (indented 2 spaces per level)
rows = []
for line in open(rep_p):
    f = line.rstrip("\n").split("\t")
    if len(f) < 6:
        continue
    name = f[5]
    depth = (len(name) - len(name.lstrip(" "))) // 2
    rows.append(dict(clade=int(f[1]), taxon=int(f[2]), rank=f[3], taxid=f[4], name=name.strip(), depth=depth))
by_id = {r["taxid"]: r for r in rows}
check(len(by_id) == len(rows), "report taxids unique", "want %d" % len(rows), len(by_id))
check(by_id.get("0", {}).get("clade") == nun, "report unclassified row == log unclassified", "want %d" % nun,
      by_id.get("0", {}).get("clade"))
check(by_id.get("1", {}).get("clade") == ncl, "report root clade == log classified", "want %d" % ncl,
      by_id.get("1", {}).get("clade"))
check(sum(r["taxon"] for r in rows) == proc, "sum(taxon_reads) over all rows == processed", "want %d" % proc,
      sum(r["taxon"] for r in rows))

# tree: walk with a stack of open ancestors; each row's children are the following rows one deeper
kids = {i: 0 for i in range(len(rows))}
stack = []
for i, r in enumerate(rows):
    while stack and rows[stack[-1]]["depth"] >= r["depth"]:
        stack.pop()
    if stack:
        kids[stack[-1]] += r["clade"]
    stack.append(i)
bad = [r["taxid"] for i, r in enumerate(rows) if r["taxid"] != "0" and r["clade"] != r["taxon"] + kids[i]]
check(not bad, "every clade == taxon + sum(children)", "%d clades, want 0 off" % (len(rows) - 1),
      "%d off%s" % (len(bad), (": " + ",".join(bad[:5])) if bad else ""))

# --- the second writer: per-read lines tallied by status and by taxid
status, tally = {}, {}
for line in open(tally_p):
    f = line.split()
    if f[0] == "status":
        status[f[1]] = int(f[2])
    elif f[0] == "taxid":
        tally[f[1]] = int(f[2])
check(status.get("C") == ncl and status.get("U") == nun and len(status) == 2,
      "per-read lines: C == classified, U == unclassified", "want %d / %d" % (ncl, nun), status)
rep_taxon = {r["taxid"]: r["taxon"] for r in rows if r["taxon"] > 0 and r["taxid"] != "0"}
off = sorted(set(rep_taxon) ^ set(tally)) + [t for t in rep_taxon if t in tally and tally[t] != rep_taxon[t]]
check(not off, "per-read tally == report taxon_reads, every taxid", "%d taxids, want 0 off" % len(rep_taxon),
      "%d off" % len(off))

# --- truth, observed: the published composition at genus rank
truth = [l.rstrip("\n").split("\t") for l in open(truth_p)][1:]
col = 3 if kind == "even" else 4
genera = {r["name"]: r for r in rows if r["rank"] == "G"}
out = ["genus\tdna_pct\tin_db\tclade_reads\tpct_of_processed\tpct_of_classified"]
for g, sp, king, e, lg, indb in truth:
    r = genera.get(g, {"clade": 0})
    out.append("%s\t%s\t%s\t%d\t%.4f\t%.4f" % (g, (e, lg)[col - 3], indb, r["clade"],
                                              100.0 * r["clade"] / proc, 100.0 * r["clade"] / max(ncl, 1)))
expected = {t[0] for t in truth}
top_g = sorted(genera.values(), key=lambda r: -r["clade"])
out.append("# top genera by clade reads (* = not in the mock)")
for r in top_g[:20]:
    out.append("#%s %s\t%d\t%.4f" % ("" if r["name"] in expected else "*", r["name"], r["clade"],
                                     100.0 * r["clade"] / max(ncl, 1)))
top_s = sorted((r for r in rows if r["rank"] == "S"), key=lambda r: -r["clade"])
out.append("# top species by clade reads")
for r in top_s[:20]:
    out.append("# %s (%s)\t%d\t%.4f" % (r["name"], r["taxid"], r["clade"], 100.0 * r["clade"] / max(ncl, 1)))

# --- truth, asserted at genus rank: a closed list, scored against what the standard DB can call.
# The two yeasts are not in standard-8 (archaea, bacteria, viral, plasmid, human, UniVec_Core), so
# the claims are about the 8 bacteria. Species rank is reported, not asserted: Zymo's B. subtilis
# strain is B. spizizenii in the current NCBI taxonomy, and E. coli / Salmonella share k-mers that
# kraken2 resolves only to their family.
callable = [t[0] for t in truth if t[5] == "yes"]
others = [r for r in top_g if r["name"] not in expected]
big = others[0] if others else {"name": "-", "clade": 0}
unexp = [r for r in others if r["clade"] > 0.01 * ncl]
check(not unexp, "unexpected genera above 1% of classified", "want 0 (the closed list)",
      "%d; largest %s %.4f%%" % (len(unexp), big["name"], 100.0 * big["clade"] / max(ncl, 1)))
if kind == "even":
    got = sorted(r["name"] for r in top_g[:len(callable)])
    check(got == sorted(callable), "top 8 genera == the 8 Zymo bacteria", "want the D6300 list",
          ",".join(r["name"] for r in top_g[:len(callable)]))
else:
    ladder = ["Listeria", "Pseudomonas", "Bacillus"]  # 89.1%, 8.9%, 0.89%: the first three decades
    got = [r["name"] for r in top_g[:3]]
    check(got == ladder, "top 3 genera, in order == the D6310 ladder", "want " + ">".join(ladder), ">".join(got))

hs = by_id.get("9606", {"clade": 0})["clade"]
out.append("# Homo sapiens clade reads\t%d" % hs)
open(outdir + "/score.tsv", "w").write("\n".join(out) + "\n")

lines.append("failures %d" % len(FAILS))
open(outdir + "/checks.txt", "w").write("\n".join(lines) + "\n")
print("\n".join(lines))
print("\n".join(out))
sys.exit(1 if FAILS else 0)
