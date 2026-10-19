"""Write cp2k-real's arm64 specs (probe and full). x86/AMD come from scripts/spec_arch.py.

    python3 mkspec.py
"""
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
IMAGE = "quay.io/aarchsci/cp2k@sha256:a2b70e16570c46a30ee44f6567668cd10610db77c7f48017721845c9a81d8c6f"


def spec(tid, jobset, ttl, cap, prefix):
    cmd = ("set +e\n"
           "# spawn runs task commands under bash -e; `set -uo pipefail` does not clear it (spawn#707).\n"
           "cd /tmp\n"
           "sha256sum -c pins.sha256 > pins.check 2>&1 || { cat pins.check; echo \"FAIL: a staged input does not match its pin\"; exit 1; }\n"
           "echo \"pins: $(grep -c . pins.sha256) files verified\"\n"
           "bash real.sh /tmp/data %s\n" % jobset)
    b = "s3://${COOKBOOK_BUCKET}/inputs/cp2k-real/"
    return {
        "task_id": tid,
        "container": IMAGE,
        "command": ["bash", "-c", cmd],
        "resources": {"cpu": 16, "memory_gib": 64, "architecture": "arm64", "families": ["m8g"]},
        "inputs": [{"source": b + "data/", "destination": "/tmp/data/"}] +
                  [{"source": b + f, "destination": "/tmp/" + f} for f in ("real.py", "real.sh", "pins.sha256")],
        "outputs": [{"source": "/tmp/out/", "destination": "s3://${COOKBOOK_BUCKET}/runs/cp2k-real/%s/" % prefix}],
        "lifecycle": {"ttl": ttl, "on_complete": "terminate", "cost_limit": cap},
    }


for name, args in {"probe": ("cookbook-cp2k-real-probe", "probe", "90m", 1.0, "probe"),
                   "real": ("cookbook-cp2k-real", "full", "8h", 4.0, "full")}.items():
    with open(os.path.join(HERE, name + ".task.json"), "w") as f:
        json.dump(spec(*args), f, indent=2)
        f.write("\n")
