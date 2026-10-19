#!/usr/bin/env bash
# Pull each chip's evidence into results/<chip>/ (not the big hit tables) and compare every chip
# against Graviton4. The self-hit counts are algorithmic and asserted equal; the hit-table and
# DIAMOND-table bytes are compared and reported.
#   bash measurements/blast-real/collect.sh <bucket> <run label> <task nonce>
set -euo pipefail
B="${1:?bucket}"; RUN="${2:?run label}"; NONCE="${3:?nonce}"
HERE="$(cd "$(dirname "$0")" && pwd)"
ACCT=$(aws sts get-caller-identity --query Account --output text)
for chip in arm64 x86_64 amd; do
  case $chip in arm64) p=runs; sfx=$NONCE ;; *) p=runs-$chip; sfx=${chip/_/}-$NONCE ;; esac
  for t in blast diamond; do
    aws s3 ls "s3://$B/$p/blast-real/$RUN/$t/check.txt" >/dev/null 2>&1 || { echo "$chip $t: no run"; continue; }
    mkdir -p "$HERE/results/$chip/$t"
    aws s3 cp --recursive --only-show-errors --exclude '*.gz' --exclude 'queries.fa' \
      --exclude 'blast_self.tsv' --exclude 'queries.tsv' "s3://$B/$p/blast-real/$RUN/$t/" "$HERE/results/$chip/$t/"
    aws s3 cp --only-show-errors "s3://spawn-results-$ACCT-us-west-2/tasks/cookbook-blast-real-$t-$RUN-$sfx/completion.json" \
      "$HERE/results/$chip/$t/completion.json" || true
  done
done
python3 -I - "$HERE/results" <<'PY'
import json, os, sys
from datetime import datetime
R = sys.argv[1]
chips = [c for c in ("arm64", "x86_64", "amd") if os.path.exists(os.path.join(R, c, "blast", "check.txt"))]
def kv(p):
    d = {}
    for l in open(p):
        f = l.split()
        if len(f) >= 2:
            d[f[0]] = f[1]
    return d
def when(s):
    return datetime.strptime(s.split(".")[0].rstrip("Z"), "%Y-%m-%dT%H:%M:%S")
out = []
def line(s):
    out.append(s); print(s)
fail = []
data = {}
for c in chips:
    for t in ("blast", "diamond"):
        d = os.path.join(R, c, t)
        if not os.path.exists(os.path.join(d, "check.txt")):
            continue
        k = kv(os.path.join(d, "check.txt"))
        j = json.load(open(os.path.join(d, "completion.json")))
        win = (when(j["ended_at"]) - when(j["started_at"])).total_seconds()
        hr = j["cost_estimate"] / win * 3600
        wall = float(k.get("blastp_wall_s", k.get("diamond_search_s", "nan")))
        mem = open(os.path.join(d, "memory-blastp.txt")).read().split()[1] if t == "blast" else \
            open(os.path.join(d, "timing.txt")).read().split()[7]
        h = dict(l.rstrip("\n").split("\t", 1) for l in open(os.path.join(d, "host.txt")) if "\t" in l)
        data[(c, t)] = k
        line("%-7s %-8s %-26s tpc %s  wall %6.0f s  window %5.0f s  $/hr %.4f  compute $ %.4f  billed $ %.4f  "
             "vmhwm %.2f GiB  %s" % (c, t, h.get("cpu_model", "Graviton4 cpu_part " + h.get("cpu_part", "?")), h.get("threads_per_core"), wall, win, hr,
                                     wall / 3600 * hr, j["cost_estimate"], int(mem) / 2**20, j.get("state")))
KEYS = {"blast": ["db_sequences", "db_letters", "query_oids", "query_letters", "queries_with_hit", "self_rows",
                  "self_100pct", "full_length_self", "self_is_top_score", "hit_rows", "hits_sha256",
                  "queries_sha256", "swissprot_fa_sha256"],
        "diamond": ["queries_with_hit", "self_rows", "self_100pct", "full_length_self", "self_both",
                    "self_blast_only", "self_diamond_only", "hit_rows", "hits_sha256"]}
EXACT = {"db_sequences", "db_letters", "query_oids", "query_letters", "queries_sha256", "swissprot_fa_sha256",
         "self_rows", "self_100pct", "queries_with_hit"}
for t in ("blast", "diamond"):
    if ("arm64", t) not in data:
        continue
    ref = data[("arm64", t)]
    for c in chips[1:]:
        if (c, t) not in data:
            continue
        for key in KEYS[t]:
            a, b = ref.get(key), data[(c, t)].get(key)
            same = a == b
            tag = ("ok" if same else "FAIL") if (key in EXACT and t == "blast") else ("same" if same else "differs")
            if tag == "FAIL":
                fail.append("%s %s %s" % (c, t, key))
            line("%-7s %-8s %-20s %s vs Graviton4 %s  [%s]" % (c, t, key, b, a, tag))
line("CROSS-CHIP PASSED" if not fail else "CROSS-CHIP FAILED %s" % fail)
open(os.path.join(R, "crosschip.txt"), "w").write("\n".join(out) + "\n")
sys.exit(1 if fail else 0)
PY
