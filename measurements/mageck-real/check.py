#!/usr/bin/env python3
"""The mageck-real assertions, run in /tmp/out after count, test, mle and recount.py.

Fails (exit 1) on any identity; prints `MAGECK REAL OK` last when all hold. Observations are
printed with "(observation)" and never fail the run.
"""
import math
import re
import sys

ok = True
SAMPLES = ["plasmid", "repA", "repB"]
ENA_READS = {"plasmid": 60120685, "repA": 99121522, "repB": 93622486}   # ENA read_count


def rec(name, val, note=""):
    print("%-38s %s%s" % (name, str(val), ("   " + note) if note else ""))


def fail(msg):
    global ok
    print("FAIL: " + msg)
    ok = False


def table(path):
    rows = [l.rstrip("\n").split("\t") for l in open(path)]
    return rows[0], rows[1:]


def kv(path):
    return dict(l.rstrip("\n").split("\t", 1) for l in open(path) if "\t" in l)


# ---- count: each table equals an independent recount, every sgRNA x sample -----------------
def counts(run):
    mh, mr = table(run + ".count.txt")
    if mh[2:] != SAMPLES:
        fail("%s count table columns %s, expected %s" % (run, mh[2:], SAMPLES))
    ids = [r[0] for r in mr]
    if len(set(ids)) != len(ids):
        fail("%s: duplicate sgRNA IDs in the count table" % run)
    return {r[0]: (r[1], [int(r[2 + j]) for j in range(3)]) for r in mr}


def gini(col):   # mean absolute difference x n/(n-1), on log(count+1): MAGeCK's form
    x = sorted(math.log(c + 1.0) for c in col)
    n = len(x)
    pre, mad = 0.0, 0.0
    for i, v in enumerate(x):
        mad += v * i - pre
        pre += v
    return (mad / (n * n * (sum(x) / n))) * n / (n - 1)


TABLE = {}
for run in ("stagger", "auto"):
    got = TABLE[run] = counts(run)
    logtxt = open(run + ".log").read()
    dups = re.search(r"There are (\d+) sgRNAs with duplicated sequences", logtxt)
    if run == "stagger":
        rec("library_duplicated_sequences", dups.group(1) if dups else "?", "(MAGeCK keeps the first)")
    sh, sr = table(run + ".countsummary.txt")
    H = {h: i for i, h in enumerate(sh)}
    ginis = [float(x) for x in re.findall(r"giniindex\t(\S+)", logtxt)]
    for j, s in enumerate(SAMPLES):
        st = kv("/tmp/work/recount.%s.%s.stats" % (run, s))
        rc = {l.split("\t")[0]: int(l.split("\t")[1]) for l in open("/tmp/work/recount.%s.%s.tsv" % (run, s))}
        if set(rc) != set(got):
            fail("%s %s: recount has %d sgRNAs, count table %d" % (run, s, len(rc), len(got)))
        nd = sum(1 for k in got if rc.get(k) != got[k][1][j])
        reads, mapped, anyoff = int(st["reads"]), int(st["mapped"]), int(st["mapped_any_offset"])
        rec("%s_vs_recount_%s" % (run, s), "%d sgRNAs, %d differ" % (len(got), nd),
            "(offsets %s; reads %d, counted %d = %.2f%%)" % (st["offsets"], reads, mapped, 100.0 * mapped / reads))
        if nd:
            fail("%s %s: %d sgRNAs differ from the independent recount" % (run, s, nd))
        if reads != ENA_READS[s]:
            fail("%s: %d reads in the FASTQ, ENA lists %d" % (s, reads, ENA_READS[s]))
        rec("%s_uncounted_guides_%s" % (run, s), "%d reads (%.3f%% of reads) hold a guide at another offset" %
            (anyoff - mapped, 100.0 * (anyoff - mapped) / reads), "(observation; offset:reads %s)" % st["uncounted_offsets"])
        row = [r for r in sr if r[H["Label"]] == s][0]
        col = [got[k][1][j] for k in got]
        g = gini(col)
        gl = ginis[j] if j < len(ginis) else float("nan")
        rec("%s_summary_%s" % (run, s), "reads %s mapped %s zero %s gini %s" % (row[H["Reads"]], row[H["Mapped"]], row[H["Zerocounts"]], row[H["GiniIndex"]]),
            "(|gini - recompute| %.1e)" % abs(gl - g))
        if int(row[H["Reads"]]) != ENA_READS[s]:
            fail("%s %s: MAGeCK read %s reads, ENA lists %d" % (run, s, row[H["Reads"]], ENA_READS[s]))
        if int(row[H["Mapped"]]) != sum(col) or int(row[H["Mapped"]]) != mapped:
            fail("%s %s: summary Mapped is not the table's column sum and the recount's counted reads" % (run, s))
        if int(row[H["Zerocounts"]]) != sum(1 for c in col if c == 0) or int(row[H["TotalsgRNAs"]]) != len(col):
            fail("%s %s: summary zero / total sgRNAs do not equal the table's own column" % (run, s))
        if row[H["GiniIndex"]] != "{:.4g}".format(g) or not abs(gl - g) <= 1e-12 * g:
            fail("%s %s: Gini %s / %r does not equal an independent recompute %r" % (run, s, row[H["GiniIndex"]], gl, g))
