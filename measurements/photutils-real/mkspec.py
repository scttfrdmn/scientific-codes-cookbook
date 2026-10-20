#!/usr/bin/env python3
"""Write this directory's arm64 specs from run.sh, pinning the staged scripts by sha256.

    python3 measurements/photutils-real/mkspec.py
"""
import hashlib
import json
import os

D = os.path.dirname(os.path.abspath(__file__))
IMAGE = "quay.io/aarchsci/astro@sha256:430fb49e6352fe1e24fe52fcf9c1a3587d41e86a34c3b733dd2098ee126e9c82"
B = "s3://${COOKBOOK_BUCKET}"
P = B + "/inputs/photutils-real"


def sha(name):
    return hashlib.sha256(open(os.path.join(D, name), "rb").read()).hexdigest()


def spec(task, cells, resources, ttl, cap, run):
    pins = "".join("%s  %s\\n" % (sha(f), f) for f in ("run.sh", "real.py", "pins.sha256"))
    cmd = ("set +e\nset -uo pipefail\n"
           "# spawn runs task commands under bash -e; `set -uo pipefail` does not clear it (spawn#707).\n"
           "cd /tmp\nprintf '%s' > scripts.sha256\n"
           "sha256sum -c scripts.sha256 || { echo 'FAIL: a staged script does not match its pin'; exit 1; }\n"
           "bash /tmp/run.sh %s\n") % (pins, cells)
    if cells:
        ins = []
        for c in cells.split(","):
            stem = "rings.v3.skycell.1749.%s.stk.r" % c
            ins += [{"source": "%s/ps1/%s.unconv%s.fits" % (P, stem, s), "destination": "/work/in/ps1/%s.unconv%s.fits" % (stem, s)}
                    for s in ("", ".wt", ".mask")]
            ins.append({"source": B + "/inputs/astropy-real/cmf/%s.cmf" % stem, "destination": "/work/in/cmf/%s.cmf" % stem})
    else:
        ins = [{"source": P + "/ps1/", "destination": "/work/in/ps1/"},
               {"source": B + "/inputs/astropy-real/cmf/", "destination": "/work/in/cmf/"}]
    ins += [{"source": "%s/%s" % (P, f), "destination": "/tmp/" + f} for f in ("run.sh", "real.py", "pins.sha256")]
    outs = ["check.txt", "real.log", "host.txt", "footprint.txt", "results.json", "cells.tar", "staged.sha256"]
    return {
        "task_id": task,
        "container": IMAGE,
        "command": ["bash", "-c", cmd],
        "resources": resources,
        "inputs": ins,
        "outputs": [{"source": "/tmp/out/" + f, "destination": "%s/runs/photutils-real/%s/%s" % (B, run, f)} for f in outs],
        "lifecycle": {"ttl": ttl, "on_complete": "terminate", "cost_limit": cap},
    }


def write(name, s):
    with open(os.path.join(D, name), "w") as fh:
        json.dump(s, fh, indent=2)
        fh.write("\n")


write("sizing.task.json", spec("cookbook-photutils-real-sizing", "000,001",
                               {"cpu": 2, "memory_gib": 16, "architecture": "arm64", "families": ["m8g"], "disk_gib": 40},
                               "40m", 0.15, "sizing"))
write("real.task.json", spec("cookbook-photutils-real-r1", "",
                             {"cpu": 16, "memory_gib": 128, "architecture": "arm64", "families": ["r8g"], "disk_gib": 60},
                             "90m", 1.5, "r1"))
ins = [{"source": "%s/%s/photutils-real/r1/%s" % (B, pre, f), "destination": "/tmp/cmp/%s/%s" % (chip, f)}
       for chip, pre in (("arm64", "runs"), ("x86_64", "runs-x86_64"), ("amd", "runs-amd"))
       for f in ("cells.tar", "results.json", "check.txt")]
ins.append({"source": P + "/compare.py", "destination": "/tmp/compare.py"})
write("compare.task.json", {
    "task_id": "cookbook-photutils-real-compare",
    "container": IMAGE,
    "command": ["bash", "-c", "set +e\nset -uo pipefail\nexport PATH=/opt/conda/bin:$PATH HOME=/tmp\nmkdir -p /tmp/out\n"
                "echo '%s  /tmp/compare.py' | sha256sum -c - || { echo 'FAIL: compare.py does not match its pin'; exit 1; }\n"
                "python3 -u /tmp/compare.py /tmp/cmp > /tmp/out/compare.log 2>&1; RC=$?\ncat /tmp/out/compare.log\n"
                "[ $RC = 0 ] && grep -q 'COMPARE DONE' /tmp/out/compare.log && test -s /tmp/out/crosschip.txt || { echo FAIL; exit 1; }\n" % sha("compare.py")],
    "resources": {"cpu": 2, "memory_gib": 8, "architecture": "arm64", "families": ["m8g"]},
    "inputs": ins,
    "outputs": [{"source": "/tmp/out/" + f, "destination": "%s/runs/photutils-real/compare/%s" % (B, f)} for f in ("crosschip.txt", "compare.log")],
    "lifecycle": {"ttl": "30m", "on_complete": "terminate", "cost_limit": 0.1},
})
print("wrote specs in", D)
