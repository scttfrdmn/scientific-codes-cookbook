#!/usr/bin/env bash
# Pull each chip's evidence into results/<chip>/ and compare every chip with Graviton4.
#
#   bash measurements/calculix-real/collect.sh <bucket> <task nonce>
set -euo pipefail
BUCKET="${1:?pass your bucket}"
NONCE="${2:?pass the nonce the specs were resolved with}"
HERE="$(cd "$(dirname "$0")" && pwd)"
ACCT=$(aws sts get-caller-identity --query Account --output text)
for chip in arm64 x86_64 amd; do
  case $chip in arm64) p=runs; id=cookbook-calculix-real-real-$NONCE ;; *) p=runs-$chip; id=cookbook-calculix-real-real-${chip/_/}-$NONCE ;; esac
  aws s3 ls "s3://$BUCKET/$p/calculix-real/real/score.tsv" >/dev/null 2>&1 || { echo "$chip: no run yet"; continue; }
  mkdir -p "$HERE/results/$chip"
  aws s3 cp --recursive --only-show-errors --exclude '*.frd.gz' --exclude '*.dat' "s3://$BUCKET/$p/calculix-real/real/" "$HERE/results/$chip/"
  # the .dat files (4.5 MB across chips) and the finest stress field (17 MB per chip) are compared
  # below from scratch, not kept in git
  mkdir -p "${TMPDIR:-/tmp}/calculix-real-dat/$chip"
  aws s3 cp --recursive --only-show-errors --exclude '*' --include '*.dat' "s3://$BUCKET/$p/calculix-real/real/" "${TMPDIR:-/tmp}/calculix-real-dat/$chip/"
  aws s3 cp --only-show-errors "s3://spawn-results-$ACCT-us-west-2/tasks/$id/completion.json" "$HERE/results/$chip/completion.json"
  aws s3 cp --only-show-errors "s3://$BUCKET/$p/calculix-real/real/le10_finest_stress.frd.gz" "${TMPDIR:-/tmp}/calculix-real-$chip.frd.gz"
done
python3 -I - "$HERE/results" "${TMPDIR:-/tmp}" <<'PY'
import gzip, json, os, re, sys
from datetime import datetime
R, T = sys.argv[1], sys.argv[2]
when = lambda s: datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ")
chips = [c for c in ("arm64", "x86_64", "amd") if os.path.exists(os.path.join(R, c, "score.tsv"))]
kv = lambda p: dict(l.split("\t", 1) for l in open(p).read().split("\n")[1:] if "\t" in l)
lad, sc = {}, {}
for c in chips:
    d = os.path.join(R, c)
    sc[c] = kv(os.path.join(d, "score.tsv"))
    lad[c] = json.load(open(os.path.join(d, "ladder.json")))
    tim = dict(l.split("\t") for l in open(os.path.join(d, "times.tsv")).read().split("\n") if l)
    host = open(os.path.join(d, "host.txt")).read()
    tpc = [l.split(":")[1].strip() for l in host.splitlines() if l.startswith("Thread(s) per core")]
    blas = [l.split("\t")[1] for l in host.splitlines() if l.startswith("openblas_corename")]
    j = json.load(open(os.path.join(d, "completion.json")))
    win = (when(j["ended_at"]) - when(j["started_at"])).total_seconds()
    hr = j["cost_estimate"] / win * 3600
    tool = float(tim["le10_s"])
    ccx = sum(r["wall_s"] for r in lad[c]["runs"])
    print("%-7s %s  tool %4.0f s (ccx %4.0f s)  window %4.0f s  $/hr %.4f  compute $ %.4f  billed $ %.4f  "
          "cgroup %.1f GiB  max ccx rss %.1f GiB  tpc %s  blas %s  failed=%s"
          % (c, j["state"], tool, ccx, win, hr, tool / 3600 * hr, j["cost_estimate"],
             float(tim["cgroup_memory_peak_bytes"]) / 2 ** 30, max(r["maxrss_gib"] for r in lad[c]["runs"]),
             tpc[0] if tpc else "?", blas[0] if blas else "?", sc[c]["failed_checks"]))
    for r in lad[c]["runs"]:
        print("    %-24s %8s eq  %7.1f s  %6.2f GiB  syy_D %.5e" % (r["tag"], r["equations"], r["wall_s"], r["maxrss_gib"], r.get("syy_D", float("nan"))))
def parse_dat(path):
    blocks, cur = {}, None
    for line in open(path):
        if " for set " in line:
            cur = (line.split("(")[0].strip(), line.split(" for set ")[1].split()[0].upper()); blocks[cur] = []
        elif cur and re.match(r"^\s*-?\d", line):
            blocks[cur].append([float(x) for x in line.split()])
    return blocks
def dat_compare(pa, pb):
    # every printed number, scaled by its block's largest magnitude (le10.py's thread-invariance rule)
    ba, bb = parse_dat(pa), parse_dat(pb)
    vals = lambda row: row[1:] if len(row) == 4 else row
    n = nd = 0; worst = 0.0
    for k, rows in ba.items():
        scale = max((abs(x) for row in rows for x in vals(row)), default=0.0) or 1.0
        if len(rows) != len(bb.get(k, [])): worst = float("inf")
        for ra, rb in zip(rows, bb.get(k, [])):
            for x, y in zip(vals(ra), vals(rb)):
                n += 1
                if x != x or y != y: worst = float("inf")
                elif x != y: nd += 1; worst = max(worst, abs(x - y) / scale)
    return n, nd, worst
for c in chips[1:]:
    a = {r["tag"]: r for r in lad["arm64"]["runs"]}
    print("== %s vs arm64" % c)
    wsp = 0.0
    for r in lad[c]["runs"]:
        b = a[r["tag"]]
        n, nd, w = dat_compare(os.path.join(T, "calculix-real-dat", "arm64", "le10_%s.dat" % r["tag"]), os.path.join(T, "calculix-real-dat", c, "le10_%s.dat" % r["tag"]))
        rs = abs(r["syy_D"] - b["syy_D"]) / abs(b["syy_D"])
        if r["solver"] == "SPOOLES": wsp = max(wsp, w, rs)
        print("  %-24s syy_D rel %.2e  .dat: %6d numbers, %5d differ, worst %.2e of block max" % (r["tag"], rs, n, nd, w))
    print("  CROSS_CHIP_SPOOLES_WORST %.3e (bound 1e-5) %s" % (wsp, "PASS" if wsp < 1e-5 else "FAIL"))
    fa, fb = os.path.join(T, "calculix-real-arm64.frd.gz"), os.path.join(T, "calculix-real-%s.frd.gz" % c)
    if os.path.exists(fa) and os.path.exists(fb):
        def stress(p):
            out, on = {}, False
            for line in gzip.open(p, "rt"):
                if line.startswith(" -4") and "STRESS" in line: on = True
                elif on and line.startswith(" -3"): break
                elif on and line.startswith(" -1"): out[int(line[3:13])] = line[13:85]
            return out
        sa, sb = stress(fa), stress(fb)
        nd = sum(1 for n in sa if sa[n] != sb.get(n))
        md = 0.0
        smax = max(abs(float(v[12 * q:12 * q + 12])) for v in sa.values() for q in range(6))
        for n in sa:
            if sa[n] != sb.get(n):
                md = max(md, max(abs(float(sa[n][12 * q:12 * q + 12]) - float(sb[n][12 * q:12 * q + 12])) for q in range(6)))
        print("  finest stress field: %d nodes, %d differ in any printed digit, max |diff| %.3e MPa (field max %.3e)" % (len(sa), nd, md, smax))
PY
