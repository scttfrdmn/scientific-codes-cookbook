#!/usr/bin/env python3
"""Score a MetaPhlAn profile of a ZymoBIOMICS mock: the recipe's checks, carried to a whole run.

    python3 -u score.py <profile.txt> <truth.tsv> <expected reads> <outdir>

Writes <outdir>/score.tsv and <outdir>/checks.txt; exits non-zero if any check fails.
Every check is an identity or a closed list, not a band:
  * reads processed     == the archive's own read count (nothing dropped, nothing doubled)
  * the closed species list: all 8 Zymo bacteria present, unexpected >1% at most 2 (the recipe's rule)
  * conservation        UNCLASSIFIED + kingdoms == 100, every rank sums to the same total, and every
                        clade == the sum of its children -- to the precision MetaPhlAn prints
"""
import sys

prof_p, truth_p, want_reads, outdir = sys.argv[1], sys.argv[2], int(sys.argv[3]), sys.argv[4]
FAILS = []
lines = []


def check(ok, name, want, got):
    ok = bool(ok)
    lines.append("%-44s %-30s %s" % (name, want, ("ok   (%s)" if ok else "FAIL (%s)") % got))
    if not ok:
        FAILS.append(name)


header, rows = [], []
for line in open(prof_p):
    if line.startswith("#"):
        header.append(line.rstrip("\n"))
        continue
    f = line.rstrip("\n").split("\t")
    if len(f) >= 3:
        rows.append((f[0], f[2]))

# --- reads processed: MetaPhlAn's own header line
proc = [h for h in header if h.endswith(" reads processed")]
got = int(proc[0][1:].split()[0]) if proc else -1
check(got == want_reads, "reads processed == archive read count", "want %d" % want_reads, got)

# --- conservation. Each value is printed with a fixed number of decimals, so the tolerance is the
# rounding of the terms summed: half a unit in the last printed place, per term.
def ulp(s):
    return 0.5 * 10 ** -(len(s.split(".")[1]) if "." in s else 0)


ab = {c: (float(v), ulp(v)) for c, v in rows}
check(len(ab) == len(rows), "clade names unique", "want %d" % len(rows), len(ab))
unc = ab.get("UNCLASSIFIED", (0.0, 0.0))
kings = [c for c in ab if c.startswith("k__") and "|" not in c]
tot = unc[0] + sum(ab[k][0] for k in kings)
tol = unc[1] + sum(ab[k][1] for k in kings)
check(abs(tot - 100) <= tol, "UNCLASSIFIED + kingdoms == 100", "tol %.1e (print precision)" % tol, "%.6f" % tot)

ktot = sum(ab[k][0] for k in kings)
worst_rank = 0.0
bad_rank = 0
by_depth = {}
for c in ab:
    if c != "UNCLASSIFIED":
        by_depth.setdefault(c.count("|"), []).append(c)
# A rank sums to the kingdom total only while every lineage reaches it; MetaPhlAn's SGB rows (t__)
# exist only for some species, so the check is per parent, which also covers the ranks.
worst_child = 0.0
bad_child = 0
nparents = 0
for c, (a, u) in ab.items():
    if c == "UNCLASSIFIED":
        continue
    d = c.count("|")
    kids = [k for k in by_depth.get(d + 1, []) if k.startswith(c + "|")]
    if not kids:
        continue
    nparents += 1
    s = sum(ab[k][0] for k in kids)
    t = u + sum(ab[k][1] for k in kids)
    worst_child = max(worst_child, abs(a - s))
    if abs(a - s) > t:
        bad_child += 1
check(bad_child == 0, "every clade == sum of its children", "%d parents, within print precision" % nparents,
      "%d off, worst %.2e" % (bad_child, worst_child))
for d in sorted(by_depth):
    s = sum(ab[c][0] for c in by_depth[d])
    lines.append("  rank depth %d: %3d clades, sum %.6f (kingdoms %.6f)" % (d, len(by_depth[d]), s, ktot))

# --- the closed species list. A 5th truth column `must_detect` (yes/no) says which species the
# claim requires; without it every bacterium is required (the recipe's D6300 rule).
truth = []
for line in open(truth_p):
    f = line.rstrip("\n").split("\t")
    if f[0] in ("species", ""):
        continue
    must = f[4] == "yes" if len(f) > 4 else f[2] == "Bacteria"
    truth.append((f[0], float(f[1]), f[2], f[3], must))
obs = {}
for c, v in rows:
    if "|s__" in c and "|t__" not in c:
        n = c.split("|s__")[-1]
        obs[n] = obs.get(n, 0.0) + float(v)
names = set()
out = []
for sp, pct, k, alt, must in truth:
    names.add(sp)
    if alt != "-":
        names.add(alt)
    hit = next((x for x in (sp, alt) if x != "-" and x in obs), None)
    out.append((sp, k, pct, hit, obs.get(hit, 0.0), must))
req = [r for r in out if r[5]]
found = [r for r in req if r[3]]
btot = sum(r[4] for r in out if r[1] == "Bacteria" and r[3])
extra = sorted(((n, a) for n, a in obs.items() if n not in names and a >= 1.0), key=lambda x: -x[1])
minor = sorted(((n, a) for n, a in obs.items() if n not in names and a < 1.0), key=lambda x: -x[1])
with open(outdir + "/score.tsv", "w") as fh:
    fh.write("expected_species\tkingdom\tzymo_dna_pct\tmust_detect\tdetected_as\tmetaphlan_pct\tshare_of_bacteria\n")
    for sp, k, pct, hit, a, must in out:
        fh.write("%s\t%s\t%g\t%s\t%s\t%.4f\t%.2f\n" % (sp, k, pct, "yes" if must else "no", hit or "NOT_FOUND", a,
                                                       100 * a / btot if k == "Bacteria" and btot else 0))
    for tag, lst in (("UNEXPECTED", extra), ("unexpected_below_1pct", minor)):
        for n, a in lst:
            fh.write("%s\t-\t0\tno\t%s\t%.4f\t%.2f\n" % (tag, n, a, 100 * a / btot if btot else 0))
check(len(found) == len(req), "required Zymo species detected", "want %d/%d" % (len(req), len(req)),
      "%d/%d" % (len(found), len(req)))
check(len(extra) <= 2, "unexpected species above 1%", "want <=2 (recipe's rule)",
      "%d %s" % (len(extra), ",".join(n for n, _ in extra)))
lines.append("  species rows %d, unexpected below 1%%: %d %s" % (len(obs), len(minor),
             ",".join("%s=%.4f" % x for x in minor[:6])))

with open(outdir + "/checks.txt", "w") as fh:
    fh.write("\n".join(lines) + "\n")
sys.stdout.write("\n".join(lines) + "\n")
sys.stdout.write(open(outdir + "/score.tsv").read())
if FAILS:
    sys.exit("FAIL: " + "; ".join(FAILS))
print("SCORE OK")
