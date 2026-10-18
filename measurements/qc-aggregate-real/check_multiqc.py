#!/usr/bin/env python3
"""Check MultiQC's cohort tables against the FastQC reports they were built from.

    check_multiqc.py <task-1 dir> <task-2 out dir>

Exact checks only: the row count against the expected N, a numeric round trip of every carried
metric, the cohort read total, every module's PASS/WARN/FAIL against FastQC's own summary.txt,
and the withheld-sample run. Exits 1 on any failure.
"""
import csv
import os
import sys

IN, OUT = sys.argv[1], sys.argv[2]
fails = []


def check(ok, msg):
    if not bool(ok):
        fails.append(msg)
    return bool(ok)


def table(path):
    with open(path, newline="") as f:
        return list(csv.DictReader(f, delimiter="\t"))


def num(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


fq = {}
for line in open(os.path.join(IN, "fastqc_values.tsv")):
    s, reads, length, gc, dd, bases = line.rstrip("\n").split("\t")
    fq[s] = dict(reads=reads, length=length, gc=gc, dd=dd, bases=int(bases))
rows = table(os.path.join(OUT, "multiqc_fastqc.txt"))


def match(name, pool):
    """MultiQC cleans sample names; map each row back to the FastQC report it came from."""
    if name in pool:
        return name
    c = [s for s in pool if s.startswith(name)]
    return c[0] if len(c) == 1 else None


nexp = len(fq)
print("rows_in_report\t%d of %d   (assert equal -- the aggregator must not drop a sample)" % (len(rows), nexp))
check(len(rows) == nexp, "cohort report has %d rows, expected %d" % (len(rows), nexp))

mods = None
round_trip = string_eq = status_eq = status_n = 0
avg_exact = avg_n = 0
avg_obs = []
seen = set()
for r in rows:
    s = match(r["Sample"], fq)
    if not check(s is not None and s not in seen, "row %r maps to no unique FastQC report" % r["Sample"]):
        continue
    seen.add(s)
    v = fq[s]
    ok = True
    for col, key in (("Total Sequences", "reads"), ("%GC", "gc"), ("total_deduplicated_percentage", "dd")):
        a, b = num(r.get(col)), num(v[key])
        ok &= check(a is not None and b is not None and a == b, "%s %s: MultiQC %r vs FastQC %r" % (s, col, r.get(col), v[key]))
    a, b = num(r.get("Sequence length")), num(v["length"])
    ok &= check((a == b) if (a is not None and b is not None) else (str(r.get("Sequence length")) == v["length"]),
                "%s Sequence length: MultiQC %r vs FastQC %r" % (s, r.get("Sequence length"), v["length"]))
    round_trip += bool(ok)
    string_eq += (str(r.get("Total Sequences")) == v["reads"])
    # MultiQC's mean read length, against the exact mean from the recount (bases / reads)
    exact = v["bases"] / int(v["reads"])
    got = num(r.get("avg_sequence_length"))
    if "-" not in v["length"]:
        avg_n += 1
        avg_exact += check(got == exact, "%s avg_sequence_length %r != exact %r on a fixed-length file" % (s, got, exact))
    else:
        avg_obs.append("%s %.4f vs exact %.4f (%+.4f)" % (s, got if got is not None else float("nan"), exact,
                                                          (got - exact) if got is not None else float("nan")))
    # every module's status, against FastQC's own summary.txt
    for line in open(os.path.join(IN, s + ".summary.txt")):
        st, mod, _ = line.rstrip("\n").split("\t")
        col = mod.lower().replace(" ", "_")
        if col in r:
            status_n += 1
            status_eq += check(r[col].upper() == st, "%s %s: MultiQC %r vs FastQC %r" % (s, mod, r[col], st))
print("values_round_trip\t%d of %d   (assert all -- EXACT, numerically: reads, length, %%GC, dedup%%)" % (round_trip, nexp))
print("module_status_round_trip\t%d of %d   (assert all -- every module's PASS/WARN/FAIL == FastQC's summary.txt)"
      % (status_eq, status_n))
check(status_n >= nexp * 10, "only %d module statuses found in multiqc_fastqc.txt" % status_n)
print("avg_length_fixed_length\t%d of %d   (assert all -- == bases/reads where every read is one length)" % (avg_exact, avg_n))
for o in avg_obs:
    print("avg_length_variable\t%s   (observation: MultiQC averages FastQC's binned length histogram)" % o)

sfq = sum(int(v["reads"]) for v in fq.values())
smq = sum(int(num(r["Total Sequences"])) for r in rows)
print("cohort_reads\tFastQC %d == MultiQC %d   (assert equal -- CONSERVATION across the aggregation)" % (sfq, smq))
check(sfq == smq, "read totals differ")
print("string_equal_rows\t%d of %d   (observation: a TEXTUAL comparison of the same read counts)" % (string_eq, nexp))

# The headline General Statistics table. It stores read counts in MILLIONS, as floats.
gs = table(os.path.join(OUT, "multiqc_general_stats.txt"))
check(len(gs) == nexp, "general stats has %d rows" % len(gs))
g_round = g_float = g_gc = g_dup = g_fail = 0
for r in gs:
    s = match(r["Sample"], fq)
    if not check(s is not None, "general stats row %r maps to no report" % r["Sample"]):
        continue
    v = fq[s]
    m = num(r.get("fastqc-total_sequences"))
    g_round += check(m is not None and round(m * 1e6) == int(v["reads"]),
                     "%s general stats total_sequences %r M != %s" % (s, m, v["reads"]))
    g_float += (m is not None and m * 1e6 == int(v["reads"]))
    g_gc += check(num(r.get("fastqc-percent_gc")) == num(v["gc"]), "%s general stats %%GC" % s)
    g_dup += check(abs(num(r.get("fastqc-percent_duplicates")) - (100 - num(v["dd"]))) < 1e-9,
                   "%s general stats percent_duplicates %r != 100 - %s" % (s, r.get("fastqc-percent_duplicates"), v["dd"]))
    st = [l.split("\t")[0] for l in open(os.path.join(IN, s + ".summary.txt"))]
    g_fail += check(abs(num(r.get("fastqc-percent_fails")) - 100.0 * st.count("FAIL") / len(st)) < 1e-9,
                    "%s percent_fails %r != 100 x %d/%d" % (s, r.get("fastqc-percent_fails"), st.count("FAIL"), len(st)))
print("general_stats_reads\t%d of %d   (assert all -- round(millions x 1e6) == FastQC's count)" % (g_round, nexp))
print("general_stats_reads_float\t%d of %d   (observation: millions x 1e6 == the count with NO rounding)" % (g_float, nexp))
print("general_stats_gc_dup_fails\t%d / %d / %d of %d   (assert all -- %%GC, 100 - dedup%%, 100 x FAILs/modules)"
      % (g_gc, g_dup, g_fail, nexp))
sgs = sum(num(r["fastqc-total_sequences"]) for r in gs)
print("general_stats_cohort_M\t%r   (observation: the cohort total as the headline table carries it)" % sgs)

p = dict(l.rstrip("\n").split("\t") for l in open(os.path.join(OUT, "partial.txt")))
prow = table(os.path.join(OUT, "multiqc_fastqc.partial.txt"))
present = {match(r["Sample"], fq) for r in prow}
print("missing_sample_exit\t%s   (assert 0 -- dropping a cohort member is NOT an error to MultiQC)" % p["partial_exit"])
print("missing_sample_rows\t%d of %d   (assert %d -- the report is silently one sample short)" % (len(prow), nexp, nexp - 1))
print("missing_sample_named\t%s log lines name %s; %s warn/error lines   (observation)"
      % (p["partial_lines_naming_withheld"], p["withheld"], p["partial_warn_lines"]))
check(p["partial_exit"] == "0", "multiqc exited %s with a sample missing" % p["partial_exit"])
check(len(prow) == nexp - 1 and p["withheld"] not in present, "partial report has %d rows" % len(prow))

for f in fails:
    print("FAIL\t" + f)
sys.exit(1 if fails else 0)
