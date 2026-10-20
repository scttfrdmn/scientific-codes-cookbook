"""meta.py <metadata.json> <calls.tsv>: summarise one Cromwell run from its own metadata.

Prints `key<TAB>value` lines (workflow status, calls per task, how many were cache hits, the
workflow output) and writes one row per call to <calls.tsv>:
    task  shard  status  cache_hit  seconds  interval_list  output_vcf
Every value is str()-ed before formatting, so describing the run cannot fail it."""
import json
import sys
from datetime import datetime


def secs(c):
    try:
        f = "%Y-%m-%dT%H:%M:%S"
        a = datetime.strptime(str(c.get("start", ""))[:19], f)
        b = datetime.strptime(str(c.get("end", ""))[:19], f)
        return str(int((b - a).total_seconds()))
    except Exception:
        return "NA"


m = json.load(open(sys.argv[1]))
rows = []
for name, calls in sorted(m.get("calls", {}).items()):
    task = name.split(".")[-1]
    for c in calls:
        cc = c.get("callCaching", {}) or {}
        rows.append([task, str(c.get("shardIndex")), str(c.get("executionStatus")),
                     str(bool(cc.get("hit", False))).lower(), secs(c),
                     str((c.get("inputs", {}) or {}).get("interval_list", "-")),
                     str((c.get("outputs", {}) or {}).get("output_vcf", "-"))])
with open(sys.argv[2], "w") as fh:
    for r in sorted(rows, key=lambda r: (r[0], int(r[1]) if r[1].lstrip("-").isdigit() else 0)):
        fh.write("\t".join(r) + "\n")
print("status\t%s" % str(m.get("status")))
for task in sorted({r[0] for r in rows}):
    rs = [r for r in rows if r[0] == task]
    print("%s_calls\t%d" % (task, len(rs)))
    print("%s_done\t%d" % (task, sum(r[2] == "Done" for r in rs)))
    print("%s_cache_hits\t%d" % (task, sum(r[3] == "true" for r in rs)))
    print("%s_shards\t%s" % (task, ",".join(sorted({r[1] for r in rs}, key=lambda s: int(s) if s.lstrip("-").isdigit() else 0))))
outs = m.get("outputs", {}) or {}
print("output_vcf\t%s" % str(outs.get("HaplotypeCallerGvcf_GATK4.output_vcf", "-")))
