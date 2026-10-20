#!/usr/bin/env python3
"""Write 00-pin.task.json, probe.task.json and real.task.json.

    python3 mkspec.py
"""
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
IMAGE = "quay.io/aarchsci/pointcloud@sha256:1e53023e25dc060315e5e693be3f942aa733c4e7ca6cab65fa915a34e2862823"
IN = "s3://${COOKBOOK_BUCKET}/inputs/pointcloud-real"
PROJECT = "CA_LassenNP_2020"
PRE = (
    "set +e\n"
    "# spawn runs task commands under bash -e; `set -uo pipefail` does not clear it (spawn#707).\n"
    "set -uo pipefail\n"
    "export PATH=${CONDA_PREFIX:-/opt/conda}/bin:$PATH\n"
    "cd /tmp\n"
)


def write(name, spec):
    with open(os.path.join(HERE, name), "w") as f:
        json.dump(spec, f, indent=2)
        f.write("\n")
    print(name)


write("00-pin.task.json", {
    "task_id": "cookbook-pointcloud-real-pin",
    "container": IMAGE,
    "command": ["bash", "-c", PRE + (
        "grep -E '  pin\\.py$' pins.sha256 | sha256sum -c || { echo \"FAIL: pin.py does not match its pin\"; exit 1; }\n"
        "mkdir -p /tmp/out\n"
        "python3 -u /tmp/pin.py %s /tmp/out 64 2>&1 | tee /tmp/out/pin.log\n"
        "grep -q '^PIN OK' /tmp/out/pin.log || { echo \"FAIL: pin\"; exit 1; }\n"
        "( cd /tmp/out && sha256sum pins.tsv > pins.tsv.sha256 && cat pins.tsv.sha256 )\n" % PROJECT)],
    "resources": {"cpu": 16, "memory_gib": 32, "architecture": "arm64", "families": ["c8g"]},
    "inputs": [{"source": "%s/%s" % (IN, f), "destination": "/tmp/%s" % f} for f in ["pin.py", "pins.sha256"]],
    "outputs": [{"source": "/tmp/out/%s" % f, "destination": "%s/%s" % (IN, f)} for f in ["pins.tsv", "pins.tsv.sha256", "pin.log"]],
    "lifecycle": {"ttl": "2h", "on_complete": "terminate", "cost_limit": 0.6},
})


def run_spec(task, prefix, probe_tiles, probe_nodes, ttl, cost):
    cmd = PRE + (
        "grep -vE '  pin\\.py$' pins.sha256 | sha256sum -c || { echo \"FAIL: a staged input does not match its pin\"; exit 1; }\n"
        "export PC_PROBE_TILES=%d PC_PROBE_NODES=%d\n"
        "bash /tmp/real.sh 16\n" % (probe_tiles, probe_nodes))
    return {
        "task_id": task,
        "container": IMAGE,
        "command": ["bash", "-c", cmd],
        "resources": {"cpu": 16, "memory_gib": 32, "architecture": "arm64", "families": ["c8g"], "disk_gib": 80},
        "inputs": [{"source": "%s/%s" % (IN, f), "destination": "/tmp/%s" % f} for f in ["real.sh", "real.py", "pins.tsv", "pins.sha256"]],
        "outputs": [{"source": "/tmp/out/", "destination": "s3://${COOKBOOK_BUCKET}/%s/" % prefix}],
        "lifecycle": {"ttl": ttl, "on_complete": "terminate", "cost_limit": cost},
    }


write("probe.task.json", run_spec("cookbook-pointcloud-real-probe", "runs/pointcloud-real/probe", 6, 3000, "1h", 0.4))
write("real.task.json", run_spec("cookbook-pointcloud-real", "runs/pointcloud-real/r1", 0, 0, "4h", 2.0))

# t03_06 of the real run's grid: 15,758,493 points
TILE = "-13524340.005,-13522340.005,4944953.995,4946953.995"
write("03-compare.task.json", {
    "task_id": "cookbook-pointcloud-real-compare",
    "container": IMAGE,
    "command": ["bash", "-c", PRE + (
        "sha256sum -c compare.sha256 || { echo \"FAIL: compare.py does not match its pin\"; exit 1; }\n"
        "mkdir -p /tmp/out\n"
        "export HOME=/tmp OMP_NUM_THREADS=1\n"
        "python3 -u /tmp/compare.py /tmp/dtm %s 2>&1 | tee /tmp/out/compare.txt\n"
        "grep -q '^COMPARE OK' /tmp/out/compare.txt || { echo \"FAIL: compare\"; exit 1; }\n" % TILE)],
    "resources": {"cpu": 4, "memory_gib": 16, "architecture": "arm64", "families": ["m8g"], "disk_gib": 40},
    "inputs": [{"source": "%s/%s" % (IN, f), "destination": "/tmp/%s" % f} for f in ["compare.py", "compare.sha256"]]
    + [{"source": "s3://${COOKBOOK_BUCKET}/%s/pointcloud-real/r1/dtm-2m-mean.tif" % p, "destination": "/tmp/dtm/dtm-%s.tif" % c}
       for p, c in (("runs", "arm64"), ("runs-x86_64", "x86_64"), ("runs-amd", "amd"))],
    "outputs": [{"source": "/tmp/out/compare.txt", "destination": "s3://${COOKBOOK_BUCKET}/runs/pointcloud-real/compare.txt"}],
    "lifecycle": {"ttl": "1h", "on_complete": "terminate", "cost_limit": 0.3},
})