got = TABLE["stagger"]


# ---- test (RRA): median-of-ratios size factors and normalised counts, recomputed ----------
def factors(cols):
    idx = [SAMPLES.index(c) for c in cols]
    m = {k: [v[1][i] for i in idx] for k, v in got.items()}
    gm = {k: math.exp(sum(math.log(c + 1.0) for c in v) / len(v)) for k, v in m.items() if sum(v) > 0}
    out = []
    for j in range(len(idx)):
        r = sorted(m[k][j] / gm[k] for k in gm)
        out.append(1.0 / r[len(r) // 2])
    return out


for name, cols in (("rra", SAMPLES), ("null", ["repA", "repB"])):
    f = [float(x) for x in re.search(r"Final size factor: (.*)", open(name + ".log").read()).group(1).split()]
    mine = factors(cols)
    rel = max(abs(a - b) / b for a, b in zip(f, mine)) if len(f) == len(mine) else float("nan")
    rec("size_factors_" + name, " ".join("%.6f" % v for v in f), "(max rel diff vs recompute %.1e)" % rel)
    if not rel <= 1e-12:
        fail("%s median size factors do not equal an independent recompute" % name)
nh, nr = table("rra.normalized.txt")
f = [float(x) for x in re.search(r"Final size factor: (.*)", open("rra.log").read()).group(1).split()]
fac = dict(zip(SAMPLES, f))
worst = max(abs(float(r[2 + j]) - got[r[0]][1][SAMPLES.index(c)] * fac[c]) / max(1.0, float(r[2 + j]))
            for r in nr for j, c in enumerate(nh[2:]))
rec("normalized_eq_raw_x_factor", "%.1e" % worst, "(max rel diff, %d rows)" % len(nr))
if not worst <= 1e-12:
    fail("normalised counts are not raw counts x size factor")
same = open("rra.gene_summary.txt").read() == open("rra_rep.gene_summary.txt").read()
rec("rra_rerun_byte_identical", same)
if not same:
    fail("RRA gave a different gene summary on an identical rerun")


# ---- regime: Hart's reference sets (CEGv2 essential, NEGv1 non-essential) --------------------
def geneset(path):
    return {l.split("\t")[0] for l in open(path).read().splitlines()[1:] if l.strip()}


CEG, NEG = geneset("/tmp/data/CEGv2.txt"), geneset("/tmp/data/NEGv1.txt")
gh, gr = table("rra.gene_summary.txt")
G = {h: i for i, h in enumerate(gh)}
genes = {r[0] for r in gr}
ceg, neg = CEG & genes, NEG & genes
rec("reference_sets_in_library", "CEGv2 %d of %d, NEGv1 %d of %d" % (len(ceg), len(CEG), len(neg), len(NEG)))
hits = {r[0] for r in gr if float(r[G["neg|fdr"]]) < 0.05}
nfd = len(hits & neg)
rec("rra_depleted_fdr_lt_0.05", "%d of %d genes: %d CEG (recall %.3f), %d NEG" % (len(hits), len(gr), len(hits & ceg), len(hits & ceg) / len(ceg), nfd))
# NEG genes are true nulls for a viability dropout, so they are a subset of the false discoveries,
# whose expected share of the discoveries FDR control bounds at 0.05
if not (len(hits) > 0 and nfd <= 0.05 * len(hits)):
    fail("NEGv1 genes are %d of %d depleted discoveries, more than FDR 0.05 permits" % (nfd, len(hits)))
for name, note in (("rra_ctrl", "non-targeting guides as the null"), ("rra_auto", "AUTO offsets")):
    h2, r2 = table(name + ".gene_summary.txt")
    G2 = {h: i for i, h in enumerate(h2)}
    x = {r[0] for r in r2 if float(r[G2["neg|fdr"]]) < 0.05}
    rec(name + "_depleted_fdr_lt_0.05", "%d of %d genes: %d CEG, %d NEG" % (len(x), len(r2), len(x & ceg), len(x & neg)), "(observation: %s)" % note)
rank = {r[0]: int(r[G["neg|rank"]]) for r in gr}


def auc(pos, negs, score):   # P(score of a pos gene < score of a neg gene): Mann-Whitney
    s = sorted([(score[g], 1) for g in pos] + [(score[g], 0) for g in negs])
    rsum, i = 0.0, 0
    while i < len(s):
        k = i
        while k < len(s) and s[k][0] == s[i][0]:
            k += 1
        r = (i + k + 1) / 2.0
        rsum += r * sum(1 for t in s[i:k] if t[1] == 1)
        i = k
    npos = len(pos)
    return 1.0 - (rsum - npos * (npos + 1) / 2.0) / (npos * len(negs))


rec("rra_auc_ceg_vs_neg", "%.4f" % auc(ceg, neg, rank), "(observation; by neg|rank)")

# ---- mle: deterministic columns, NaN-free, and the reference sets in order ------------------
mh2, mr2 = table("mle.gene_summary.txt")
M = {h: i for i, h in enumerate(mh2)}
beta = {r[0]: float(r[M["dropout|beta"]]) for r in mr2}
nan = sum(1 for v in beta.values() if not math.isfinite(v))
rec("mle_genes", "%d, %d non-finite beta" % (len(beta), nan))
if nan:
    fail("MLE betas: %d non-finite" % nan)
# gene sets, from the count table: RRA first drops sgRNAs with zero reads in every sample of the
# comparison, so it scores exactly the genes with a read; MLE scores every gene in the table
all_genes = {v[0] for v in got.values()}
read_genes = {v[0] for v in got.values() if sum(v[1]) > 0}
rec("genes_scored", "RRA %d (genes with a read: %d), MLE %d (genes in the table: %d)" % (len(genes), len(read_genes), len(beta), len(all_genes)))
if genes != read_genes or set(beta) != all_genes:
    fail("RRA does not score exactly the genes with a read, or MLE not exactly the genes in the table")
for g in sorted(all_genes - read_genes):
    rec("mle_beta_of_a_gene_with_no_reads", "%s %.5f" % (g, beta[g]), "(observation)")
med = lambda v: sorted(v)[len(v) // 2]
mc, mn = med([beta[g] for g in ceg]), med([beta[g] for g in neg])
rec("mle_median_beta", "CEG %.4f, NEG %.4f" % (mc, mn))
if not mc < mn:
    fail("median MLE beta of essential genes is not below that of non-essential genes")
rec("mle_auc_ceg_vs_neg", "%.4f" % auc(ceg, neg, beta), "(observation; by beta)")
cols = ["Gene", "sgRNA"] + [c for c in mh2 if c.endswith(("|beta", "|z", "|wald-p-value", "|wald-fdr"))]
with open("mle.fixed.tsv", "w") as o:
    for r in [mh2] + mr2:
        o.write("\t".join(r[M[c]] for c in cols) + "\n")
rec("mle_fixed_columns", ",".join(cols[2:]), "(written to mle.fixed.tsv for the cross-chip compare)")

# ---- null: replicate vs replicate (observation) ---------------------------------------------
nh2, nr2 = table("null.gene_summary.txt")
N = {h: i for i, h in enumerate(nh2)}
rec("null_repB_vs_repA_fdr_lt_0.05", "neg %d, pos %d" % (sum(1 for r in nr2 if float(r[N["neg|fdr"]]) < 0.05),
                                                       sum(1 for r in nr2 if float(r[N["pos|fdr"]]) < 0.05)), "(observation)")
if not ok:
    sys.exit(1)
print("MAGECK REAL OK")
