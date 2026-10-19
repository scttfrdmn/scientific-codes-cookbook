#!/usr/bin/env bash
# Pull each chip's evidence into results/<chip>/ and compare every chip with Graviton4.
#
#   bash measurements/fenicsx-real/collect.sh <bucket> <task nonce>
set -euo pipefail
BUCKET="${1:?pass your bucket}"
NONCE="${2:?pass the nonce the specs were resolved with}"
HERE="$(cd "$(dirname "$0")" && pwd)"
ACCT=$(aws sts get-caller-identity --query Account --output text)
for chip in arm64 x86_64 amd; do
  case $chip in arm64) p=runs; id=cookbook-fenicsx-real-real-$NONCE ;; *) p=runs-$chip; id=cookbook-fenicsx-real-real-${chip/_/}-$NONCE ;; esac
  aws s3 ls "s3://$BUCKET/$p/fenicsx-real/real/times.tsv" >/dev/null 2>&1 || { echo "$chip: no run yet"; continue; }
  mkdir -p "$HERE/results/$chip"
  aws s3 cp --recursive --only-show-errors "s3://$BUCKET/$p/fenicsx-real/real/" "$HERE/results/$chip/"
  aws s3 cp --only-show-errors "s3://spawn-results-$ACCT-us-west-2/tasks/$id/completion.json" "$HERE/results/$chip/completion.json"
done
python3 -I - "$HERE/results" <<'PY'
import csv, json, os, sys
from datetime import datetime
R = sys.argv[1]
when = lambda s: datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ")
chips = [c for c in ("arm64", "x86_64", "amd") if os.path.exists(os.path.join(R, c, "times.tsv"))]
kv = lambda p: dict(l.split("\t", 1) for l in open(p).read().split("\n")[1:] if "\t" in l)
rungs, score = {}, {}
for c in chips:
    d = os.path.join(R, c)
    tim = dict(l.split("\t") for l in open(os.path.join(d, "times.tsv")).read().split("\n") if l)
    score[c] = kv(os.path.join(d, "score-par.tsv"))
    rungs[c] = list(csv.DictReader(open(os.path.join(d, "rungs-par.tsv")), delimiter="\t")) + \
               list(csv.DictReader(open(os.path.join(d, "rungs-serial.tsv")), delimiter="\t"))
    j = json.load(open(os.path.join(d, "completion.json")))
    win = (when(j["ended_at"]) - when(j["started_at"])).total_seconds()
    hr = j["cost_estimate"] / win * 3600
    par, ser = float(tim["par_s"]), float(tim["serial_s"])
    fin = [r for r in rungs[c] if r["pass"] == "par" and r["degree"] == "2" and r["n"] == "128"]
    print("%-7s %s  par %4.0f s  serial %4.0f s  window %4.0f s  $/hr %.4f  compute $ %.4f  billed $ %.4f  "
          "rss-sum %s GiB  cgroup %.1f GiB  tpc %s  blas %s"
          % (c, j["state"], par, ser, win, hr, par / 3600 * hr, j["cost_estimate"],
             score[c]["rss_sum_of_rank_peaks_gib"], float(tim["cgroup_memory_peak_bytes"]) / 2 ** 30,
             score[c]["threads_per_core"], score[c]["openblas_corename"]))
    for r in fin:
        print("        finest P2 %-9s its %3s  solve %6s s  setup %5s s" % (r["solver"], r["its"], r["t_solve"], r["t_setup"]))
key = lambda r: (r["pass"], r["case"], r["solver"], r["degree"], r["n"])
for c in chips[1:]:
    a = {key(r): r for r in rungs["arm64"]}
    print("== %s vs arm64" % c)
    worst = {}
    for r in rungs[c]:
        b = a[key(r)]
        for k in ("l2_error", "h1_error"):
            x, y = float(b[k]), float(r[k])
            rel = abs(x - y) / abs(x)
            tag = "exact-case" if r["case"] == "quadratic" and r["degree"] != "1" else "discretisation"
            worst[(tag, k)] = max(worst.get((tag, k), 0.0), rel)
        if b["its"] != r["its"]:
            print("  its differ: %s %s vs %s" % ("/".join(key(r)), b["its"], r["its"]))
    for k, v in sorted(worst.items()):
        print("  worst relative %-28s %.3e" % ("/".join(k), v))
    for k in ("sine_P1_L2_orders", "sine_P2_L2_orders", "sine_P1_H1_orders", "sine_P2_H1_orders",
              "finest_P2_algebraic_fraction", "serial_vs_parallel_worst_relative", "mpi_comm_world_size"):
        print("  %-36s arm64 %-26s %s %s" % (k, score["arm64"][k], c, score[c][k]))
PY
