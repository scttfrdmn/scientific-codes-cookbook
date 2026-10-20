#!/usr/bin/env python3
"""Write real.task.json (the measured run) and probe.task.json (the same on five entries).

    python3 mkspec.py
"""
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
IMAGE = "quay.io/aarchsci/structbio@sha256:9fd80ad08baf5b1f79c0e433d0fb30eec1d2f810b34594ad556d8f4fb8bb5479"
IN = "s3://${COOKBOOK_BUCKET}/inputs/cctbx-real"
PROBE = "1ab0 1ab1 5abh 5abj 8ab3"   # the two smallest-numbered and the three largest sf files


def spec(task, prefix, probe_ids, cpu, mem, family, ttl, cost):
    cmd = (
        "set +e\n"
        "# spawn runs task commands under bash -e; `set -uo pipefail` does not clear it (spawn#707).\n"
        "set -uo pipefail\n"
        "cd /tmp\n"
        "sha256sum -c --status cohort.sha256 || { echo \"FAIL: a staged entry does not match cohort.sha256\"; exit 1; }\n"
        "sha256sum -c scripts.sha256 || { echo \"FAIL: a script does not match its pin\"; exit 1; }\n"
        "export CCTBX_PROBE_IDS=\"%s\"\n"
        "bash /tmp/real.sh\n" % probe_ids
    )
    outs = ["results.tsv", "real.log", "host.txt", "logs.tar.gz"]
    return {
        "task_id": task,
        "container": IMAGE,
        "command": ["bash", "-c", cmd],
        "resources": {"cpu": cpu, "memory_gib": mem, "architecture": "arm64", "families": [family],
                      "disk_gib": 40},
        "inputs": [{"source": "%s/pdb-20260101/ab/" % IN, "destination": "/tmp/pdb/"}]
        + [{"source": "%s/%s" % (IN, f), "destination": "/tmp/%s" % f}
           for f in ["real.sh", "real.py", "cohort.sha256", "scripts.sha256"]],
        "outputs": [{"source": "/tmp/out/%s" % f, "destination": "s3://${COOKBOOK_BUCKET}/%s/%s" % (prefix, f)}
                    for f in outs],
        "lifecycle": {"ttl": ttl, "on_complete": "terminate", "cost_limit": cost},
    }


for name, s in [("real.task.json", spec("cookbook-cctbx-real", "runs/cctbx-real/r1", "", 16, 32, "c8g", "2h", 1.0)),
                ("probe.task.json", spec("cookbook-cctbx-real-probe", "runs/cctbx-real/probe", PROBE, 8, 32, "m8g", "2h", 0.6))]:
    with open(os.path.join(HERE, name), "w") as f:
        json.dump(s, f, indent=2)
        f.write("\n")
    print(name)
