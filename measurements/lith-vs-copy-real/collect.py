#!/usr/bin/env python3
"""Pull each arm's small evidence into results/<arm id>/ and print one row per arm.

    AWS_PROFILE=... python3 collect.py <bucket> <arm id> [<arm id> ...]     # e.g. manta-copy-a1

Reads runs/lith-vs-copy-real/<id>/arm/ (arm.tsv, data.ls, lith metrics, the container's log),
the workload's own small outputs, and spawn's completion record. Never pulls a VCF, a volume or a
thickness grid. Requests are costed at us-west-2 S3 Standard list prices, as if you owned the
bucket: GET/HEAD $0.0004 per 1,000, LIST $0.005 per 1,000.
"""
import json
import math
import os
import re
import subprocess
import sys
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
GET, LIST = 0.0004 / 1000, 0.005 / 1000
SMALL = ("arm.tsv", "data.ls", "lith-metrics.txt", "container.log", "lith.log", "lith-index.log", "dnf.log",
         "timing.txt", "check.txt", "host.txt", "times.txt", "real.log", "pin.log", "run.log", "summary.json",
         "gfs-cycle.sha256", "gsw-argo.log", "cpu.txt", "gsw-results.tsv", "score.tsv", "workflow.log",
         "delly.log", "pyflow_log.txt", "tmp-links.txt")


def sh(*a):
    return subprocess.run(a, capture_output=True, text=True).stdout


def pull(bucket, rid):
    d = os.path.join(HERE, "results", rid)
    os.makedirs(d, exist_ok=True)
    base = "s3://%s/runs/lith-vs-copy-real/%s/" % (bucket, rid)
    for line in sh("aws", "s3", "ls", "--recursive", base).splitlines():
        key = line.split(None, 3)[3]
        name = os.path.basename(key)
        if name in SMALL:
            sub = "arm" if "/arm/" in key else "out"
            os.makedirs(os.path.join(d, sub), exist_ok=True)
            sh("aws", "s3", "cp", "--only-show-errors", "s3://%s/%s" % (bucket, key), os.path.join(d, sub, name))
    acct = sh("aws", "sts", "get-caller-identity", "--query", "Account", "--output", "text").strip()
    sh("aws", "s3", "cp", "--only-show-errors",
       "s3://spawn-results-%s-us-west-2/tasks/cookbook-lith-vs-copy-real-%s/completion.json" % (acct, rid),
       os.path.join(d, "completion.json"))
    return d


def kv(p):
    out = {}
    if os.path.exists(p):
        for line in open(p, errors="replace"):
            f = line.rstrip("\n").split("\t")
            if len(f) >= 2:
                out.setdefault(f[0].strip(), f[1].strip())
    return out


def first(p, rx, cast=float):
    if not os.path.exists(p):
        return None
    m = re.search(rx, open(p, errors="replace").read(), re.M)
    return cast(m.group(1)) if m else None


def when(s):
    return datetime.strptime(s.rstrip("Z"), "%Y-%m-%dT%H:%M:%S")


def tool(w, o, armlog):
    """(verify-read seconds, tool seconds, checks seconds excluded from $/result, sentinel ok)"""
    if w == "tomography":
        r = os.path.join(o, "real.log")
        g = lambda k: first(r, r"^%s\t([0-9.]+)" % k) or 0.0
        t = g("phase_read_s") + g("phase_centre_s") + g("time_preprocess_s") + g("time_gridrec_s")
        checks = g("phase_calibrate_s") + g("time_compare_s") + g("phase_invariance_s") + g("phase_astra_s")
        return None, t, checks, "TOMOGRAPHY REAL OK" in open(r, errors="replace").read() if os.path.exists(r) else False
    if w == "gfs":
        return (first(os.path.join(o, "pin.log"), r"hashed in ([0-9.]+) s"),
                first(os.path.join(o, "run.log"), r"^workload_wall_s\t([0-9.]+)"), 0.0,
                "GFS THICKNESS REAL OK" in open(os.path.join(o, "run.log"), errors="replace").read())
    if w in ("manta", "delly"):
        tm = kv(os.path.join(o, "timing.txt"))
        t = float(tm["config_s"]) + float(tm["workflow_s"]) if w == "manta" else float(tm.get("delly_s", "nan"))
        ok = "CHECKS PASSED" in open(os.path.join(o, "check.txt"), errors="replace").read()
        return float(tm.get("sha256_s", "nan")), t, 0.0, ok
    if w == "gsw":
        return (first(armlog, r"^sha256 in ([0-9]+)s"), first(armlog, r"^argo\.py exit 0 in ([0-9]+)s"), 0.0,
                "GSW ARGO OK" in open(armlog, errors="replace").read())


