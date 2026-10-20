#!/usr/bin/env python3
"""Pull each chip's evidence into results/<chip>/, tabulate it, and compare every chip with Graviton4.
    python3 collect.py <bucket> <run label> <arm64 nonce> <x86_64 nonce> <amd nonce>
Decoding is integer arithmetic, so the decoded coordinates are a fixed point and must be identical on
every chip (asserted). The analyses are float32 kernels and are compared, digest by digest; where a
digest differs the per-frame difference is reported, not asserted."""
import glob
import json
import os
import subprocess
import sys
from datetime import datetime

import numpy as np

B, RUN = sys.argv[1:3]
NONCE = dict(zip(("arm64", "x86_64", "amd"), sys.argv[3:6]))
HERE = os.path.dirname(os.path.abspath(__file__))
R = os.path.join(HERE, "results")
SCR = os.path.join(os.environ.get("XTMP", os.path.expanduser("~/.xtmp")), "mdtraj-real", "arrays")
ACCT = subprocess.check_output(["aws", "sts", "get-caller-identity", "--query", "Account", "--output", "text"],
                               text=True).strip()
NAME = {"arm64": "graviton4", "x86_64": "intel", "amd": "amd"}
KEEP = ["check.txt", "host.txt", "real.log", "results.json"]
chips = []
for c in ("arm64", "x86_64", "amd"):
    p, sfx = ("runs", NONCE[c]) if c == "arm64" else ("runs-" + c, c.replace("_", "") + "-" + NONCE[c])
    base = "s3://%s/%s/mdtraj-real/%s/" % (B, p, RUN)
    if subprocess.call(["aws", "s3", "ls", base + "check.txt"], stdout=subprocess.DEVNULL) != 0:
        print("%s: no run" % c)
        continue
    d = os.path.join(R, NAME[c])
    os.makedirs(d, exist_ok=True)
    for f in KEEP:
        subprocess.call(["aws", "s3", "cp", "--only-show-errors", base + f, os.path.join(d, f)])
    subprocess.check_call(["aws", "s3", "cp", "--only-show-errors", "--recursive", base + "arrays/",
                           os.path.join(SCR, NAME[c]) + "/"])
    subprocess.call(["aws", "s3", "cp", "--only-show-errors",
                     "s3://spawn-results-%s-us-west-2/tasks/cookbook-mdtraj-real-%s-%s/completion.json" % (ACCT, RUN, sfx),
                     os.path.join(d, "completion.json")])
    chips.append(NAME[c])


def kv(p):
    d = {}
    for line in open(p, encoding="utf-8"):
        if "\t" in line:
            k, v = line.rstrip("\n").split("\t", 1)
            d[k] = v
    return d


def when(s):
    return datetime.strptime(s.split(".")[0].rstrip("Z"), "%Y-%m-%dT%H:%M:%S")


out, fail = [], []


def line(s):
    out.append(s)
    print(s)


res = {}
for c in chips:
    h = kv(os.path.join(R, c, "host.txt"))
    j = json.load(open(os.path.join(R, c, "completion.json")))
    r = res[c] = json.load(open(os.path.join(R, c, "results.json")))
    win = (when(j["ended_at"]) - when(j["started_at"])).total_seconds()
    hr = j["cost_estimate"] / win * 3600
    tool = float(h["tool_wall_s"])
    frames = sum(f["n_frames"] for f in r["files"])
    line("%-9s %-28s tpc %s  tool %5.0f s  window %5.0f s  $/hr %.4f  compute $ %.4f  billed $ %.4f  "
         "cgroup %s GiB  worker rss %.2f GiB  %.0f frames/s  openblas %s  %s %s" % (
             c, h.get("Model name"), h.get("Thread(s) per core"), tool, win, hr, tool / 3600 * hr, j["cost_estimate"],
             h.get("cgroup_memory_peak_gib"), max(f["worker_maxrss_gib"] for f in r["files"]), frames / tool,
             h.get("openblas_corename"), j.get("state"), j.get("exit_code")))

DIGESTS = ["xyz_sha", "box_sha", "rmsd_sha", "rg_sha", "phi_sha", "psi_sha", "dist_sha", "dssp_sha", "sasa_sha"]
ref = chips[0]
pairs = [(ref, c) for c in chips[1:]] + ([("intel", "amd")] if {"intel", "amd"} <= set(chips) else [])
for a, b in pairs:
    fa = {f["file"]: f for f in res[a]["files"]}
    fb = {f["file"]: f for f in res[b]["files"]}
    if sorted(fa) != sorted(fb):
        fail.append("%s vs %s: different file sets" % (b, a))
        continue
    for k in DIGESTS:
        same = sum(fa[x][k] == fb[x][k] for x in fa)
        msg = "%s vs %s: %-9s identical on %d of %d files" % (b, a, k[:-4], same, len(fa))
        if same < len(fa) and k[:-4] in ("rmsd", "rg"):
            dmax = max(float(np.abs(np.load(os.path.join(SCR, b, x[:-4] + ".npz"))[k[:-4]].astype(np.float64)
                                    - np.load(os.path.join(SCR, a, x[:-4] + ".npz"))[k[:-4]]).max()) for x in fa)
            msg += "; max |diff| %.2e nm" % dmax
        if same < len(fa) and k == "sasa_sha":
            dmax = max(float(np.abs(np.load(os.path.join(SCR, b, x[:-4] + ".npz"))["sasa_total"].astype(np.float64)
                                    - np.load(os.path.join(SCR, a, x[:-4] + ".npz"))["sasa_total"]).max()) for x in fa)
            msg += "; total SASA max |diff| %.2e nm^2" % dmax
        if same < len(fa) and k == "dssp_sha":
            n = tot = 0
            for x in fa:
                da = np.load(os.path.join(SCR, a, x[:-4] + ".npz"))["dssp"]
                db = np.load(os.path.join(SCR, b, x[:-4] + ".npz"))["dssp"]
                n += int((da != db).sum()); tot += da.size
            msg += "; %d of %d residue-frames differ" % (n, tot)
        line(msg)
        if k == "xyz_sha" and same < len(fa):
            fail.append("%s vs %s: decoded coordinates differ on %d files" % (b, a, len(fa) - same))
    ja, jb = res[a].get("jac", {}), res[b].get("jac", {})
    line("%s vs %s: JAC DCD decode %s; worst constraint deviation MDTraj %s, MDAnalysis %s" % (
        b, a, "identical" if ja.get("xyz_sha") == jb.get("xyz_sha") else "DIFFERS",
        "identical" if ja.get("worst_dev_mdtraj") == jb.get("worst_dev_mdtraj") else "differs",
        "identical" if ja.get("worst_dev_mda") == jb.get("worst_dev_mda") else "differs"))
    if ja.get("xyz_sha") != jb.get("xyz_sha"):
        fail.append("%s vs %s: JAC DCD decode differs" % (b, a))
for f in fail:
    line("FAIL " + f)
open(os.path.join(R, "crosschip.txt"), "w").write("\n".join(out) + "\n")
sys.exit(1 if fail else 0)
