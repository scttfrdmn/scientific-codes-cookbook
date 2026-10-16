"""Account for every test in NumPy's, SciPy's and pandas' own suites.

Each group was collected in a separate `--collect-only` process and then run, so "every test is
accounted for" is a check between two counts rather than one log agreeing with itself (account.py
records both). pandas runs as the two invocations its own wheel test makes (parallel, then
`single_cpu` serially); those two marker expressions partition one collection, so their items must
add up to it. Subtest reports (pytest >= 9) are counted apart from tests. Every failure must be
attributed, by its own traceback, to one cause in KNOWN at its exact count. Run with `python3 -u`.
"""
import collections
import json
import re
import sys

# A floor per group against a collapsing collection, ~2% under this pin's counts
# (50,865 / 84,787 / 230,774 / 2,723).
FLOORS = {"numpy": 49800, "scipy": 83000, "pandas-par": 226000, "pandas-single": 2650}
# Groups whose items must add up to one separate collection.
PARTITIONS = {"pandas-all": ("pandas-par", "pandas-single")}
# Every failure this pin produces on Graviton4, each run and reported -- never deselected -- and
# attributed by the text of its own traceback: (group, nodeid regex, text in the traceback,
# EXACT count, cause). Any other failure fails the run, and so does a count that moves either
# way, so a fix upstream or in the image shows up as a stale page rather than a silent pass.
KNOWN = [
    ("numpy", r"_core/tests/(test_mem_policy|test_array_interface)\.py::", "Unknown compiler(s)", 10,
     "builds a C extension at test time; the runtime env has no C compiler"),
    ("pandas-par", r"::", "not found in timezone database", 13,
     "pyarrow reads /usr/share/zoneinfo, and the image's Debian 13 tzdata has no legacy US/* links"),
    ("pandas-single", r"test_sql\.py::test_con_string_import_error$", "Regex pattern did not match", 1,
     "pandas 3.0.6's message changed and this test (run only without sqlalchemy) was not updated"),
    ("pandas-single", r"test_numba\.py::TestEngine::test_numba_min_periods$", "_call_reprcompare", 1,
     "numba 0.68 cannot type pytest's rewritten assert inside the jitted test function"),
]

