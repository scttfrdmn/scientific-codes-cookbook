#!/usr/bin/env bash
# Pull every run's small evidence files (not the reads, the PAF or the assemblies) into results/,
# with each task's spawn completion record, and print the per-chip table and the cross-chip view.
#   bash measurements/polish-real/collect.sh <bucket> <nonce>
set -euo pipefail
B="${1:?bucket}"; NONCE="${2:?task nonce}"
HERE="$(cd "$(dirname "$0")" && pwd)"
R="$HERE/results"
ACCT=$(aws sts get-caller-identity --query Account --output text)
SR="s3://spawn-results-$ACCT-us-west-2/tasks"
SMALL=(--exclude "*.fa" --exclude "*.fasta" --exclude "*.paf" --exclude "*.fastq.gz" --exclude "*.tar.gz")
for t in reads fetched draft overlap; do
  aws s3 cp --only-show-errors --recursive "${SMALL[@]}" "s3://$B/inputs/polish-real/$t/" "$R/graviton4-once/$t/"
done
for t in prep fetch draft overlap; do
  aws s3 cp --only-show-errors "$SR/cookbook-polish-real-$t-$NONCE/completion.json" "$R/graviton4-once/$t.completion.json" || true
done
for chip in arm64 x86_64 amd; do
  case $chip in arm64) p=runs; sfx=$NONCE ;; *) p=runs-$chip; sfx=${chip/_/}-$NONCE ;; esac
  aws s3 cp --only-show-errors --recursive "${SMALL[@]}" "s3://$B/$p/polish-real/" "$R/$chip/" || true
  for t in racon medaka; do
    aws s3 cp --only-show-errors "$SR/cookbook-polish-real-$t-$sfx/completion.json" "$R/$chip/$t/completion.json" || true
  done
done
python3 -I - "$R" <<'PY'
import json, os, sys
from datetime import datetime
R = sys.argv[1]
def kv(p):
    d = {}
    for l in open(p):
        f = l.split()
        if len(f) >= 2:
            d.setdefault(f[0], " ".join(f[1:]))
    return d
def when(s):
    return datetime.strptime(s.split(".")[0].rstrip("Z"), "%Y-%m-%dT%H:%M:%S")
out = []
def line(s):
    out.append(s); print(s)
def bill(p):
    j = json.load(open(p))
    win = (when(j["ended_at"]) - when(j["started_at"])).total_seconds()
    return j.get("instance_type", "?"), win, j["cost_estimate"] / win * 3600, j["cost_estimate"], j.get("state")
for t, wall_key, f in (("draft", "wall_s", "flye-time.txt"), ("overlap", "minimap2_wall_s", "checks.txt")):
    p = os.path.join(R, "graviton4-once", t + ".completion.json")
    if os.path.exists(p):
        it, win, hr, cost, st = bill(p)
        w = float(kv(os.path.join(R, "graviton4-once", t, f))[wall_key])
        line("once    %-7s %-12s wall %6.0f s  window %5.0f s  $/hr %.4f  compute $ %.4f  billed $ %.4f  %s" % (t, it, w, win, hr, w / 3600 * hr, cost, st))
seen = {}
for chip in ("arm64", "x86_64", "amd"):
    for t, wall_key, mem_key, f in (("racon", "racon_wall_s", "racon_vmhwm_kib", "checks.txt"),
                                    ("medaka", "wall_s", "ru_maxrss_children_kib", "medaka-time.txt")):
        d = os.path.join(R, chip, t)
        if not os.path.exists(os.path.join(d, "completion.json")) or not os.path.exists(os.path.join(d, f)):
            continue
        it, win, hr, cost, st = bill(os.path.join(d, "completion.json"))
        k = kv(os.path.join(d, f)); h = kv(os.path.join(d, "host.txt"))
        w = float(k[wall_key])
        line("%-7s %-7s %-12s tpc %s  wall %6.0f s  window %5.0f s  peak %.2f GiB  $/hr %.4f  compute $ %.4f  billed $ %.4f  %s  [%s]"
             % (chip, t, it, h.get("threads_per_core"), w, win, int(k[mem_key]) / 2**20, hr, w / 3600 * hr, cost, st, h.get("cpu_model")))
        c = kv(os.path.join(d, "checks.txt"))
        seen[(chip, t)] = c.get(t + "_sha256")
for t in ("racon", "medaka"):
    ref = seen.get(("arm64", t))
    for chip in ("x86_64", "amd"):
        if (chip, t) in seen:
            line("%-7s %-7s consensus bytes vs Graviton4: %s" % (chip, t, "identical" if seen[(chip, t)] == ref else "differ"))
for chip in ("arm64", "x86_64", "amd"):
    p = os.path.join(R, chip, "medaka", "score.tsv")
    if os.path.exists(p):
        for l in open(p).read().splitlines()[1:]:
            line("%-7s score  %s" % (chip, l))
open(os.path.join(R, "summary.txt"), "w").write("\n".join(out) + "\n")
PY
