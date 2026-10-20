"""Write real.task.json with the sha256 of each staged script pinned into the command.
    python3 measurements/cantera-real/mkspec.py <run label> [family] [vcpus]"""
import hashlib
import json
import os
import sys

D = os.path.dirname(os.path.abspath(__file__))
IMAGE = "quay.io/aarchsci/comp-chem@sha256:b5c1b1ec10cfbb5374d0e733310244f84b59dc4ce99620b645c4efb6f49c439d"
RUN = sys.argv[1] if len(sys.argv) > 1 else "r1"
FAMILY = sys.argv[2] if len(sys.argv) > 2 else "c8g"
NP = int(sys.argv[3]) if len(sys.argv) > 3 else 8
MEM = {"c8g": 2, "m8g": 4, "r8g": 8}[FAMILY] * NP
S3 = "s3://${COOKBOOK_BUCKET}/"
IN = S3 + "inputs/cantera-real/"
OUT = S3 + "runs/cantera-real/%s/" % RUN
HEAD = ("set +e\n# spawn runs task commands under bash -e; `set -uo pipefail` does not clear it (spawn#707).\n"
        "set -uo pipefail\ncd /tmp\n")
R = ["run.sh", "real.py", "targets.tsv", "sources.sha256"]
pins = "".join("%s  %s\\n" % (hashlib.sha256(open(os.path.join(D, f), "rb").read()).hexdigest(), f) for f in R)
check = ("printf '%s' > scripts.sha256\n"
         "sha256sum -c scripts.sha256 || { echo 'FAIL: a staged script does not match its pin'; exit 1; }\n" % pins)
spec = {
    "task_id": "cookbook-cantera-real-%s" % RUN,
    "container": IMAGE,
    "command": ["bash", "-c", HEAD + check + "bash /tmp/run.sh %d\n" % NP],
    "resources": {"cpu": NP, "memory_gib": MEM, "architecture": "arm64", "families": [FAMILY]},
    "inputs": [{"source": IN + "gri/", "destination": "/tmp/gri/"}]
    + [{"source": IN + f, "destination": "/tmp/" + f} for f in R],
    "outputs": [{"source": "/tmp/out/" + f, "destination": OUT + f}
                for f in ["check.txt", "real.log", "host.txt", "footprint.txt", "results.jsonl", "phases.txt",
                          "sources.check"]],
    "lifecycle": {"ttl": "3h", "on_complete": "terminate", "cost_limit": 0.6},
}
with open(os.path.join(D, "real.task.json"), "w") as fh:
    json.dump(spec, fh, indent=2)
    fh.write("\n")
print(os.path.join(D, "real.task.json"))
