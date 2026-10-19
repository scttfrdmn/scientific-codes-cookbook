"""Write 00-fetch.task.json and real.task.json with the sha256 of each staged script pinned into
the command.   python3 measurements/qiskit-real/mkspec.py <run label> [family] [vcpus]"""
import hashlib
import json
import os
import sys

D = os.path.dirname(os.path.abspath(__file__))
IMAGE = "quay.io/aarchsci/quantum@sha256:4f22a72396c229660587b4be43bf7f832046dcf9006b3aad8f81cbfd82c6207b"
RUN = sys.argv[1] if len(sys.argv) > 1 else "r1"
FAMILY = sys.argv[2] if len(sys.argv) > 2 else "m8g"
NP = int(sys.argv[3]) if len(sys.argv) > 3 else 16
MEM = {"c8g": 2, "m8g": 4, "r8g": 8}[FAMILY] * NP
S3 = "s3://${COOKBOOK_BUCKET}/"
IN = S3 + "inputs/qiskit-real/"
OUT = S3 + "runs/qiskit-real/%s/" % RUN
HEAD = ("set +e\n# spawn runs task commands under bash -e; `set -uo pipefail` does not clear it (spawn#707).\n"
        "set -uo pipefail\ncd /tmp\n")


def pins(files):
    return "".join("%s  %s\\n" % (hashlib.sha256(open(os.path.join(D, f), "rb").read()).hexdigest(), f)
                   for f in files)


def check(files):
    return ("printf '%s' > scripts.sha256\n"
            "sha256sum -c scripts.sha256 || { echo 'FAIL: a staged script does not match its pin'; exit 1; }\n"
            % pins(files))


def write(name, spec):
    with open(os.path.join(D, name), "w") as fh:
        json.dump(spec, fh, indent=2)
        fh.write("\n")
    print(os.path.join(D, name))


F = ["fetch.py", "qasmbench-357b942.gitblobs"]
write("00-fetch.task.json", {
    "task_id": "cookbook-qiskit-real-fetch",
    "container": IMAGE,
    "command": ["bash", "-c", HEAD + check(F) +
                "export PATH=/opt/conda/bin:$PATH TMPDIR=/tmp\nmkdir -p /tmp/out\n"
                "python3 -u fetch.py qasmbench-357b942.gitblobs /tmp/out || { echo 'FAIL: fetch'; exit 1; }\n"
                "find /tmp/out -type f | wc -l\ndu -sh /tmp/out\necho DONE\n"],
    "resources": {"cpu": 2, "memory_gib": 8, "architecture": "arm64", "families": ["m8g"]},
    "inputs": [{"source": IN + f, "destination": "/tmp/" + f} for f in F],
    "outputs": [{"source": "/tmp/out/", "destination": IN + "qasmbench/"}],
    "lifecycle": {"ttl": "30m", "on_complete": "terminate", "cost_limit": 0.05},
})

R = ["run.sh", "real.py", "qasmbench.sha256"]
write("real.task.json", {
    "task_id": "cookbook-qiskit-real-%s" % RUN,
    "container": IMAGE,
    "command": ["bash", "-c", HEAD + check(R) + "bash /tmp/run.sh %d\n" % NP],
    "resources": {"cpu": NP, "memory_gib": MEM, "architecture": "arm64", "families": [FAMILY],
                  "disk_gib": 60},
    "inputs": [{"source": IN + "qasmbench/", "destination": "/tmp/qasmbench/"}]
    + [{"source": IN + f, "destination": "/tmp/" + f} for f in R],
    "outputs": [{"source": "/tmp/out/" + f, "destination": OUT + f}
                for f in ["check.txt", "real.log", "host.txt", "footprint.txt", "circuits.jsonl",
                          "phases.txt"]],
    "lifecycle": {"ttl": "4h", "on_complete": "terminate", "cost_limit": 1.6},
})
