#!/usr/bin/env python3
"""Write this directory's arm64 specs from fetch.sh / run.sh, pinning the staged scripts by sha256.

    python3 measurements/datashader-real/mkspec.py

The x86 specs come from these by scripts/spec_arch.py (datashader x86_64|amd ...).
"""
import hashlib
import json
import os

D = os.path.dirname(os.path.abspath(__file__))
IMAGE = "quay.io/aarchsci/geo-ml@sha256:7414b1df7c155b7d820cc062d88b61e5081b3d2ba4685c57a29b591cd67748de"
B = "s3://${COOKBOOK_BUCKET}"
IN = B + "/inputs/datashader-real"


def sha(name):
    return hashlib.sha256(open(os.path.join(D, name), "rb").read()).hexdigest()


def write(name, spec):
    with open(os.path.join(D, name), "w") as fh:
        json.dump(spec, fh, indent=2)
        fh.write("\n")


write("00-fetch.task.json", {
    "task_id": "cookbook-datashader-real-fetch",
    "container": IMAGE,
    "command": ["bash", "-c", open(os.path.join(D, "fetch.sh")).read()],
    # 11.4 GB lands in /tmp, the tmpfs of half the RAM: 64 GiB gives it 32
    "resources": {"cpu": 8, "memory_gib": 60, "architecture": "arm64", "families": ["r8g"]},
    "outputs": [{"source": "/tmp/out/", "destination": IN + "/tlc/"}],
    "lifecycle": {"ttl": "40m", "on_complete": "terminate", "cost_limit": 0.15},
})


def real(task_id, run, data_inputs, expect, ttl, cost):
    pins = "".join("%s  %s\\n" % (sha(f), f) for f in ("run.sh", "real.py", "pins.sha256"))
    cmd = ("set +e\nset -uo pipefail\n"
           "# spawn runs task commands under bash -e; `set -uo pipefail` does not clear it (spawn#707).\n"
           "cd /tmp\nprintf '%s' > scripts.sha256\n"
           "sha256sum -c scripts.sha256 || { echo 'FAIL: a staged script does not match its pin'; exit 1; }\n"
           "EXPECT_FILES=%d NT=16 bash /tmp/run.sh\n") % (pins, expect)
    outs = ["check.txt", "real.log", "host.txt", "footprint.txt", "results.json", "rasters.npz",
            "staged.sha256"]
    return {
        "task_id": task_id,
        "container": IMAGE,
        "command": ["bash", "-c", cmd],
        # measured on the whole dataset: the persisted frame peaks at 40.4 GiB beside 10.6 GiB of
        # staged Parquet in the tmpfs -- too close to a 2xlarge's 64 GiB, so a 4xlarge (128 GiB)
        "resources": {"cpu": 16, "memory_gib": 120, "architecture": "arm64", "families": ["r8g"]},
        "inputs": data_inputs + [
            {"source": IN + "/" + f, "destination": "/tmp/" + f} for f in ("run.sh", "real.py", "pins.sha256")],
        "outputs": [{"source": "/tmp/out/" + f,
                     "destination": B + "/runs/datashader-real/%s/%s" % (run, f)} for f in outs],
        "lifecycle": {"ttl": ttl, "on_complete": "terminate", "cost_limit": cost},
    }


if os.path.exists(os.path.join(D, "pins.sha256")):
    one = ["yellow_tripdata_2009-01.parquet", "yellow_tripdata_2010-01.parquet"]
    write("probe.task.json", real(
        "cookbook-datashader-real-probe", "probe",
        [{"source": IN + "/tlc/" + f, "destination": "/tmp/tlc/" + f} for f in one], 2, "30m", 0.15))
    write("real.task.json", real(
        "cookbook-datashader-real-r1", "r1",
        [{"source": IN + "/tlc/", "destination": "/tmp/tlc/"}], 24, "60m", 0.5))
