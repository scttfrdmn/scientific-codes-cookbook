#!/usr/bin/env python3
"""Write this directory's arm64 specs, pinning the staged scripts by sha256.

    python3 measurements/r-arrow-real/mkspec.py

The x86 specs come from real.task.json by scripts/spec_arch.py (r-arrow x86_64|amd ...).
00-reference runs once, on arm64: its tables are integers, and the arrow runs on every chip
must reproduce them exactly.
"""
import hashlib
import json
import os

D = os.path.dirname(os.path.abspath(__file__))
IMAGE = "quay.io/aarchsci/r@sha256:136bf063a0d04967e2e2dac8622259edf344d5aa74cdd5869502007cf6c5a56c"
# polars 2.0.0: aarch.science's neuroimaging env, as pinned by recipes/nilearn and recipes/mne
POLARS = "quay.io/aarchsci/neuroimaging@sha256:f57c5cc3637cfcf20dfdf8a1627f3a57b56b397dde9f99805d35fa4b4a7a657c"
B = "s3://${COOKBOOK_BUCKET}"
IN = B + "/inputs/r-arrow-real"
DATA = [{"source": B + "/inputs/datashader-real/tlc/", "destination": "/tmp/tlc/"},   # 2009-2010
        {"source": B + "/inputs/r-real/tlc/", "destination": "/tmp/tlc/"}]           # 2023


def sha(name):
    return hashlib.sha256(open(os.path.join(D, name), "rb").read()).hexdigest()


def write(name, spec):
    with open(os.path.join(D, name), "w") as fh:
        json.dump(spec, fh, indent=2)
        fh.write("\n")


def cmd(scripts, body):
    pins = "".join("%s  %s\\n" % (sha(f), f) for f in scripts)
    return ["bash", "-c", "set +e\nset -uo pipefail\n"
            "# spawn runs task commands under bash -e; `set -uo pipefail` does not clear it (spawn#707).\n"
            "cd /tmp\nprintf '%s' > scripts.sha256\n"
            "sha256sum -c scripts.sha256 || { echo 'FAIL: a staged script does not match its pin'; exit 1; }\n"
            "%s\n" % (pins, body)]


def staged(names):
    return [{"source": IN + "/" + f, "destination": "/tmp/" + f} for f in names]


write("00-reference.task.json", {
    "task_id": "cookbook-r-arrow-real-reference",
    "container": POLARS,
    "command": cmd(["ref.sh", "reference.py", "pins.sha256"], "bash /tmp/ref.sh"),
    # 12 GB of Parquet in the tmpfs (half of 64 GiB) and one month's columns in polars at a time
    "resources": {"cpu": 16, "memory_gib": 60, "architecture": "arm64", "families": ["m8g"]},
    "inputs": DATA + staged(["ref.sh", "reference.py", "pins.sha256"]),
    "outputs": [{"source": "/tmp/out/", "destination": IN + "/reference/"}],
    "lifecycle": {"ttl": "60m", "on_complete": "terminate", "cost_limit": 0.5},
})


def real(task_id, run, data, expect, ttl, cost, with_ref, cpu=16, mem=60):
    scripts = ["run.sh", "analysis.R", "pins.sha256"] + (["ref.sha256"] if with_ref else [])
    ins = data + staged(scripts)
    if with_ref:
        ins.append({"source": IN + "/reference/ref/", "destination": "/tmp/ref/"})
    outs = ["check.txt", "analysis.log", "results.tsv", "host.txt", "footprint.txt", "staged.sha256",
            "files.csv", "pay.csv", "hour.csv", "dist.csv"]
    return {
        "task_id": task_id,
        "container": IMAGE,
        "command": cmd(scripts, "EXPECT_FILES=%d NT=%d bash /tmp/run.sh" % (expect, cpu)),
        # measured on the whole dataset (README): the tmpfs peaked at 14.0-14.6 GB (12.0 GB staged +
        # the 2.3 GB rewrite) of 32, the R process at 6.1 GiB -- a 32 GiB box's 16 GiB tmpfs is too close
        "resources": {"cpu": cpu, "memory_gib": mem, "architecture": "arm64", "families": ["m8g"]},
        "inputs": ins,
        "outputs": [{"source": "/tmp/out/" + f,
                     "destination": B + "/runs/r-arrow-real/%s/%s" % (run, f)} for f in outs],
        "lifecycle": {"ttl": ttl, "on_complete": "terminate", "cost_limit": cost},
    }


one = ["yellow_tripdata_2009-01.parquet", "yellow_tripdata_2010-01.parquet", "yellow_tripdata_2023-01.parquet",
       "yellow_tripdata_2023-02.parquet"]
src = {"2009": "datashader-real", "2010": "datashader-real", "2023": "r-real"}
write("probe.task.json", real(
    "cookbook-r-arrow-real-probe", "probe",
    [{"source": B + "/inputs/%s/tlc/%s" % (src[f[16:20]], f), "destination": "/tmp/tlc/" + f} for f in one],
    len(one), "30m", 0.15, False, cpu=4, mem=16))
write("real.task.json", real("cookbook-r-arrow-real-r1", "r1", DATA, 36, "60m", 0.6,
                             os.path.exists(os.path.join(D, "ref.sha256"))))
