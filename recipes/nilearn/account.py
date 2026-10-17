"""pytest plugin: account for every collected test, exactly.

"0 failed" alone passes if collection silently shrinks (an optional import that drops a
module becomes fewer items, not a failure), and the summary line cannot be used as the
count: its `skipped` also includes skips raised at COLLECTION time (a module-level
`importorskip`), which are not test items (copied from recipes/scikit-learn, where that
off-by-one was measured).

So this records, from inside pytest, the collected item ids and every per-test report, and
the task asserts: every collected item reports exactly one outcome; nothing reports that was
not collected; and the summary equals items + collection-time skips. Under xdist the report
hook fires on the controller for every worker, so one file sees everything.
"""
import json
import os

_items, _reports, _collect_skips = [], [], []


def pytest_collectreport(report):
    if report.outcome == "skipped":
        _collect_skips.append(report.nodeid)


def pytest_collection_finish(session):
    # After `-m` deselection, which is itself a collection_modifyitems hook: recording there
    # counted deselected items as collected (measured in recipes/scipy-stack -- this plugin's
    # hook ran first). This suite runs three `-m` groups, so it matters here.
    if not hasattr(session.config, "workerinput"):
        _items.extend(i.nodeid for i in session.items)


def pytest_runtest_logreport(report):
    _reports.append((report.nodeid, report.when, report.outcome))


def pytest_sessionfinish(session, exitstatus):
    if hasattr(session.config, "workerinput"):
        return
    with open(os.environ.get("ACCOUNT_OUT", "account.json"), "w") as fh:
        json.dump({"items": _items, "reports": _reports, "collect_skips": _collect_skips}, fh)
