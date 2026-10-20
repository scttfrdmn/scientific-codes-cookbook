#!/usr/bin/env python3
"""Write this directory's arm64 specs from fetch.sh / run.sh, pinning the staged scripts by sha256.

    python3 measurements/rebound-real/mkspec.py            # real.task.json, 100 Myr
    python3 measurements/rebound-real/mkspec.py r0 1       # probe.task.json, 1 Myr (the sizing run)
"""
import hashlib
import json
import os

D = os.path.dirname(os.path.abspath(__file__))
IMAGE = "quay.io/aarchsci/astro@sha256:9568095c64864dbcf4ad2759ad1aa5457bfa4e12530282378f706c3d394dc753"
B = "s3://${COOKBOOK_BUCKET}"
import sys
RUN = sys.argv[1] if len(sys.argv) > 1 else "r1"
SPAN = sys.argv[2] if len(sys.argv) > 2 else "100"   # Myr
SCRIPTS = ("run.sh", "ic.py", "real.py", "long.py", "longcheck.py", "pins.sha256")


def sha(name):
    return hashlib.sha256(open(os.path.join(D, name), "rb").read()).hexdigest()


def write(name, spec):
    with open(os.path.join(D, name), "w") as fh:
        json.dump(spec, fh, indent=2)
        fh.write("\n")


write("00-fetch.task.json", {
    "task_id": "cookbook-rebound-real-fetch",
    "container": IMAGE,
    "command": ["bash", "-c", open(os.path.join(D, "fetch.sh")).read()],
    "resources": {"cpu": 2, "memory_gib": 4, "architecture": "arm64", "families": ["c8g"]},
    "outputs": [{"source": "/tmp/out/", "destination": B + "/inputs/rebound-real/de440/"}],
    "lifecycle": {"ttl": "30m", "on_complete": "terminate", "cost_limit": 0.05},
})

if all(os.path.exists(os.path.join(D, f)) for f in SCRIPTS):
    pins = "".join("%s  %s\\n" % (sha(f), f) for f in SCRIPTS)
    cmd = ("set +e\nset -uo pipefail\n"
           "# spawn runs task commands under bash -e; `set -uo pipefail` does not clear it (spawn#707).\n"
           "cd /tmp\nprintf '%s' > scripts.sha256\n"
           "sha256sum -c scripts.sha256 || { echo 'FAIL: a staged script does not match its pin'; exit 1; }\n"
           "bash /tmp/run.sh %s\n") % (pins, SPAN)
    outs = ["check.txt", "real.log", "long-nominal.log", "long-shadow.log", "host.txt", "footprint.txt",
            "results.json", "long.json", "ephem.npz", "long-nominal.npz", "long-shadow.npz", "staged.sha256"]
    write("real.task.json" if RUN == "r1" else "probe.task.json", {
        "task_id": "cookbook-rebound-real-" + RUN,
        "container": IMAGE,
        "command": ["bash", "-c", cmd],
        "resources": {"cpu": 4, "memory_gib": 8, "architecture": "arm64", "families": ["c8g"]},
        "inputs": [{"source": B + "/inputs/rebound-real/de440/", "destination": "/tmp/de440/"}] +
                  [{"source": B + "/inputs/rebound-real/" + f, "destination": "/tmp/" + f} for f in SCRIPTS],
        "outputs": [{"source": "/tmp/out/" + f, "destination": B + "/runs/rebound-real/%s/%s" % (RUN, f)}
                    for f in outs],
        "lifecycle": {"ttl": "10h" if RUN == "r1" else "1h", "on_complete": "terminate", "cost_limit": 1.5 if RUN == "r1" else 0.2},
    })
print("wrote specs in", D)
