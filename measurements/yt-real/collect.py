"""Pull each chip's evidence into results/<chip>/ and compare every chip against Graviton4.
   AWS_PROFILE=... python3 measurements/yt-real/collect.py <bucket> <run label>
Every check must PASS on every chip. Every observation except timings is compared and each
difference is listed: the reductions are deterministic, so a difference is a finding."""
import json
import os
import subprocess
import sys
from datetime import datetime

B, RUN = sys.argv[1], sys.argv[2]
HERE = os.path.dirname(os.path.abspath(__file__))
R = os.path.join(HERE, "results")
ACCT = subprocess.check_output(["aws", "sts", "get-caller-identity", "--query", "Account",
                                "--output", "text"], text=True).strip()
CHIPS = {"arm64": ("runs", ""), "x86_64": ("runs-x86_64", "x8664-"), "amd": ("runs-amd", "amd-")}
FILES = ["check.tsv", "observations.tsv", "real.log", "host.txt", "footprint.txt"]


def cp(src, dst):
    subprocess.run(["aws", "s3", "cp", "--only-show-errors", src, dst], check=True)


def tsv(p):
    rows = [l.rstrip("\n").split("\t") for l in open(p)][1:]
    return rows


def kv(p):
    return dict(l.rstrip("\n").split("\t", 1) for l in open(p) if "\t" in l)


def when(s):
    return datetime.strptime(s.rstrip("Z").split(".")[0], "%Y-%m-%dT%H:%M:%S")


out, fail, data = [], [], {}


def line(s=""):
    out.append(s)
    print(s)


for chip, (prefix, tag) in CHIPS.items():
    d = os.path.join(R, chip)
    os.makedirs(d, exist_ok=True)
    for f in FILES:
        cp("s3://%s/%s/yt-real/%s/%s" % (B, prefix, RUN, f), os.path.join(d, f))
    cp("s3://spawn-results-%s-us-west-2/tasks/cookbook-yt-real-%s-%s%s/completion.json"
       % (ACCT, RUN, tag, RUN), os.path.join(d, "completion.json"))
    j = json.load(open(os.path.join(d, "completion.json")))
    win = (when(j["ended_at"]) - when(j["started_at"])).total_seconds()
    hr = j["cost_estimate"] / win * 3600
    fp, h = kv(os.path.join(d, "footprint.txt")), kv(os.path.join(d, "host.txt"))
    wall = float(fp["tool_wall_s"])
    rss = [l.split("\t")[1] for l in open(os.path.join(d, "real.log")) if l.startswith("worker_peak_rss_gib")]
    checks = tsv(os.path.join(d, "check.tsv"))
    nf = sum(1 for c in checks if c[2] != "PASS")
    if nf or j["state"] != "completed":
        fail.append("%s: %d failed checks, state %s" % (chip, nf, j["state"]))
    data[chip] = (checks, tsv(os.path.join(d, "observations.tsv")))
    line("%-7s %-26s tpc %s  openblas %-12s wall %4.0f s  window %4.0f s  $/hr %.4f  compute $ %.5f  "
         "billed $ %.5f  worker rss %s GiB  cgroup %s GiB  checks %d/%d" % (
             chip, h.get("Model name", "?"), h.get("Thread(s) per core", "?"),
             h.get("openblas_corename", "?").split()[0], wall, win, hr, wall / 3600 * hr,
             j["cost_estimate"], rss[0] if rss else "?", fp.get("cgroup_peak_gib", "?"),
             len(checks) - nf, len(checks)))

ref_c, ref_o = data["arm64"]
for chip in ("x86_64", "amd"):
    c, o = data[chip]
    rc = {(a, b): v for a, b, _, v in ref_c}
    cc = {(a, b): v for a, b, _, v in c}
    ro = {(a, b): v for a, b, v in ref_o if not b.startswith("t_") and b not in ("output", "error")}
    co = {(a, b): v for a, b, v in o if not b.startswith("t_") and b not in ("output", "error")}
    if set(rc) != set(cc) or set(ro) != set(co):
        fail.append("%s: different check or observation keys" % chip)
    dc = sorted(k for k in rc if rc[k] != cc.get(k))
    do = sorted(k for k in ro if ro[k] != co.get(k))
    line()
    line("%s vs Graviton4: %d/%d check values identical, %d/%d observations identical"
         % (chip, len(rc) - len(dc), len(rc), len(ro) - len(do), len(ro)))
    for k in dc:
        line("  check  %-8s %-34s %s  | Graviton4 %s" % (k[0], k[1], cc.get(k), rc[k]))
    for k in do:
        line("  obs    %-8s %-34s %s  | Graviton4 %s" % (k[0], k[1], co.get(k), ro[k]))
line()
line("ALL CHIPS PASSED" if not fail else "FAILED %s" % fail)
open(os.path.join(R, "crosschip.txt"), "w").write("\n".join(out) + "\n")
sys.exit(1 if fail else 0)
