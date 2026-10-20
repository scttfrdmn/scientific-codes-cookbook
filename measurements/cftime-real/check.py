#!/usr/bin/env python3
"""Assert what decode.py measured, and summarise it.

    python3 -u check.py <catalog.csv.gz> <decode outdir>

Fails on: a catalog store missing or repeated in the results, a store decode.py could not
process, a value cftime refused, an inexact integer round trip, a float round trip off by more
than cftime's 1 us resolution or not stable on re-decode, any value where cftime differs from
the exact-integer referee or from xarray's decode, any step where cftime's own datetime
subtraction differs from the stored step, or a control (wrong calendar) that fails to change
an axis spanning a year or more. What the archive says about itself is printed, not asserted.
Writes summary.tsv; prints CFTIME REAL OK last.
"""
import collections
import csv
import gzip
import hashlib
import json
import os
import sys

cat, out = sys.argv[1], sys.argv[2]
ROWS = []


def rec(k, v):
    v = str(v)
    print("%s\t%s" % (k, v), flush=True)
    ROWS.append((k, v))


def die(msg):
    with open(os.path.join(out, "summary.tsv"), "w") as f:
        f.writelines("%s\t%s\n" % r for r in ROWS)
    print("FAIL: %s" % msg, flush=True)
    sys.exit(1)


def sha(p):
    return hashlib.sha256(open(p, "rb").read()).hexdigest()


catalog = sorted(r["zstore"] for r in csv.DictReader(gzip.open(cat, "rt")))
stores = [json.loads(l) for l in open(os.path.join(out, "stores.jsonl"))]
vars_ = [json.loads(l) for l in open(os.path.join(out, "vars.jsonl"))]
rec("stores_jsonl_sha256", sha(os.path.join(out, "stores.jsonl")))
rec("vars_jsonl_sha256", sha(os.path.join(out, "vars.jsonl")))

# ------------------------------------------------------------------ accounting
rec("catalog_stores", len(catalog))
if [s["z"] for s in stores] != catalog:
    die("the results do not cover the catalog's stores exactly once each")
kinds = collections.Counter(s["kind"] for s in stores)
for k in ("time", "no-time", "no-zmetadata", "defective", "error"):
    rec("stores_%s" % k, kinds.get(k, 0))
if kinds.get("error"):
    die("%d stores failed in decode.py, e.g. %s" % (kinds["error"], next(s for s in stores if s["kind"] == "error")))
fetch_err = sum(1 for s in stores if s["status"] == "error")
rec("stores_fetch_error", fetch_err)
if fetch_err:
    die("%d stores were not fetched (status error in the staged axes)" % fetch_err)
for k, n in sorted(collections.Counter(s["defect"] for s in stores if s["kind"] == "defective").items()):
    rec("defective_%s" % k, n)
nz = collections.Counter(s["status"] for s in stores if s["kind"] == "no-zmetadata")
rec("no_zmetadata_http_status", dict(sorted(nz.items())) or "none")

tstores = [s for s in stores if s["kind"] == "time"]
rec("models", len({s.get("source_id") for s in tstores}))
rec("time_variables", sum(1 for v in vars_ if v["var"] == "time"))
rec("bounds_variables", sum(1 for v in vars_ if v["var"] != "time"))
nval = sum(v["n"] - v.get("masked", 0) for v in vars_)
rec("time_values_read", nval)
rec("time_values_masked", sum(v.get("masked", 0) for v in vars_))

st = collections.Counter(v["status"] for v in vars_)
rec("var_status", dict(sorted(st.items())))
refused = [v for v in vars_ if v["status"].startswith("cftime-refused")]
rec("refused_vars", len(refused))
rec("refused_vars_justified", sum(1 for v in refused if v.get("refusal_justified")))
rec("refused_vars_xarray_also_refused", sum(1 for v in refused if v.get("xr_refused")))
rec("refused_vars_bytes_are_float64", sum(1 for v in refused if v.get("bytes_are_plausible_f8")))
rec("refused_stores", len({v["z"] for v in refused}))
rec("refused_models", len({v["z"].split("/")[5] + "/" + v["z"].split("/")[6] for v in refused}))
bad = [v for v in refused if not (v.get("refusal_justified") and v.get("xr_refused"))]
if bad:
    die("%d refusals are not justified by int64 overflow or xarray decoded them, e.g. %s" % (len(bad), bad[0]))
noref = [v for v in vars_ if v["status"] == "no-referee"]
rec("no_referee_vars", len(noref))
for v in noref[:5]:
    rec("  no_referee_example", "%s %s units=%r calendar=%r" % (v["z"], v["var"], v.get("units"), v.get("calendar")))
ok = [v for v in vars_ if v["status"] == "ok"]
rec("values_checked", sum(v["n"] - v["masked"] for v in ok))

# ------------------------------------------------------------------ per-value assertions
def total(k):
    return sum(v.get(k, 0) for v in ok)