def requests_copy(dls, chunk, recursive):
    sizes = [int(l.split()[2]) for l in open(dls) if l.strip()]
    gets = sum(max(1, math.ceil(s / chunk)) for s in sizes)
    lists = math.ceil(len(sizes) / 1000) if recursive else 0
    heads = 0 if recursive else len(sizes)
    return gets + heads, lists


def row(bucket, rid):
    d = pull(bucket, rid)
    w, mode = rid.split("-")[0], rid.split("-")[1]
    a = kv(os.path.join(d, "arm", "arm.tsv"))
    j = json.load(open(os.path.join(d, "completion.json")))
    win = (when(j["started_at"]) and (when(j["ended_at"]) - when(j["started_at"])).total_seconds())
    hr = j["cost_estimate"] / win * 3600
    base = sum(float(re.search(r" ([0-9.]+) s ", " " + a.get(k, "rc 0 0 s ") + " ").group(1))
               for k in ("baseline_default", "baseline_tuned"))
    vr, t, checks, ok = tool(w, os.path.join(d, "out"), os.path.join(d, "arm", "container.log"))
    billed = win - base - checks
    rec = os.path.exists(os.path.join(d, "arm", "data.ls"))
    if mode == "lith":
        rq = dict(x.split("=") for x in a.get("lith_requests", "").split())
        # the index build's own ListObjectsV2 pages are not on the mount's counters
        gets = sum(int(v) for k, v in rq.items() if k != "list")
        lists = int(rq.get("list", 0)) + math.ceil(int(a.get("data_objects", 0)) / 1000)
    else:
        gets, lists = requests_copy(os.path.join(d, "arm", "data.ls"), 16 * 2**20, w in ("gfs", "gsw"))
    # today's stage-in, same objects, at the CLI default 8 MiB part size
    cg, cl = requests_copy(os.path.join(d, "arm", "data.ls"), 8 * 2**20, w in ("gfs", "gsw"))
    return {
        "arm": rid, "state": j["state"], "instance": a.get("instance_type"), "region": a.get("region"),
        "stage_s": a.get("stage_s", "").split()[0], "verify_read_s": vr, "tool_s": t, "checks_s": checks,
        "window_s": win, "baseline_s": round(base, 1), "billed_s": round(billed), "usd_hr": round(hr, 4),
        "billed_usd": round(billed * hr / 3600, 4), "host_mem_peak_gib": round(int(a.get("host_mem_peak_kb", 0)) / 2**20, 1),
        "tmp_peak_gib": round(int(a.get("tmp_peak_bytes", 0)) / 2**30, 1),
        "lith_vmhwm_gib": round(int(a.get("lith_vmhwm_kb") or 0) / 2**20, 2),
        "gets": gets, "lists": lists, "request_usd": round(gets * GET + lists * LIST, 4),
        "control_gets": cg, "control_lists": cl, "control_request_usd": round(cg * GET + cl * LIST, 4),
        "baseline": "%s / %s" % (a.get("baseline_default", "").split(" s ")[-1], a.get("baseline_tuned", "").split(" s ")[-1]),
        "sentinel": ok, "command_rc": a.get("command_rc"),
    }


if __name__ == "__main__":
    b = sys.argv[1]
    for rid in sys.argv[2:]:
        r = row(b, rid)
        with open(os.path.join(HERE, "results", rid, "row.json"), "w") as f:
            json.dump(r, f, indent=1)
        print(json.dumps(r))
