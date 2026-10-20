#!/usr/bin/env python3
"""Write this directory's arm64 specs from run.sh, pinning the staged scripts by sha256.

    python3 measurements/sunpy-real/mkspec.py
"""
import hashlib
import json
import os

D = os.path.dirname(os.path.abspath(__file__))
IMAGE = "quay.io/aarchsci/astro@sha256:c09e88de976ec1184dbc4461bbb19477e9d39a991ef8aceeddb012da8d356a1c"
B = "s3://${COOKBOOK_BUCKET}"
P = B + "/inputs/sunpy-real"
SIZING = ["sdo_aia_h2_20170906T%s_0171_v1.fits" % t for t in ("000000", "060000", "110000", "120000", "130000", "180000")]


def sha(name):
    return hashlib.sha256(open(os.path.join(D, name), "rb").read()).hexdigest()


def spec(task, files, resources, ttl, cap, run):
    pins = "".join("%s  %s\\n" % (sha(f), f) for f in ("run.sh", "real.py", "pins.sha256"))
    cmd = ("set +e\nset -uo pipefail\n"
           "# spawn runs task commands under bash -e; `set -uo pipefail` does not clear it (spawn#707).\n"
           "cd /tmp\nprintf '%s' > scripts.sha256\n"
           "sha256sum -c scripts.sha256 || { echo 'FAIL: a staged script does not match its pin'; exit 1; }\n"
           "bash /tmp/run.sh %s\n") % (pins, " ".join(files))
    if files:
        ins = [{"source": "%s/aia/%s" % (P, f), "destination": "/work/in/%s" % f} for f in files]
    else:
        ins = [{"source": P + "/aia/", "destination": "/work/in/"}]
    ins += [{"source": "%s/%s" % (P, f), "destination": "/tmp/" + f} for f in ("run.sh", "real.py", "pins.sha256")]
    outs = ["check.txt", "real.log", "host.txt", "footprint.txt", "results.json", "staged.sha256"]
    return {
        "task_id": task,
        "container": IMAGE,
        "command": ["bash", "-c", cmd],
        "resources": resources,
        "inputs": ins,
        "outputs": [{"source": "/tmp/out/" + f, "destination": "%s/runs/sunpy-real/%s/%s" % (B, run, f)} for f in outs],
        "lifecycle": {"ttl": ttl, "on_complete": "terminate", "cost_limit": cap},
    }


def write(name, s):
    with open(os.path.join(D, name), "w") as fh:
        json.dump(s, fh, indent=2)
        fh.write("\n")


write("sizing.task.json", spec("cookbook-sunpy-real-sizing", SIZING,
                               {"cpu": 4, "memory_gib": 16, "architecture": "arm64", "families": ["m8g"], "disk_gib": 40},
                               "60m", 0.25, "sizing"))
# 14.5 s and ~1.9 GiB per image per worker (sizing run): 343 images on 16 workers is ~6 min.
write("real.task.json", spec("cookbook-sunpy-real-r1", [],
                             {"cpu": 16, "memory_gib": 64, "architecture": "arm64", "families": ["m8g"], "disk_gib": 40},
                             "60m", 0.6, "r1"))
print("wrote specs in", D)
