#!/usr/bin/env python3
"""Write real.task.json (the whole archive) and probe.task.json (1 entry in 20, plus every entry
with an object over 50 MB, which is how the box was sized).

    python3 mkspec.py
"""
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
IMAGE = "quay.io/aarchsci/md@sha256:285377124854316a3604756eecde350dfb45b26b24b9f0e4f247d3b0a0dff58f"
IN = "s3://${COOKBOOK_BUCKET}/inputs/gemmi-real"
FILES = ["manifest.tsv.gz", "real.py", "real.sh", "pins.sha256"]
OUTS = ["entries.tsv.gz", "summary.tsv", "exceptions.tsv", "spacegroups.tsv", "timing.tsv", "real.log", "host.txt"]


def spec(task, prefix, sample, cpu, mem, family, ttl, cost):
    cmd = (
        "set +e\n"
        "# spawn runs task commands under bash -e; `set -uo pipefail` does not clear it (spawn#707).\n"
        "set -uo pipefail\n"
        "cd /tmp\n"
        "sha256sum -c pins.sha256 || { echo \"FAIL: a staged input does not match its pin\"; exit 1; }\n"
        "export SAMPLE=%d MANIFEST=/tmp/manifest.tsv.gz OUT=/tmp/out\n"
        "bash /tmp/real.sh\n" % sample
    )
    return {
        "task_id": task,
        "container": IMAGE,
        "command": ["bash", "-c", cmd],
        "resources": {"cpu": cpu, "memory_gib": mem, "architecture": "arm64", "families": [family]},
        "inputs": [{"source": "%s/%s" % (IN, f), "destination": "/tmp/%s" % f} for f in FILES],
        "outputs": [{"source": "/tmp/out/%s" % f, "destination": "s3://${COOKBOOK_BUCKET}/%s/%s" % (prefix, f)}
                    for f in OUTS],
        "lifecycle": {"ttl": ttl, "on_complete": "terminate", "cost_limit": cost},
    }


for name, s in [("real.task.json", spec("cookbook-gemmi-real", "runs/gemmi-real/r1", 1, 32, 64, "c8g", "4h", 3.0)),
                ("probe.task.json", spec("cookbook-gemmi-real-probe", "runs/gemmi-real/probe", 20, 32, 64, "c8g", "2h", 1.5))]:
    with open(os.path.join(HERE, name), "w") as f:
        json.dump(s, f, indent=2)
        f.write("\n")
    print(name)
