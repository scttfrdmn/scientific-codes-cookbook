#!/usr/bin/env python3
"""Write probe.task.json and real.task.json (arm64; scripts/spec_arch.py makes the x86 and AMD ones).

    python3 mkspec.py
"""
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
IMAGE = "quay.io/aarchsci/pointcloud@sha256:7817e0291f05f6bc209aa5999363503897647daf4eb9d6df153b1965629d6ffc"
IN = "s3://${COOKBOOK_BUCKET}/inputs/laspy-real"
PRE = (
    "set +e\n"
    "# spawn runs task commands under bash -e; `set -uo pipefail` does not clear it (spawn#707).\n"
    "set -uo pipefail\n"
    "export PATH=${CONDA_PREFIX:-/opt/conda}/bin:$PATH\n"
    "cd /tmp\n"
)


def spec(task, prefix, probe_tiles, pins, ttl, cost):
    skip = "" if pins else " | grep -vE '  pins\\.tsv$'"
    cmd = PRE + (
        "cat pins.sha256%s | sha256sum -c || { echo \"FAIL: a staged input does not match its pin\"; exit 1; }\n"
        "export LR_PROBE_TILES=%d\n"
        "bash /tmp/real.sh 16\n" % (skip, probe_tiles))
    files = ["real.sh", "real.py", "src.tsv", "manifest.json", "CA_LassenNP_2020.vpc", "pins.sha256"] + (["pins.tsv"] if pins else [])
    return {
        "task_id": task,
        "container": IMAGE,
        "command": ["bash", "-c", cmd],
        "resources": {"cpu": 16, "memory_gib": 64, "architecture": "arm64", "families": ["m8g"], "disk_gib": 100},
        "inputs": [{"source": "%s/laz/" % IN, "destination": "/tmp/laz/"}]
        + [{"source": "%s/%s" % (IN, f), "destination": "/tmp/%s" % f} for f in files],
        "outputs": [{"source": "/tmp/out/", "destination": "s3://${COOKBOOK_BUCKET}/%s/" % prefix}],
        "lifecycle": {"ttl": ttl, "on_complete": "terminate", "cost_limit": cost},
    }


for name, s in (("probe.task.json", spec("cookbook-laspy-real-probe", "runs/laspy-real/probe", 16, True, "1h", 0.8)),
                ("real.task.json", spec("cookbook-laspy-real", "runs/laspy-real/r1", 0, True, "2h", 2.0))):
    with open(os.path.join(HERE, name), "w") as f:
        json.dump(s, f, indent=2)
        f.write("\n")
    print(name)