for k, what in (("rt_bad", "round trip"), ("rt_redecode_bad", "re-decode of the round trip"),
                ("ref_bad", "cftime vs the exact-integer referee"),
                ("xr_bad", "xarray use_cftime=True vs cftime"),
                ("xrdef_bad", "xarray default (cftime fallback) vs cftime"),
                ("diff_bad", "cftime datetime subtraction vs the stored step")):
    n = total(k)
    rec(k, n)
    if n:
        bad = next(v for v in ok if v.get(k))
        die("%d values fail %s, e.g. %s" % (n, what, json.dumps(bad, sort_keys=True)))

rec("xr_undecoded_climatology_vars", sum(1 for v in ok if v.get("xr_undecoded")))
rec("xr_undecoded_climatology_stores", len({v["z"] for v in ok if v.get("xr_undecoded")}))
rec("vars_units_inherited", sum(1 for v in ok if v.get("units_inherited")))
ints = [v for v in ok if v["dtype"][1] in "iu"]
flts = [v for v in ok if v["dtype"][1] == "f"]
rec("int_values_exact_roundtrip", sum(v["n"] - v["masked"] for v in ints))
rec("float_values", sum(v["n"] - v["masked"] for v in flts))
rec("float_values_inexact_roundtrip", total("rt_inexact"))
rec("float_vars_inexact_roundtrip", sum(1 for v in flts if v.get("rt_inexact")))
rec("float_roundtrip_max_us", "%.6g" % max([v.get("rt_max_us", 0.0) for v in flts] or [0.0]))
dt64 = [v for v in ok if str(v.get("xrdef", "")).startswith("datetime64")]
rec("xrdef_datetime64_values", sum(v["n"] - v["masked"] for v in dt64))
rec("xrdef_datetime64_units", dict(sorted(collections.Counter(v["xrdef"] for v in dt64).items())))
# xarray's own datetime64 path is REPORTED: it is pandas arithmetic, not cftime's
offx = [v for v in dt64 if v.get("xrdef_outside")]
rec("REPORTED_xrdef_values_off_exact", sum(v["xrdef_outside"] for v in offx))
rec("REPORTED_xrdef_vars_off_exact", len(offx))
rec("REPORTED_xrdef_values_off_by_gt_1s", sum(v.get("xrdef_outside_by_gt_1s", 0) for v in offx))
rec("REPORTED_xrdef_stores_off_by_gt_1s", len({v["z"] for v in offx if v.get("xrdef_outside_by_gt_1s")}))
rec("REPORTED_xrdef_max_err_s_within_1s", "%.3g" % max([v["xrdef_max_err_s"] for v in offx if not v.get("xrdef_outside_by_gt_1s")] or [0]))
for v in [v for v in offx if v.get("xrdef_outside_by_gt_1s")][:5]:
    rec("  REPORTED_xrdef_example", "%s %s %d values, max err %.4g s" % (v["z"], v["var"], v["xrdef_outside_by_gt_1s"], v["xrdef_max_err_s"]))
rec("xrdef_cftime_values", sum(v["n"] - v["masked"] for v in ok if v.get("xrdef") == "cftime"))

span = [v for v in ok if v.get("span_ge_1y")]
noctl = [v for v in span if "control" not in v]
rec("control_vars_spanning_1y", len(span))
rec("control_vars_no_other_calendar", len(noctl))
miss = [v for v in span if "control" in v and not v.get("control_differs")]
rec("control_vars_unchanged", len(miss))
if miss:
    die("the wrong calendar left %d year-long axes unchanged, e.g. %s" % (len(miss), miss[0]))
if len(noctl) > 0:
    die("%d year-long axes had no control calendar, e.g. %s" % (len(noctl), noctl[0]))

# ------------------------------------------------------------------ what the archive says
cal = collections.Counter()
calm = collections.defaultdict(set)
tv = {(v["z"]): v for v in ok if v["var"] == "time"}
for s in tstores:
    v = tv.get(s["z"])
    if v:
        c = str(v["calendar"]).lower()
        cal[c] += 1
        calm[c].add(s.get("source_id"))
for c, n in cal.most_common():
    rec("calendar_%s" % c, "%d stores, %d models" % (n, len(calm[c])))
mism = sum(1 for s in tstores if s.get("bounds_calendar"))
rec("stores_bounds_in_another_calendar_or_shape", mism)
rec("stores_monotonic", sum(1 for s in tstores if s.get("monotonic") == 1))
rec("stores_not_monotonic", sum(1 for s in tstores if s.get("monotonic") == 0))
rec("stores_with_duplicate_times", sum(1 for s in tstores if s.get("dups")))
wb = [s for s in tstores if "n_bounds_pairs" in s]
rec("stores_bounds_checked", len(wb))
rec("stores_every_time_inside_its_bounds", sum(1 for s in wb if s["in_bounds"] == s["n_bounds_pairs"]))
rec("stores_bounds_contiguous", sum(1 for s in wb if s["contiguous"] == s["n_bounds_pairs"] - 1))
yrs = [int(v["first"][:4]) for v in ok if "first" in v] + [int(v["last"][:4]) for v in ok if "last" in v]
rec("year_range", "%d .. %d" % (min(yrs), max(yrs)))
rec("distinct_decoded_axes", len({v["digest"] for v in ok if v["var"] == "time"}))

with open(os.path.join(out, "summary.tsv"), "w") as f:
    f.writelines("%s\t%s\n" % r for r in ROWS)
print("CFTIME REAL OK", flush=True)
