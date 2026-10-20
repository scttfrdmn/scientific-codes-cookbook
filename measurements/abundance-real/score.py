#!/usr/bin/env python3
"""Score Bracken's species (S) and genus (G) re-estimation of one whole-run kraken2 report.

    python3 -u score.py <kraken2 report> <ENA read_count> <even|log> <threshold> <zymo.tsv> <outdir>

Reads <outdir>/{S,G}.{bracken,breport,log}; writes <outdir>/checks.txt and <outdir>/score.tsv and
exits non-zero if any check fails. The kraken2 report is parsed here independently of Bracken, so
each of Bracken's own counts is checked against a second reading of the same input.

  * books        total == ENA read_count; unclassified == the report's; kept + discarded +
                 distributed + not distributed + unclassified == total (Bracken's printed partition)
  * input        kept / discarded / the two taxon counts == this script's parse of the report;
                 reads above the level (distributed + not distributed) == the report's taxon reads
                 outside every taxon at the level
  * table        one row per report taxon at the level with clade >= threshold; kraken_assigned_reads
                 == that clade count; added_reads == new - assigned and never negative
  * conservation sum(new_est_reads) == kept + distributed, short by less than one read per row:
                 each row is int() of a sum of fractional shares (est_abundance.py)
  * normalisation fraction_total_reads == new / sum(new) to its printed 5 decimals
  * report       the -w report's rows at the level == the table's new_est_reads, and its root ==
                 sum(new) within the same per-row truncation
  * truth        the published mixture: the 8 Zymo bacteria are the top 8 (even), the D6310 ladder
                 (log), and no genus outside the mock above 1% of Bracken's genus-level reads
"""
import re
import sys

rep_p, want_pairs, kind, thresh, truth_p, outdir = sys.argv[1:7]
want_pairs, thresh = int(want_pairs), int(thresh)
lines, FAILS, obs = [], [], []


def check(ok, name, want, got):
    ok = bool(ok)
    lines.append("%-60s %-34s %s" % (name, want, ("ok   (%s)" if ok else "FAIL (%s)") % (got,)))
    if not ok:
        FAILS.append(name)


