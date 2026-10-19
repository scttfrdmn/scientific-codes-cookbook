#!/usr/bin/env python3
"""The humann-real identities, on one HUMAnN run of a ZymoBIOMICS D6300 mock.

    python3 -u check.py <outdir> <input reads> <truth.tsv>

Reads <outdir>/<acc>_{genefamilies,pathabundance,pathcoverage}.tsv, humann.stdout and
custom_db_species.txt; writes checks.txt and species.tsv; exits non-zero if any check fails.
Every check is an identity, a structural property or a closed list -- no band on an abundance:
  * read accounting   UNMAPPED == input reads x HUMAnN's printed final unaligned fraction
  * the tree          every gene family's community value == the sum of its strata, exactly as printed
  * the guided path   every stratum is a species HUMAnN put in its custom database from the profile,
                      and every Zymo bacterium is in that database and recruits gene families
"""
import glob
import os
import re
import sys
from decimal import Decimal

out, n_reads, truth_p = sys.argv[1], int(sys.argv[2]), sys.argv[3]
FAILS, lines = [], []


def check(ok, name, want, got):
    ok = bool(ok)
    lines.append("%-52s %-34s %s" % (name, want, ("ok   (%s)" if ok else "FAIL (%s)") % got))
    if not ok:
        FAILS.append(name)


def half_ulp(s):
    """half a unit in the last place HUMAnN printed (it prints fixed decimals)"""
    d = Decimal(s)
    return Decimal(5) * Decimal(10) ** (d.as_tuple().exponent - 1)


def table(suffix):
    p = glob.glob(os.path.join(out, "*_%s.tsv" % suffix))[0]
    rows = []
    for l in open(p):
        if l.startswith("#"):
            continue
        k, v = l.rstrip("\n").split("\t")
        rows.append((k, v))
    return rows


stdout = open(os.path.join(out, "humann.stdout")).read()

# --- read accounting. HUMAnN's UNMAPPED is a read count, and it prints the unaligned fraction of
# the reads it counted in the input; the two must agree on OUR count of the input.
gf = table("genefamilies")
keys = [k for k, _ in gf]
check(len(set(keys)) == len(keys), "gene-family rows unique", "want %d" % len(keys), len(set(keys)))
gfd = dict(gf)
unm = Decimal(gfd.get("UNMAPPED", "-1"))
pct = re.findall(r"Unaligned reads after (nucleotide|translated) alignment: ([0-9.]+) %", stdout)
pcts = dict(pct)
check("nucleotide" in pcts and "translated" in pcts, "both unaligned fractions printed", "nucleotide, translated", sorted(pcts))
if "translated" in pcts:
    p2 = Decimal(pcts["translated"])
    expect = Decimal(n_reads) * p2 / 100
    tol = Decimal(n_reads) * half_ulp(pcts["translated"]) / 100
    check(abs(unm - expect) <= tol, "UNMAPPED == reads x final unaligned %", "tol %.1e (print precision)" % tol,
          "%s vs %.4f" % (unm, expect))
    check(Decimal(pcts["translated"]) <= Decimal(pcts["nucleotide"]) <= 100,
          "unaligned: translated <= nucleotide <= 100%", "monotone", "%s <= %s" % (pcts["translated"], pcts["nucleotide"]))
check(unm == unm.to_integral_value() and unm >= 0, "UNMAPPED is a whole read count", "integer", unm)

# --- the tree: community == sum of strata, in exact decimal arithmetic on the printed values
comm, strata = {}, {}
for k, v in gf:
    if k == "UNMAPPED":
        continue
    if "|" in k:
        fam, taxon = k.split("|", 1)
        strata.setdefault(fam, []).append((taxon, v))
    else:
        comm[k] = v
