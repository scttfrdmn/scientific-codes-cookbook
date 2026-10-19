#!/usr/bin/env python3
"""Write this directory's arm64 specs from fetch.sh / run.sh, pinning the staged scripts by sha256.

    python3 measurements/healpy-real/mkspec.py
"""
import hashlib
import json
import os

D = os.path.dirname(os.path.abspath(__file__))
IMAGE = "quay.io/aarchsci/astro@sha256:430fb49e6352fe1e24fe52fcf9c1a3587d41e86a34c3b733dd2098ee126e9c82"
B = "s3://${COOKBOOK_BUCKET}"


def sha(name):
    return hashlib.sha256(open(os.path.join(D, name), "rb").read()).hexdigest()


def write(name, spec):
    with open(os.path.join(D, name), "w") as fh:
        json.dump(spec, fh, indent=2)
        fh.write("\n")


write("00-fetch.task.json", {
    "task_id": "cookbook-healpy-real-fetch",
    "container": IMAGE,
    "command": ["bash", "-c", open(os.path.join(D, "fetch.sh")).read()],
    "resources": {"cpu": 2, "memory_gib": 8, "architecture": "arm64", "families": ["m8g"]},
    "outputs": [{"source": "/tmp/out/", "destination": B + "/inputs/healpy-real/pr3/"}],
    "lifecycle": {"ttl": "40m", "on_complete": "terminate", "cost_limit": 0.1},
})

if os.path.exists(os.path.join(D, "run.sh")):
    pins = "".join("%s  %s\\n" % (sha(f), f) for f in ("run.sh", "real.py", "pins.sha256"))
    cmd = ("set +e\nset -uo pipefail\n"
           "# spawn runs task commands under bash -e; `set -uo pipefail` does not clear it (spawn#707).\n"
           "cd /tmp\nprintf '%s' > scripts.sha256\n"
           "sha256sum -c scripts.sha256 || { echo 'FAIL: a staged script does not match its pin'; exit 1; }\n"
           "bash /tmp/run.sh\n") % pins
    outs = ["check.txt", "real.log", "host.txt", "footprint.txt", "results.json", "spectra.npz", "staged.sha256"]
    write("real.task.json", {
        "task_id": "cookbook-healpy-real-r2",
        "container": IMAGE,
        "command": ["bash", "-c", cmd],
        "resources": {"cpu": 8, "memory_gib": 32, "architecture": "arm64", "families": ["m8g"]},
        "inputs": [
            {"source": B + "/inputs/healpy-real/pr3/", "destination": "/tmp/pr3/"},
            {"source": B + "/inputs/healpy-real/run.sh", "destination": "/tmp/run.sh"},
            {"source": B + "/inputs/healpy-real/real.py", "destination": "/tmp/real.py"},
            {"source": B + "/inputs/healpy-real/pins.sha256", "destination": "/tmp/pins.sha256"},
        ],
        "outputs": [{"source": "/tmp/out/" + f, "destination": B + "/runs/healpy-real/r2/" + f}
                    for f in outs],
        "lifecycle": {"ttl": "90m", "on_complete": "terminate", "cost_limit": 0.3},
    })
print("wrote specs in", D)
