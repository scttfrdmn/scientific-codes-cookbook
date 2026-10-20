#!/usr/bin/env bash
# Pull each chip's evidence into results/<chip>/ with spawn's completion record, plus 00-prep's
# into results/prep/, and write results/runs.txt (one line per chip) and results/crosschip.txt.
#   bash measurements/pangenome-real/collect.sh <bucket> <nonce>
set -euo pipefail
B="${1:?bucket}"; N="${2:?task nonce}"
HERE="$(cd "$(dirname "$0")" && pwd)"
ACCT=$(aws sts get-caller-identity --query Account --output text)
R="s3://spawn-results-$ACCT-us-west-2/tasks"
mkdir -p "$HERE/results/prep"
aws s3 cp --recursive --only-show-errors --exclude '*.gz' "s3://$B/inputs/pangenome-real/prep/" "$HERE/results/prep/"
for chip in arm64 x86_64 amd; do
  case $chip in arm64) p=runs; id=$N ;; *) p=runs-$chip; id=${chip/_/}-$N ;; esac
  aws s3 ls "s3://$B/$p/pangenome-real/giraffe/checks.tsv" >/dev/null 2>&1 || { echo "$chip: no run"; continue; }
  mkdir -p "$HERE/results/$chip"
  aws s3 cp --recursive --only-show-errors "s3://$B/$p/pangenome-real/giraffe/" "$HERE/results/$chip/"
  aws s3 cp --only-show-errors "$R/cookbook-pangenome-real-giraffe-$id/completion.json" "$HERE/results/$chip/completion.json"
done
python3 -I - "$HERE/results" <<'PY'
import json, os, sys
from datetime import datetime
R = sys.argv[1]
def kv(p):
    d = {}
    for l in open(p):
        f = l.rstrip("\n").split("\t")
        if len(f) >= 2:
            d[f[0]] = f[1]
    return d
def when(s):
    return datetime.strptime(s.split(".")[0].rstrip("Z"), "%Y-%m-%dT%H:%M:%S")
# fixed points of the input bytes: graph, identity, every count, the sorted outputs, the index bytes
FIXED = ["graph_nodes", "graph_edges", "graph_length", "graph_samples", "graph_paths", "pairs", "gfa_bytes",
         "gaf_records", "gaf_mapped", "gaf_unmapped", "gaf_mapq30", "gaf_mapq60", "gaf_perfect",
         "gaf_sorted_sha256", "surject_sorted_sha256", "linear_mapped", "giraffe_mapped", "both_mapq30",
         "both_mapq30_ustart_within_5bp", "confident_concordance", "confident_pos_concordance"]
out, data, idx, fail = [], {}, {}, []
for c in ("arm64", "x86_64", "amd"):
    d = os.path.join(R, c)
    if not os.path.exists(os.path.join(d, "results.tsv")):
        continue
    k = kv(os.path.join(d, "results.tsv")); data[c] = k
    t = kv(os.path.join(d, "timings.tsv"))
    idx[c] = open(os.path.join(d, "index.sha256")).read()
    j = json.load(open(os.path.join(d, "completion.json")))
    win = (when(j["ended_at"]) - when(j["started_at"])).total_seconds()
    hr = j["cost_estimate"] / win * 3600
    index = float(t["convert"]) + float(t["autoindex"])
    tool = index + float(t["giraffe"])
    rss = max(int(k[x]) for x in ("convert_vmhwm_kib", "autoindex_vmhwm_kib", "giraffe_vmhwm_kib")) / 2**20
    nfail = sum(1 for l in open(os.path.join(d, "checks.tsv")) if l.rstrip("\n").split("\t")[3] != "ok")
    out.append("%-7s %-8s tpc %s  index %4.0f s  giraffe %4.0f s  surject %4s s  tool %5.0f s  window %5.0f s  "
               "$/hr %.4f  compute $ %.4f  billed $ %.4f  peak rss %.2f GiB (autoindex %.2f, giraffe %.2f)  "
               "cgroup %s MiB  %s  failed checks %d" % (
                   c, k.get("cpu_model"), k.get("threads_per_core"), index, float(t["giraffe"]), t.get("surject"),
                   tool, win, hr, tool / 3600 * hr, j["cost_estimate"], rss, int(k["autoindex_vmhwm_kib"]) / 2**20,
                   int(k["giraffe_vmhwm_kib"]) / 2**20, k.get("cgroup_memory_peak_mib"), j.get("state"), nfail))
for c in ("x86_64", "amd"):
    if c not in data or "arm64" not in data:
        continue
    for key in FIXED:
        a, b = data["arm64"].get(key), data[c].get(key)
        if a != b:
            fail.append("%s %s" % (c, key))
        out.append("%-7s %-34s %s vs Graviton4 %s  [%s]" % (c, key, b, a, "same" if a == b else "DIFFERS"))
    same = idx[c] == idx["arm64"]
    # reported, not asserted: the distance and minimizer indexes differ run to run on one chip
    out.append("%-7s %-34s [%s, reported]" % (c, "index files sha256", "same" if same else "differ"))
out.append("ALL AGREE WITH GRAVITON4" if not fail else "DIFFER FROM GRAVITON4: %s" % fail)
print("\n".join(out))
open(os.path.join(R, "crosschip.txt"), "w").write("\n".join(out) + "\n")
PY
