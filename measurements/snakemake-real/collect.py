#!/usr/bin/env python3
"""Summarise measurements/snakemake-real/results/<chip>/ into one line per chip and one per job.

    AWS_PROFILE=... python3 collect.py <phase> [chip ...]

Per job, the rule's own log (logs/stats/<file>.log, copied by run.sh) gives the instance and the
phase timestamps. The billed window of each job instance is EC2's: CloudTrail's RunInstances and
the instance's own TerminateInstances, looked up by instance id and cached in
results/<chip>/<phase>.instances.tsv so the evidence is committed. $/hr is spawn's own price for
the instance, the spawn:price-per-hour tag it set at launch (in the same RunInstances event).
Snakemake's log gives when it dispatched each job and when it saw it finish, so the submit-side
latency (spawn sizing and pricing the launch, before EC2 is called) is separated from the billed
window."""
import json
import os
import subprocess
import sys
import time
from datetime import datetime

D = os.path.dirname(os.path.abspath(__file__))


def kv(path):
    d = {}
    for line in open(path):
        f = line.rstrip("\n").split("\t", 1)
        if len(f) == 2:
            d.setdefault(f[0], f[1])
    return d


def trail(iid):
    out = subprocess.run(["aws", "cloudtrail", "lookup-events", "--region", "us-west-2", "--lookup-attributes",
                          "AttributeKey=ResourceName,AttributeValue=" + iid, "--query",
                          "Events[].[EventName,EventTime,CloudTrailEvent]", "--output", "json"],
                         capture_output=True, text=True, check=True).stdout
    ev, price = {}, None
    for name, t, raw in json.loads(out):
        ev.setdefault(name, datetime.fromisoformat(t).timestamp())
        if name == "RunInstances":
            for ts in json.loads(raw)["requestParameters"].get("tagSpecificationSet", {}).get("items", []):
                for tag in ts.get("tags", []):
                    if tag["key"] == "spawn:price-per-hour":
                        price = float(tag["value"])
    return ev.get("RunInstances"), ev.get("TerminateInstances"), price


def instances(chip, phase, ids):
    path = os.path.join(D, "results", chip, "%s.instances.tsv" % phase)
    have = {}
    if os.path.exists(path):
        for line in open(path):
            f = line.rstrip("\n").split("\t")
            have[f[0]] = (float(f[1]), float(f[2]), float(f[3]))
    for iid in ids:
        if iid not in have:
            run, term, price = trail(iid)
            if run is None or term is None or price is None:
                sys.exit("%s: CloudTrail has no RunInstances/TerminateInstances yet (it lags ~15 min)" % iid)
            have[iid] = (run, term, price)
    with open(path, "w") as fh:
        for iid in sorted(have):
            fh.write("%s\t%.0f\t%.0f\t%.6f\n" % (iid, *have[iid]))
    return have


def snakemake_jobs(path):
    """(rule, file) -> [dispatch, finish] epoch, from Snakemake's own log of the phase."""
    jobs, byid, ts, cur = {}, {}, None, None
    for line in open(path):
        line = line.rstrip("\n")
        if line.startswith("[") and line.endswith("]"):
            ts = time.mktime(time.strptime(line[1:-1], "%a %b %d %H:%M:%S %Y"))
        elif line.startswith("rule ") and line.endswith(":"):
            cur = {"rule": line[5:-1], "f": "-", "start": ts}
        elif cur is not None and line.strip().startswith("jobid:"):
            byid[line.split(":")[1].strip()] = cur
        elif cur is not None and line.strip().startswith("wildcards: f="):
            cur["f"] = line.strip()[len("wildcards: f="):]
        elif line.startswith("Finished jobid:"):
            j = byid[line.split()[2]]
            jobs[(j["rule"], j["f"])] = [j["start"], ts]
    return jobs


def main(phase, chips):
    for chip in chips:
        R = os.path.join(D, "results", chip)
        out = os.path.join(R, "outputs-" + phase)
        wall = [int(x) for x in open(os.path.join(R, phase + ".wall")).read().split()]
        ran = snakemake_jobs(os.path.join(R, phase + ".log"))
        jobs = []
        for rule, f in sorted(ran):
            p = os.path.join(out, "logs", "stats", f + ".log") if rule == "stats" else os.path.join(out, "logs", "cohort.log")
            jobs.append((rule, kv(p), ran[(rule, f)]))
        win = instances(chip, phase, [j["instance_id"] for _, j, _ in jobs])
        tot = dict(billed_s=0.0, billed_usd=0.0, seqkit_s=0.0, seqkit_usd=0.0, submit_s=0.0)
        hwm = 0
        for rule, j, (disp, fin) in jobs:
            run, term, rate = win[j["instance_id"]]
            b = term - run
            tot["billed_s"] += b
            tot["billed_usd"] += b * rate / 3600
            tot["submit_s"] += run - disp
            line = [chip, rule, j.get("file", "-"), j["instance_type"], "$/hr=%.4f" % rate, "billed=%.0fs" % b,
                    "submit=%.0fs" % (run - disp), "seen_done=%.0fs" % (fin - term)]
            if rule == "stats":
                s = float(j["seqkit_end"]) - float(j["seqkit_start"])
                tot["seqkit_s"] += s
                tot["seqkit_usd"] += s * rate / 3600
                hwm = max(hwm, int(j["seqkit_vmhwm_kib"]))
                st = float(j["script_start"])
                line += ["boot>venv=%.0f" % (float(j["venv_birth_epoch"]) - float(j["boot_epoch"])),
                         "venv>staged=%.0f" % (float(j["input_ctime_epoch"]) - float(j["venv_birth_epoch"])),
                         "staged>script=%.0f" % (st - float(j["input_ctime_epoch"])),
                         "docker=%.0f" % (float(j["docker_ready"]) - st),
                         "pull=%.0f" % (float(j["image_pulled"]) - float(j["docker_ready"])),
                         "seqkit=%.1f" % s, "sha=%.0f" % (float(j["sha256_done"]) - float(j["seqkit_end"])),
                         "after=%.0f" % (term - float(j["script_end"])),
                         "launch>boot=%.0f" % (float(j["boot_epoch"]) - run),
                         "vmhwm=%.0fMiB" % (int(j["seqkit_vmhwm_kib"]) / 1024),
                         "disk=%.1f/%.1fGiB" % (int(j["disk_used_kib"]) / 2**20, int(j["disk_size_kib"]) / 2**20),
                         "tpc=" + j["threads_per_core"], j["cpu_model"], "fails=" + j["fails"]]
            print("\t".join(line))
        print("\t".join([chip, "TOTAL", phase, "jobs=%d" % len(jobs), "workflow_wall=%ds" % (wall[1] - wall[0]),
                         "exit=%d" % wall[2], "sum_seqkit=%.0fs" % tot["seqkit_s"],
                         "sum_billed=%.0fs" % tot["billed_s"], "sum_submit=%.0fs" % tot["submit_s"], "compute$=%.4f" % tot["seqkit_usd"],
                         "billed$=%.4f" % tot["billed_usd"], "peak_vmhwm=%.0fMiB" % (hwm / 1024)]))


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    main(sys.argv[1], sys.argv[2:] or ["arm64", "x86_64", "amd"])
