"""Account for every test in spaCy's own suite.

The suite was collected in a separate `--collect-only` process and then run, so "every test is
accounted for" is a check between two counts rather than one log agreeing with itself (the
account.py plugin records both). spaCy's CI runs the suite as one serial invocation, so there is
one group here. Run with `python3 -u`.
"""
import collections
import json
import re
import sys

FLOOR, MEASURED = int(sys.argv[1]), sys.argv[2]
fails = []
col, run = json.load(open("collect.json")), json.load(open("run.json"))
log = open("suite.log", errors="replace").read()
rc, wall = open("rc").read().split()
items, cskips = col["items"], col["collect_skips"]
summ = [l for l in log.splitlines() if re.search(r"\d+ (passed|failed)", l) and " in " in l]
counts = collections.Counter()
for num, kind in re.findall(r"(\d+) (passed|failed|skipped|xfailed|xpassed|errors?)", summ[-1] if summ else ""):
    counts["errors" if kind.startswith("error") else kind] += int(num)
print("suite_items        %d collected, %d unique (separate --collect-only)" % (len(items), len(set(items))))
for k in ("passed", "skipped", "xfailed", "xpassed", "failed", "errors"):
    print("suite_%-12s %d" % (k, counts[k]))
print("suite_exit_wall    %s %s" % (rc, wall))
# One counted outcome per item: the call report, or a setup/teardown report that did not pass.
# Subtest reports (pytest >= 9) are counted apart, and must equal the summary's "N subtests".
per = collections.defaultdict(int)
subs = collections.Counter()
for nid, when, outcome, is_sub in run["reports"]:
    if is_sub:
        subs[outcome] += 1
    elif when == "call" or outcome != "passed":
        per[nid] += 1
m = re.search(r"(\d+) subtests passed", summ[-1] if summ else "")
summary_subs = int(m.group(1)) if m else 0
print("suite_subtests     %d subtest reports (%s); summary says %d subtests passed"
      % (sum(subs.values()), dict(subs), summary_subs))
if sum(subs.values()) != summary_subs or subs.get("failed"):
    fails.append("subtests: %d reports %s vs %d in the summary" % (sum(subs.values()), dict(subs), summary_subs))
ids = set(items)
missing = [i for i in items if per.get(i, 0) == 0]
extra = [i for i in per if i not in ids]
multi = [i for i, k in per.items() if k != 1]
ssum = sum(counts[k] for k in ("passed", "skipped", "xfailed", "xpassed", "failed", "errors"))
print("suite_accounting   %d items -> %d with exactly one outcome; %d missing, %d uncollected, %d multiple"
      % (len(items), sum(1 for i in items if per.get(i) == 1), len(missing), len(extra), len(multi)))
print("suite_summary_sum  %d = %d items + %d collection-time skip(s) %s"
      % (ssum, len(items), len(cskips), ", ".join(c[:60] for c in cskips)))
if int(rc) != 0: fails.append("pytest exited %s" % rc)
if counts["failed"]: fails.append("%d tests FAILED" % counts["failed"])
if counts["errors"]: fails.append("%d ERRORS" % counts["errors"])
if len(ids) != len(items): fails.append("duplicate collected ids")
if missing: fails.append("%d collected tests never reported, e.g. %s" % (len(missing), missing[0][:100]))
if extra: fails.append("%d reports for tests that were not collected" % len(extra))
if multi: fails.append("%d tests reported more than one outcome" % len(multi))
if ssum != len(items) + len(cskips):
    fails.append("summary %d != %d items + %d collection-time skips" % (ssum, len(items), len(cskips)))
# A floor against a collapsing collection, from the first real run.
if len(items) < FLOOR:
    fails.append("only %d items collected; this pin collects %s" % (len(items), MEASURED))
reasons = collections.Counter()
for num, why in re.findall(r"SKIPPED \[(\d+)\] [^:]+:\d+: (.*)", log):
    reasons[re.sub(r"\d+", "N", why)[:90]] += int(num)
print("top skip reasons (so the skips are explained, not waved through):")
for why, k in reasons.most_common(10):
    print("  %6d  %s" % (k, why))
if fails:
    print("FAIL: " + "; ".join(fails)); sys.exit(1)
print("SUITE OK")
