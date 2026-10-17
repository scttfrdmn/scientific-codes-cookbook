"""Account for every test in Nilearn's own suite, across the three groups its tox.ini runs.

Each group was collected in a separate `--collect-only` process and then run, so "every test is
accounted for" is a check between two counts rather than one log agreeing with itself. The three
marker expressions partition the suite, so their items must also add up to the whole collection.
Run with `python3 -u`.
"""
import collections
import json
import re
import sys

GROUPS = sys.argv[1:]
fails, total_items = [], 0
whole = json.load(open("collect-all.json"))["items"]
print("suite_collected    %d items in the whole suite (separate --collect-only)" % len(whole))
for g in GROUPS:
    col, run = json.load(open("collect-%s.json" % g)), json.load(open("run-%s.json" % g))
    log = open("suite-%s.log" % g, errors="replace").read()
    rc = int(open("rc-%s" % g).read().split()[0])
    items, cskips = col["items"], col["collect_skips"]
    total_items += len(items)
    summ = [l for l in log.splitlines() if re.search(r"\d+ (passed|failed|deselected)", l) and " in " in l]
    counts = collections.Counter()
    for num, kind in re.findall(r"(\d+) (passed|failed|skipped|xfailed|xpassed|errors?)", summ[-1] if summ else ""):
        counts["errors" if kind.startswith("error") else kind] += int(num)
    per = collections.defaultdict(int)
    for nid, when, outcome in run["reports"]:
        if when == "call" or outcome != "passed":
            per[nid] += 1
    ids = set(items)
    missing = [i for i in items if per.get(i, 0) == 0]
    extra = [i for i in per if i not in ids]
    multi = [i for i, k in per.items() if k != 1]
    ssum = sum(counts[k] for k in ("passed", "skipped", "xfailed", "xpassed", "failed", "errors"))
    print("group %-14s %5d items: %d passed, %d skipped, %d xfailed, %d xpassed, %d failed, %d errors; "
          "%d missing, %d uncollected, %d multiple; exit %d"
          % (g, len(items), counts["passed"], counts["skipped"], counts["xfailed"], counts["xpassed"],
             counts["failed"], counts["errors"], len(missing), len(extra), len(multi), rc))
    tag = "group %s: " % g
    if rc != 0: fails.append(tag + "pytest exited %d" % rc)
    if counts["failed"]: fails.append(tag + "%d tests FAILED" % counts["failed"])
    if counts["errors"]: fails.append(tag + "%d ERRORS" % counts["errors"])
    if len(ids) != len(items): fails.append(tag + "duplicate collected ids")
    if missing: fails.append(tag + "%d collected tests never reported, e.g. %s" % (len(missing), missing[0][:100]))
    if extra: fails.append(tag + "%d reports for tests that were not collected" % len(extra))
    if multi: fails.append(tag + "%d tests reported more than one outcome" % len(multi))
    if ssum != len(items) + len(cskips):
        fails.append(tag + "summary %d != %d items + %d collection-time skips" % (ssum, len(items), len(cskips)))
    reasons = collections.Counter()
    for num, why in re.findall(r"SKIPPED \[(\d+)\] [^:]+:\d+: (.*)", log):
        reasons[re.sub(r"\d+", "N", why)[:90]] += int(num)
    for why, k in reasons.most_common(5):
        print("    skip %5d  %s" % (k, why))
print("suite_partition    %d items across the groups = %d in the whole suite" % (total_items, len(whole)))
if total_items != len(whole):
    fails.append("the groups hold %d items, the suite %d -- a test ran twice or not at all" % (total_items, len(whole)))
# A floor against a collapsing collection: 8,211 items on this pin.
if len(whole) < 8000:
    fails.append("only %d items collected; this pin collects 8,211" % len(whole))
if fails:
    print("FAIL: " + "; ".join(fails)); sys.exit(1)
print("SUITE OK")
