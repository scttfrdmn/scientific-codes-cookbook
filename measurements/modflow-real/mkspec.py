#!/usr/bin/env python3
"""Write real.task.json (the measured run) and probe.task.json (the same, truncated to 3 periods).

    python3 mkspec.py
"""
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
IMAGE = "quay.io/aarchsci/geoscience@sha256:f0f72f5b0fe3119ecd1578b5d2e475c438bf716e01c0033adfca89a066ebcb70"
IN = "s3://${COOKBOOK_BUCKET}/inputs/modflow-real"


def spec(task, prefix, probe_nper, ttl, cost):
    cmd = (
        "set +e\n"
        "# spawn runs task commands under bash -e; `set -uo pipefail` does not clear it (spawn#707).\n"
        "set -uo pipefail\n"
        "cd /tmp\n"
        "sha256sum -c pins.sha256 || { echo \"FAIL: a staged input does not match its pin\"; exit 1; }\n"
        "export MF_PROBE_NPER=%s\n"
        "bash /tmp/real.sh\n" % probe_nper
    )
    outs = ["results.tsv", "real.log", "host.txt"]
    for c in ["postcal", "precal"]:
        outs += ["mf6-%s.stdout" % c, "heads-%s.sha256" % c, "vs-archive-%s.tsv" % c, "budget-%s.tsv" % c, "budget-terms-%s.tsv" % c,
                 "%s-mobile_bay.hds" % c, "%s-mobile_bay.gwf.head.obs.csv" % c, "%s-mobile_bay.gwf.sfr.obs.csv" % c,
                 "archive-%s-mobile_bay.gwf.head.obs.csv" % c, "archive-%s-mobile_bay.gwf.sfr.obs.csv" % c,
                 "%s-mfsim.lst.gz" % c, "%s-mobile_bay.lst.gz" % c]
    return {
        "task_id": task,
        "container": IMAGE,
        "command": ["bash", "-c", cmd],
        "resources": {"cpu": 4, "memory_gib": 16, "architecture": "arm64", "families": ["m8g"], "disk_gib": 40},
        "inputs": [{"source": "%s/archive/%s" % (IN, f), "destination": "/tmp/archive/%s" % f}
                   for f in ["model.zip", "output.zip"]]
        + [{"source": "%s/%s" % (IN, f), "destination": "/tmp/%s" % f} for f in ["real.sh", "real.py", "pins.sha256"]],
        "outputs": [{"source": "/tmp/out/%s" % f, "destination": "s3://${COOKBOOK_BUCKET}/%s/%s" % (prefix, f)} for f in outs],
        "lifecycle": {"ttl": ttl, "on_complete": "terminate", "cost_limit": cost},
    }


for name, s in [("real.task.json", spec("cookbook-modflow-real", "runs/modflow-real/r1", "0", "10h", 1.5)),
                ("probe.task.json", spec("cookbook-modflow-real-probe", "runs/modflow-real/probe", "3", "3h", 0.5))]:
    with open(os.path.join(HERE, name), "w") as f:
        json.dump(s, f, indent=2)
        f.write("\n")
    print(name)