orphans = [f for f in strata if f not in comm]
bare = [f for f in comm if f not in strata]
check(not orphans, "every stratified family has a community row", "0 orphans", len(orphans))
check(not bare, "every community row has strata", "0 without", len(bare))
worst, bad = Decimal(0), 0
for fam, v in comm.items():
    s = sum((Decimal(x) for _, x in strata.get(fam, [])), Decimal(0))
    tol = half_ulp(v) + sum((half_ulp(x) for _, x in strata.get(fam, [])), Decimal(0))
    d = abs(Decimal(v) - s)
    worst = max(worst, d)
    bad += d > tol
check(bad == 0, "family == sum of its strata (all families)", "within print precision", "%d families, worst %.1e" % (len(comm), worst))

# --- the taxonomy-guided path
db_species = {}
for l in open(os.path.join(out, "custom_db_species.txt")):
    c, sp = l.split()
    db_species[sp] = int(c)
taxa = {t for v in strata.values() for t, _ in v}
named = taxa - {"unclassified"}
check(db_species and len(db_species) < 200, "custom database is the profile's species, not all of ChocoPhlAn", "few pangenomes", "%d species" % len(db_species))
check(named <= set(db_species), "every stratum is a species in the custom database", "subset", "%d of %d" % (len(named & set(db_species)), len(named)))

truth = [l.rstrip("\n").split("\t") for l in open(truth_p)][1:]
per_species = {}
for fam, v in strata.items():
    for t, x in v:
        per_species[t] = per_species.get(t, Decimal(0)) + Decimal(x)
total_rpk = sum(per_species.values(), Decimal(0))
rows, found = [], 0
for sp, pct_dna, kingdom, alt in truth:
    names = {sp} | ({alt} if alt != "-" else set())
    hit = [t for t in db_species if t.split(".s__")[-1] in names or any(t.split(".s__")[-1].startswith(n + "_") for n in names)]
    rpk = sum((per_species.get(t, Decimal(0)) for t in hit), Decimal(0))
    fams = sum(1 for v in strata.values() for t, _ in v if t in hit)
    rows.append("%s\t%s\t%s\t%s\t%d\t%.4f" % (sp, pct_dna, ",".join(sorted(hit)) or "-", kingdom, fams, 100 * rpk / total_rpk if total_rpk else 0))
    if kingdom == "Bacteria":
        found += bool(hit) and fams > 0
nbact = sum(1 for r in truth if r[2] == "Bacteria")
check(found == nbact, "every Zymo bacterium in the database recruits families", "want %d/%d" % (nbact, nbact), "%d/%d" % (found, nbact))
with open(os.path.join(out, "species.tsv"), "w") as fh:
    fh.write("species\tdna_pct\tchocophlan_name\tkingdom\tstratified_families\tpct_of_stratified_rpk\n")
    fh.write("\n".join(rows) + "\n")

# --- pathways: coverage is a fraction, and both tables carry HUMAnN's accounting rows
cov = table("pathcoverage")
vals = [Decimal(v) for _, v in cov]
check(all(0 <= x <= 1 for x in vals), "pathway coverage in [0, 1]", "all rows", "%d rows" % len(vals))
pa = dict(table("pathabundance"))
check("UNMAPPED" in pa and "UNINTEGRATED" in pa, "pathabundance has UNMAPPED, UNINTEGRATED", "present", "%d rows" % len(pa))
npw = len([k for k in pa if "|" not in k and k not in ("UNMAPPED", "UNINTEGRATED")])
lines.append("%-52s %-34s %s" % ("observed: community pathways", "-", npw))
lines.append("%-52s %-34s %s" % ("observed: community gene families", "-", len(comm)))
lines.append("%-52s %-34s %s" % ("observed: unaligned after nucleotide / translated", "-", "%s / %s %%" % (pcts.get("nucleotide"), pcts.get("translated"))))

with open(os.path.join(out, "checks.txt"), "w") as fh:
    fh.write("\n".join(lines) + "\nfailures %d\n" % len(FAILS))
print("\n".join(lines))
print(open(os.path.join(out, "species.tsv")).read())
print("failures %d" % len(FAILS))
sys.exit(1 if FAILS else 0)
