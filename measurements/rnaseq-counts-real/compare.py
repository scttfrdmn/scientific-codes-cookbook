"""measurements/rnaseq-counts-real: set HTSeq's counts against featureCounts' and STAR's on the
same alignment bytes, and check each tool's own accounting.
    python3 -u compare.py <input dir> <output dir>
Writes check.txt (key, value, what it is held to) and htseq_counts.tsv. Exits 1 on a failed
assertion; an OBSERVATION never fails."""
import hashlib
import os
import sys

I, O = sys.argv[1], sys.argv[2]
rows, bad = [], []


def rec(key, val, note=""):
    rows.append("%-30s %s   %s" % (str(key), str(val), str(note)))
    print(rows[-1])


def need(ok, key, val, note):
    rec(key, val, note)
    if not bool(ok):
        bad.append(key)


def table(path, col=1, skip=0):
    d = {}
    with open(path) as fh:
        for i, line in enumerate(fh):
            if i < skip:
                continue
            f = line.rstrip("\n").split("\t")
            if f[0] in d:
                raise SystemExit("duplicate id %s in %s" % (f[0], path))
            d[f[0]] = int(f[col])
    return d


def star_log(key):
    for line in open(os.path.join(I, "star.Log.final.out")):
        if "|" in line and line.split("|")[0].strip() == key:
            return int(line.split("|")[1].strip())
    raise SystemExit("no %r in STAR log" % key)


hts_all = table(os.path.join(O, "htseq.txt"))
hts = {k: v for k, v in hts_all.items() if not k.startswith("__")}
hspec = {k: v for k, v in hts_all.items() if k.startswith("__")}
fc = table(os.path.join(I, "fc_counts.tsv"))
fcs = table(os.path.join(I, "fc.summary"), skip=1)
rpg = os.path.join(I, "ReadsPerGene.out.tab")
star = {c: {k: v for k, v in table(rpg, c).items() if not k.startswith("N_")} for c in (1, 2, 3)}
sspec = {k: v for k, v in table(rpg, 1).items() if k.startswith("N_")}

with open(os.path.join(O, "htseq_counts.tsv"), "w") as fh:
    for k in sorted(hts):
        fh.write("%s\t%d\n" % (k, hts[k]))
hsha = hashlib.sha256(open(os.path.join(O, "htseq_counts.tsv"), "rb").read()).hexdigest()

IN = star_log("Number of input reads")
UNIQ = star_log("Uniquely mapped reads number")
MULTI = star_log("Number of reads mapped to multiple loci")
MANY = star_log("Number of reads mapped to too many loci")
rec("star_input_pairs", IN)
rec("star_unique/multi/too_many", "%d/%d/%d" % (UNIQ, MULTI, MANY))

# ---- each tool's own accounting ----
need(sum(hts_all.values()) == IN, "htseq_accounting_sum", sum(hts_all.values()),
     "want == star_input_pairs (EXACT: every pair once)")
for k in sorted(hspec):
    rec("htseq" + k, hspec[k])
for c, name in ((1, "unstranded"), (2, "stranded_fwd"), (3, "stranded_rev")):
    tot = sum(table(rpg, c).values())   # the four N_ rows plus every gene
    need(tot == IN, "star_col%d_sum_%s" % (c + 1, name), tot, "want == star_input_pairs (EXACT)")
for k in sorted(sspec):
    rec("star_" + k, sspec[k], "(column 2)")
need(hspec.get("__not_aligned", -1) == IN - UNIQ - MULTI, "htseq_not_aligned_vs_star", hspec.get("__not_aligned"),
     "want == star unmapped + too-many-loci %d (EXACT)" % (IN - UNIQ - MULTI))
need(hspec.get("__alignment_not_unique", -1) == MULTI, "htseq_not_unique_vs_star", hspec.get("__alignment_not_unique"),
     "want == star multi-loci %d (EXACT)" % MULTI)
uniq_h = sum(hts.values()) + hspec.get("__no_feature", 0) + hspec.get("__ambiguous", 0) + hspec.get("__too_low_aQual", 0)
need(uniq_h == UNIQ, "htseq_unique_classes", uniq_h, "genes+no_feature+ambiguous+too_low_aQual, want == star unique %d (EXACT)" % UNIQ)

# ---- the same gene set from all three ----
need(set(hts) == set(fc) == set(star[1]), "gene_sets_equal", "%d/%d/%d" % (len(hts), len(fc), len(star[1])),
     "htseq/featureCounts/STAR, want identical sets")