# --- the kraken2 report (pct, clade, taxon, rank, taxid, indented name)
rows = []
for line in open(rep_p):
    f = line.rstrip("\n").split("\t")
    if len(f) < 6:
        continue
    name = f[5]
    rows.append(dict(clade=int(f[1]), taxon=int(f[2]), rank=f[3], taxid=f[4], name=name.strip(),
                     depth=(len(name) - len(name.lstrip(" "))) // 2))
ids = [r["taxid"] for r in rows]
check(len(set(ids)) == len(ids), "kraken2 report taxids unique", "want %d" % len(ids), len(set(ids)))
uncl = sum(r["clade"] for r in rows if r["taxid"] == "0")
classified = sum(r["clade"] for r in rows if r["taxid"] == "1")

# truth: genus, species (B. subtilis is filed as B. spizizenii in this index; kraken2-real), DNA %
truth = [l.rstrip("\n").split("\t") for l in open(truth_p)][1:]
col = 3 if kind == "even" else 4
bact = [t for t in truth if t[5] == "yes"]          # standard-8 holds no fungi
SPECIES_IN_INDEX = {"Bacillus subtilis": "Bacillus spizizenii"}
mock = {"G": [t[0] for t in bact], "S": [SPECIES_IN_INDEX.get(t[1], t[1]) for t in bact]}
dna = {"G": {t[0]: t[col] for t in bact}, "S": {SPECIES_IN_INDEX.get(t[1], t[1]): t[col] for t in bact}}
LADDER = {"G": ["Listeria", "Pseudomonas", "Bacillus"],
          "S": ["Listeria monocytogenes", "Pseudomonas aeruginosa", "Bacillus spizizenii"]}  # 89.1 > 8.9 > 0.89%

tsv = ["level\tname\ttaxid\tdna_pct\tkraken2_clade\tkraken2_pct_of_classified\tbracken_new_est\tbracken_pct"]
for lvl in ("S", "G"):
    L = "[%s] " % lvl
    log = open("%s/%s.log" % (outdir, lvl)).read()

    def num(pat):
        m = re.search(pat, log)
        return int(m.group(1)) if m else -1
    total = num(r"Total reads in sample: (\d+)")
    kept = num(r"Total reads kept at \w+ level \(reads > threshold\): (\d+)")
    disc = num(r"Total reads discarded \(\w+ reads < threshold\): (\d+)")
    dist = num(r"Reads distributed: (\d+)")
    nodist = num(r"Reads not distributed \(eg\. no \w+ above threshold\): (\d+)")
    bu = num(r"Unclassified reads: (\d+)")
    n_all = num(r"Number of \w+ in sample: (\d+)")
    n_est = num(r"Number of \w+ with reads > threshold: (\d+)")
    n_del = num(r"Number of \w+ with reads < threshold: (\d+)")

    check(total == want_pairs, L + "total reads == ENA read_count", "want %d" % want_pairs, total)
    check(bu == uncl, L + "unclassified == kraken2 report's", "want %d" % uncl, bu)
    s = kept + disc + dist + nodist + bu
    check(s == total, L + "kept+discarded+distributed+not+unclassified == total", "want %d" % total, s)

    # second reading of the input: taxa at the level, and the reads above every one of them
    at = [r for r in rows if r["rank"] == lvl]
    my_kept = sum(r["clade"] for r in at if r["clade"] >= thresh)
    my_disc = sum(r["clade"] for r in at if r["clade"] < thresh)
    check((n_all, n_est, n_del) == (len(at), sum(r["clade"] >= thresh for r in at), sum(r["clade"] < thresh for r in at)),
          L + "taxa at level / above / below threshold == parse", "want %d" % len(at), (n_all, n_est, n_del))
    check(kept == my_kept and disc == my_disc, L + "kept, discarded == parse", "want %d, %d" % (my_kept, my_disc),
          "%d, %d" % (kept, disc))
    above, inside = 0, None   # inside = depth of the level taxon we are under, or None
    for r in rows:
        if inside is not None and r["depth"] <= inside:
            inside = None
        if inside is None and r["rank"] == lvl:
            inside = r["depth"]
        if inside is None and r["taxid"] != "0":
            above += r["taxon"]
    check(dist + nodist == above, L + "distributed + not distributed == reads above level", "want %d" % above,
          dist + nodist)

    # the abundance table
    tab = [l.rstrip("\n").split("\t") for l in open("%s/%s.bracken" % (outdir, lvl))]
    hdr, tab = tab[0], tab[1:]
    check(hdr == ["name", "taxonomy_id", "taxonomy_lvl", "kraken_assigned_reads", "added_reads", "new_est_reads",
                  "fraction_total_reads"], L + "table header", "Bracken's 7 columns", len(hdr))
    T = {t[1]: dict(name=t[0], lvl=t[2], ka=int(t[3]), add=int(t[4]), new=int(t[5]), frac=float(t[6])) for t in tab}
    check(len(T) == len(tab), L + "table taxids unique", "want %d" % len(tab), len(T))
    want_ids = {r["taxid"]: r["clade"] for r in at if r["clade"] >= thresh}
    check(set(T) == set(want_ids), L + "table taxa == report taxa at level >= threshold", "want %d" % len(want_ids),
          "%d, sym diff %d" % (len(T), len(set(T) ^ set(want_ids))))
    off = [i for i in T if i in want_ids and T[i]["ka"] != want_ids[i]]
    check(not off, L + "kraken_assigned_reads == report clade, every row", "want 0 off", len(off))
    check(all(t["lvl"] == lvl for t in T.values()), L + "taxonomy_lvl column == level", "want " + lvl, "-")
    off = [i for i, t in T.items() if t["add"] != t["new"] - t["ka"] or t["add"] < 0]
    check(not off, L + "added == new - assigned, and >= 0, every row", "want 0 off", len(off))
    sum_new = sum(t["new"] for t in T.values())
    deficit = kept + dist - sum_new
    check(0 <= deficit < len(T), L + "conservation: kept + distributed - sum(new) in [0, rows)",
          "want [0, %d)" % len(T), "%d + %d - %d = %d" % (kept, dist, sum_new, deficit))
    # fraction = int(new) / int(float sum of untruncated estimates), and that sum is in [sum_new, sum_new + rows)
    worst = max(abs(t["frac"] - t["new"] / sum_new) for t in T.values())
    bound = 5e-6 + max(t["new"] for t in T.values()) * len(T) / sum_new ** 2
    check(worst <= bound, L + "fraction_total_reads == new / sum(new)", "want <= %.2e" % bound, "%.2e" % worst)

    # the -w report: a kraken-style tree of the estimates
    br = {}
    for line in open("%s/%s.breport" % (outdir, lvl)):
        f = line.rstrip("\n").split("\t")
        if len(f) >= 6:
            br[f[4]] = (int(f[1]), f[3])
    off = [i for i, t in T.items() if t["new"] > 0 and br.get(i, (None,))[0] != t["new"]]
    check(not off, L + "-w report level rows == table new_est_reads", "want 0 off", len(off))
    root = br.get("1", (-1,))[0]
    check(sum_new <= root < sum_new + len(T), L + "-w report root == sum(new) within truncation",
          "want [%d, %d)" % (sum_new, sum_new + len(T)), root)

    # truth: the published mixture
    by_name = sorted(T.values(), key=lambda t: -t["new"])
    names = [t["name"] for t in by_name]
    if kind == "even":
        check(sorted(names[:8]) == sorted(mock[lvl]), L + "top 8 == the 8 Zymo bacteria", "want the D6300 list",
              ",".join(names[:8]))
    else:
        check(names[:3] == LADDER[lvl], L + "top 3, in order == the D6310 ladder", "want " + ">".join(LADDER[lvl]),
              ">".join(names[:3]))
    stray = [t for t in by_name if t["name"] not in mock[lvl]]
    big = stray[0] if stray else {"name": "-", "new": 0}
    share = 100.0 * big["new"] / sum_new
    if lvl == "G":
        check(share <= 1.0, L + "no genus outside the mock above 1%", "want 0 (the closed list)",
              "largest %s %.4f%%" % (big["name"], share))
    obs.append("%slargest taxon outside the mock: %s %.4f%% of %d" % (L, big["name"], share, sum_new))
    obs.append("%sdeficit %d over %d rows; kept %d distributed %d not distributed %d discarded %d"
               % (L, deficit, len(T), kept, dist, nodist, disc))

    k2 = {r["name"]: r["clade"] for r in at}
    by_n = {t["name"]: (i, t) for i, t in T.items()}
    for n in mock[lvl]:
        i, t = by_n.get(n, ("-", {"new": 0}))
        tsv.append("%s\t%s\t%s\t%s\t%d\t%.4f\t%d\t%.4f" % (lvl, n, i, dna[lvl][n], k2.get(n, 0),
                   100.0 * k2.get(n, 0) / classified, t["new"], 100.0 * t["new"] / sum_new))
    for t in [t for t in by_name if t["name"] not in mock[lvl]][:5]:
        tsv.append("%s\t*%s\t-\t0\t%d\t%.4f\t%d\t%.4f" % (lvl, t["name"], k2.get(t["name"], 0),
                   100.0 * k2.get(t["name"], 0) / classified, t["new"], 100.0 * t["new"] / sum_new))

open(outdir + "/score.tsv", "w").write("\n".join(tsv) + "\n")
lines += ["# " + o for o in obs]
lines.append("failures %d" % len(FAILS))
open(outdir + "/checks.txt", "w").write("\n".join(lines) + "\n")
print("\n".join(lines))
print("\n".join(tsv))
sys.exit(1 if FAILS else 0)
