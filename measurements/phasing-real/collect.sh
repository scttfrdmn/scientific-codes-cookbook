#!/usr/bin/env bash
# Pull each chip's evidence into results/<chip>/ (the phased VCFs stay in the bucket; their
# record bodies are md5-compared), plus spawn's completion record, and write results/crosschip.txt.
#   bash measurements/phasing-real/collect.sh <bucket> <nonce>
set -euo pipefail
B="${1:?bucket}"; N="${2:?task nonce}"
HERE="$(cd "$(dirname "$0")" && pwd)"
ACCT=$(aws sts get-caller-identity --query Account --output text)
R="s3://spawn-results-$ACCT-us-west-2/tasks"
mkdir -p "$HERE/results/prep"
aws s3 cp --recursive --only-show-errors --exclude '*.bam' --exclude '*.bai' --exclude '*.vcf.gz' \
  "s3://$B/inputs/phasing-real/prep/" "$HERE/results/prep/"
for chip in arm64 x86_64 amd; do
  case $chip in arm64) p=runs; id=$N ;; *) p=runs-$chip; id=${chip/_/}-$N ;; esac
  aws s3 ls "s3://$B/$p/phasing-real/phase/checks.tsv" >/dev/null 2>&1 || { echo "$chip: no run"; continue; }
  mkdir -p "$HERE/results/$chip"
  aws s3 cp --recursive --only-show-errors --exclude "*.vcf.gz" "s3://$B/$p/phasing-real/phase/" "$HERE/results/$chip/"
  aws s3 cp --only-show-errors "$R/cookbook-phasing-real-phase-$id/completion.json" "$HERE/results/$chip/completion.json"
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
# fixed points: every count, rate and output body; the inputs too, since they must be the same bytes
FIXED = ["input_records", "input_het", "hifi_phased_het", "ilmn_phased_het", "hifi_phase_sets", "ilmn_phase_sets",
         "hifi_stats_bp_per_block_median", "ilmn_stats_bp_per_block_median", "hifi_stats_block_n50",
         "ilmn_long_joins", "ilmn_long_joins_consistent_with_hifiasm","hifi_body_md5", "ilmn_body_md5",
         "hifi_reads_covering_variants", "ilmn_reads_covering_variants", "hifi_reads_selected", "ilmn_reads_selected"]
out, data, fail = [], {}, []
for c in ("arm64", "x86_64", "amd"):
    d = os.path.join(R, c)
    if not os.path.exists(os.path.join(d, "results.tsv")):
        continue
    k = kv(os.path.join(d, "results.tsv")); data[c] = k
    t = kv(os.path.join(d, "timings.tsv"))
    j = json.load(open(os.path.join(d, "completion.json")))
    win = (when(j["ended_at"]) - when(j["started_at"])).total_seconds()
    hr = j["cost_estimate"] / win * 3600
    tool = sum(float(v) for n, v in t.items() if n != "phase_ilmn" and n != "phase_hifi") \
        + max(float(t["phase_hifi"]), float(t["phase_ilmn"]))
    nfail = sum(1 for l in open(os.path.join(d, "checks.tsv")) if l.rstrip("\n").split("\t")[3] != "ok")
    out.append("%-7s %-30s tpc %s  hifi %5s s  ilmn %5s s  tool %5.0f s  window %5.0f s  $/hr %.4f  compute $ %.4f  "
               "billed $ %.4f  rss hifi %s / ilmn %s GB  cgroup %s MiB  %s  failed checks %d" % (
                   c, k.get("cpu_model"), k.get("threads_per_core"), t["phase_hifi"], t["phase_ilmn"], tool, win, hr,
                   tool / 3600 * hr, j["cost_estimate"], k.get("hifi_max_memory_gb"), k.get("ilmn_max_memory_gb"),
                   k.get("cgroup_memory_peak_mib"), j.get("state"), nfail))
cmpkeys = sorted(x for x in data.get("arm64", {}) if "_vs_" in x)
for c in ("x86_64", "amd"):
    if c not in data or "arm64" not in data:
        continue
    for key in FIXED + cmpkeys:
        a, b = data["arm64"].get(key), data[c].get(key)
        if a != b:
            fail.append("%s %s" % (c, key))
        out.append("%-7s %-46s %s vs Graviton4 %s  [%s]" % (c, key, b, a, "same" if a == b else "DIFFERS"))
out.append("ALL AGREE WITH GRAVITON4" if not fail else "DIFFER FROM GRAVITON4: %s" % fail)
print("\n".join(out))
open(os.path.join(R, "crosschip.txt"), "w").write("\n".join(out) + "\n")
PY