genes = sorted(set(hts) & set(fc) & set(star[1]))


def agree(a, b, label, assert_exact):
    diff = [g for g in genes if a[g] != b[g]]
    absd = sum(abs(a[g] - b[g]) for g in genes)
    tot = sum(a[g] for g in genes)
    note = "want 0 (EXACT)" if assert_exact else "OBSERVATION"
    need((len(diff) == 0) or not assert_exact, label + "_genes_differing", len(diff), note)
    rec(label + "_sum_abs_diff", absd, "of %d counted by the first" % tot)
    if diff:
        worst = max(diff, key=lambda g: abs(a[g] - b[g]))
        rec(label + "_worst_gene", "%s %d vs %d" % (worst, a[worst], b[worst]))
    return diff


def ranks(x):
    order = sorted(range(len(x)), key=lambda i: x[i])
    r = [0.0] * len(x)
    i = 0
    while i < len(x):
        j = i
        while j + 1 < len(x) and x[order[j + 1]] == x[order[i]]:
            j += 1
        for k in range(i, j + 1):
            r[order[k]] = (i + j) / 2.0 + 1
        i = j + 1
    return r


def spearman(a, b):
    g = [k for k in genes if a[k] > 0 or b[k] > 0]
    ra, rb = ranks([a[k] for k in g]), ranks([b[k] for k in g])
    n = len(g)
    ma, mb = sum(ra) / n, sum(rb) / n
    cov = sum((x - ma) * (y - mb) for x, y in zip(ra, rb))
    va = sum((x - ma) ** 2 for x in ra)
    vb = sum((y - mb) ** 2 for y in rb)
    return (cov / (va * vb) ** 0.5 if va > 0 and vb > 0 else float("nan")), n


# HTSeq (-s no, union, --nonunique none) and STAR's unstranded GeneCounts column: the same rule,
# so the same number for every gene and every unassigned class.
agree(hts, star[1], "htseq_vs_star", True)
for h, n in (("__no_feature", "N_noFeature"), ("__ambiguous", "N_ambiguous"),
             ("__alignment_not_unique", "N_multimapping"), ("__not_aligned", "N_unmapped")):
    need(hspec.get(h) == sspec.get(n), "htseq%s_vs_star_%s" % (h, n), "%s %s" % (hspec.get(h), sspec.get(n)),
         "want equal (EXACT)")

# featureCounts differs, and only in one direction: it assigns some pairs HTSeq calls ambiguous.
# So no gene has fewer counts in featureCounts, the no-feature class is the same, and the whole
# difference is the ambiguity difference.
diff = agree(fc, hts, "featurecounts_vs_htseq", False)
need(all(fc[g] > hts[g] for g in diff), "featurecounts_above_htseq", sum(1 for g in diff if fc[g] > hts[g]),
     "of %d differing genes, want all (EXACT: featureCounts never counts fewer)" % len(diff))
need(fcs["Unassigned_NoFeatures"] == hspec.get("__no_feature"), "no_feature_fc_vs_htseq",
     "%d %s" % (fcs["Unassigned_NoFeatures"], hspec.get("__no_feature")), "want equal (EXACT)")
extra = sum(fc[g] - hts[g] for g in genes)
amb = hspec.get("__ambiguous", 0) - fcs["Unassigned_Ambiguity"]
need(extra == amb, "fc_extra_vs_ambiguity", "%d %d" % (extra, amb),
     "sum(fc - htseq) == htseq __ambiguous - fc Unassigned_Ambiguity (EXACT)")
rho, n = spearman(hts, fc)
rec("spearman_htseq_featurecounts", "%.6f" % rho, "over %d genes counted by either; OBSERVATION" % n)
rho, n = spearman(hts, star[1])
rec("spearman_htseq_star", "%.6f" % rho, "over %d genes counted by either; OBSERVATION" % n)

# ---- strandedness, settled by STAR's two stranded columns ----
f, r = sum(star[2].values()), sum(star[3].values())
rec("star_stranded_fwd_rev", "%d %d ratio %.4f" % (f, r, f / float(r)), "~1 => unstranded library, so -s no / -s 0 is right")
rec("htseq_counts_sha256", hsha)

with open(os.path.join(O, "check.txt"), "w") as fh:
    fh.write("\n".join(rows) + "\n")
    fh.write(("COMPARE PASSED\n" if not bad else "COMPARE FAILED %s\n" % bad))
print("COMPARE PASSED" if not bad else "COMPARE FAILED %s" % bad)
sys.exit(1 if bad else 0)
