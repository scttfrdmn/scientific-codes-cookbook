# The checks, over results.jsonl. A failed check exits 1, which fails the task.
#
#   python3 -u summarize.py <out dir> <expected counts: QF_IDL=2528,...>
#
# Asserted:
#   every listed benchmark has a record, and each logic has the count the release README states;
#   no verdict contradicts the benchmark's published :status (a wrong answer);
#   every sat model satisfies every assertion of the file, evaluated by smtcheck.py (not Z3);
#   every unsat core re-solves unsat on its own;
#   no core member's removal yields a model that smtcheck.py finds violating the remainder;
#   control: no such model satisfies the WHOLE file (it is unsat, so the evaluator must say no).
# Reported: solved counts, timeouts, not-checked models, core sizes, necessity, errors.
import collections, json, statistics, sys

OUT = sys.argv[1]
EXPECT = dict(kv.split("=") for kv in sys.argv[2].split(",")) if len(sys.argv) > 2 and sys.argv[2] else {}
rows = [json.loads(l) for l in open(OUT + "/results.jsonl")]
jobs = [l.rstrip("\n").split("\t") for l in open(OUT + "/jobs.txt")]
fails = []


def check(ok, msg):
    if not bool(ok):
        fails.append(msg)
        print("FAIL:", msg, flush=True)


seen = collections.Counter(r["bench"] for r in rows)
check(len(rows) == len(jobs) and set(seen) == {b for _, b in jobs} and max(seen.values()) == 1,
      "records %d for %d jobs (%d distinct)" % (len(rows), len(jobs), len(seen)))
per = collections.Counter(lg for lg, _ in jobs)
for lg, n in EXPECT.items():
    check(per[lg] == int(n), "%s: %d benchmarks, release README says %s" % (lg, per[lg], n))

score = open(OUT + "/score.tsv", "w")
score.write("logic\tbench\tstatus\tverdict\tsolve_s\trlimit\tconflicts\tmodel_check\tcore_size\tcore_alone\t"
            "necessary\tredundant\tnec_unknown\tmaxrss_mib\twall_s\terror\n")
tab = collections.defaultdict(collections.Counter)
cores = collections.defaultdict(list)
nec_tot = collections.Counter()
for r in sorted(rows, key=lambda r: r["bench"]):
    lg, st, v = r["logic"], r.get("status", "absent"), r.get("verdict", "none")
    nec = r.get("necessity", {})
    score.write("\t".join(str(x) for x in (
        lg, r["bench"], st, v, r.get("solve_s", ""), r.get("rlimit", ""), r.get("conflicts", ""),
        r.get("model_check", ""), r.get("core_size", ""), r.get("core_alone", ""), nec.get("necessary", ""),
        nec.get("redundant", ""), nec.get("unknown", ""), r["maxrss_mib"], r["wall_s"],
        r.get("error", "").strip().replace("\t", " ")[:200])) + "\n")
    tab[lg]["n"] += 1
    tab[lg]["status_" + st] += 1
    tab[lg][v] += 1
    if r.get("error"):
        tab[lg]["error"] += 1
    if v == "unknown":
        tab[lg]["unknown:" + ("timeout" if "timeout" in r.get("reason", "") or "canceled" in r.get("reason", "")
                              else "memout" if "memory" in r.get("reason", "") else r.get("reason", "")[:40])] += 1
    if v in ("sat", "unsat") and st in ("sat", "unsat"):
        tab[lg]["agree_published" if v == st else "WRONG"] += 1
        check(v == st, "%s: Z3 says %s, published status %s" % (r["bench"], v, st))
    if v in ("sat", "unsat") and st not in ("sat", "unsat"):
        tab[lg]["new_" + v] += 1
    if v == "sat":
        mc = r.get("model_check", "missing")
        tab[lg]["model_ok" if mc == "ok" else "model_VIOLATED" if mc == "VIOLATED" else "model_not_checked"] += 1
        check(mc != "VIOLATED", "%s: Z3's model violates the file's assertions" % r["bench"])
        if mc not in ("ok", "VIOLATED"):
            print("not checked: %s %s" % (r["bench"], mc), flush=True)
    if v == "unsat":
        cv = r.get("core_verdict", "none")
        tab[lg]["core_" + cv] += 1
        if cv == "unsat":
            ca = r.get("core_alone")
            tab[lg]["core_alone_" + str(ca)] += 1
            check(ca != "sat", "%s: the unsat core is sat on its own" % r["bench"])
            cores[lg].append((r["core_size"], r["assertions"]))
            for k, x in nec.items():
                nec_tot[k] += x
            check(nec.get("violated", 0) == 0, "%s: a core-minus-one model violates the remainder" % r["bench"])
            check(nec.get("control_ACCEPTED", 0) == 0, "%s: control: the evaluator accepted a model of an unsat file" % r["bench"])
            if nec and (nec.get("unknown", 0) + nec.get("not-checked", 0) == 0) and \
               nec.get("necessary", 0) == r["core_size"]:
                tab[lg]["core_every_member_necessary"] += 1
            if r.get("necessity_skipped"):
                print("necessity skipped: %s %s" % (r["bench"], r["necessity_skipped"]), flush=True)
score.close()

for lg in tab:
    c = tab[lg]
    print("== %s" % lg)
    for k in sorted(c):
        print("  %-32s %d" % (k, c[k]))
    if cores[lg]:
        cs = [a for a, _ in cores[lg]]
        frac = [a / b for a, b in cores[lg] if b]
        print("  core size: median %d, max %d; median fraction of the file's assertions %.4f"
              % (statistics.median(cs), max(cs), statistics.median(frac)))
print("necessity totals:", dict(nec_tot))
rss = sorted(r["maxrss_mib"] for r in rows)
print("peak RSS per process: median %.0f MiB, p99 %.0f MiB, max %.0f MiB" % (
    rss[len(rss) // 2], rss[int(len(rss) * 0.99)], rss[-1]))
print("summed solve time of solved benchmarks: %.1f s" % sum(
    r.get("solve_s", 0) for r in rows if r.get("verdict") in ("sat", "unsat")))
if fails:
    print("%d check(s) failed" % len(fails))
    sys.exit(1)
print("Z3 REAL OK")