fails, sizes = [], {}
for g in sys.argv[1:]:
    try:   # a group that died leaves a missing or truncated record: fail it, still report the rest
        col, run = json.load(open("collect-%s.json" % g)), json.load(open("run-%s.json" % g))
        log = open("suite-%s.log" % g, errors="replace").read()
        rc, wall = open("rc-%s" % g).read().split()
    except Exception as e:
        print("group %-13s no complete record (%s)" % (g, type(e).__name__))
        fails.append("group %s: no complete record" % g)
        continue
    items, cskips = col["items"], col["collect_skips"]
    sizes[g] = len(items)
    summ = [l for l in log.splitlines() if re.search(r"\d+ (passed|failed)", l) and " in " in l]
    last = summ[-1] if summ else ""
    counts = collections.Counter()
    for num, kind in re.findall(r"(\d+) (passed|failed|skipped|xfailed|xpassed|errors?)(?! subtest)", last):
        counts["errors" if kind.startswith("error") else kind] += int(num)
    per, subs = collections.defaultdict(int), collections.Counter()
    failing = []   # (nodeid, when, traceback tail) for every failed or erroring report
    for nid, when, outcome, is_sub, msg in run["reports"]:
        if is_sub:
            subs[outcome] += 1
        elif when == "call" or outcome != "passed":
            per[nid] += 1
        if outcome == "failed" and not is_sub:
            failing.append((nid, when, msg))
    summary_subs = sum(int(n) for n in re.findall(r"(\d+) subtests? (?:passed|failed|skipped)", last))
    ids = set(items)
    missing = [i for i in items if per.get(i, 0) == 0]
    extra = [i for i in per if i not in ids]
    multi = [i for i, k in per.items() if k != 1]
    ssum = sum(counts[k] for k in ("passed", "skipped", "xfailed", "xpassed", "failed", "errors"))
    print("group %-13s %6d items: %d passed, %d skipped, %d xfailed, %d xpassed, %d failed, %d errors; "
          "%d missing, %d uncollected, %d multiple; %d subtest reports (summary %d); "
          "summary %d = items + %d collection-time skips; exit %s, %s s"
          % (g, len(items), counts["passed"], counts["skipped"], counts["xfailed"], counts["xpassed"],
             counts["failed"], counts["errors"], len(missing), len(extra), len(multi),
             sum(subs.values()), summary_subs, ssum, len(cskips), rc, wall))
    tag = "group %s: " % g
    # Attribute every failure to exactly one KNOWN cause by its id AND its own traceback.
    rules = [k for k in KNOWN if k[0] == g]
    hits = collections.Counter()
    for nid, when, msg in failing:
        match = [r for r in rules if re.search(r[1], nid) and r[2] in msg]
        print("    FAILED/ERROR (%s) %s  ->  %s" % (when, nid, match[0][4] if len(match) == 1 else "UNATTRIBUTED"))
        if len(match) == 1:
            hits[match[0][1]] += 1
        else:
            fails.append(tag + "unattributed failure %s (%s): %s" % (nid, when, msg.strip().splitlines()[-1][:160] if msg.strip() else ""))
    for _, pat, _, n, why in rules:
        if hits[pat] != n:
            fails.append(tag + "%d failures for '%s', this pin gives exactly %d (%s)" % (hits[pat], pat, n, why))
    if counts["failed"] + counts["errors"] != len(failing):
        fails.append(tag + "summary has %d failed + %d errors, %d failing reports recorded"
                     % (counts["failed"], counts["errors"], len(failing)))
    # pytest exits 1 when tests fail; anything else (interrupted, internal error, usage) is not a verdict.
    if int(rc) not in ((1,) if failing else (0,)): fails.append(tag + "pytest exited %s" % rc)
    if not last: fails.append(tag + "no summary line")
    if subs.get("failed"): fails.append(tag + "%d subtests FAILED" % subs["failed"])
    if sum(subs.values()) != summary_subs:
        fails.append(tag + "%d subtest reports vs %d in the summary" % (sum(subs.values()), summary_subs))
    if len(ids) != len(items): fails.append(tag + "duplicate collected ids")
    if missing: fails.append(tag + "%d collected tests never reported, e.g. %s" % (len(missing), missing[0][:100]))
    if extra: fails.append(tag + "%d reports for tests that were not collected" % len(extra))
    if multi: fails.append(tag + "%d tests reported more than one outcome" % len(multi))
    if ssum != len(items) + len(cskips):
        fails.append(tag + "summary %d != %d items + %d collection-time skips" % (ssum, len(items), len(cskips)))
    if len(items) < FLOORS.get(g, 0):
        fails.append(tag + "only %d items collected; the floor is %d" % (len(items), FLOORS[g]))
    reasons = collections.Counter()
    for num, why in re.findall(r"SKIPPED \[(\d+)\] [^:]+:\d+: (.*)", log):
        reasons[re.sub(r"\d+", "N", why)[:100]] += int(num)
    for why, k in reasons.most_common(12):
        print("    skip %6d  %s" % (k, why))
for whole, parts in PARTITIONS.items():
    try:
        n = len(json.load(open("collect-%s.json" % whole))["items"])
    except Exception as e:
        fails.append("%s: no collection record (%s)" % (whole, type(e).__name__)); continue
    if not all(p in sizes for p in parts):
        continue
    print("partition %-11s %d items across %s = %d in one collection" % (whole, sum(sizes[p] for p in parts), "+".join(parts), n))
    if sum(sizes[p] for p in parts) != n:
        fails.append("%s: the groups hold %d items, the collection %d" % (whole, sum(sizes[p] for p in parts), n))
if fails:
    print("FAIL: " + "; ".join(fails)); sys.exit(1)
print("SUITE OK")
