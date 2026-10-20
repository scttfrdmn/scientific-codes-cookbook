"""Write the run's TaskSpec with the sha256 of each staged script pinned into the command.
    python3 measurements/xtb-real/mkspec.py <run label> [vcpus] [family] [limit]
Writes 01-gmtkn55.task.json (or probe-<label>.task.json when a limit is given)."""
import hashlib
import json
import os
import sys

D = os.path.dirname(os.path.abspath(__file__))
IMAGE = "quay.io/aarchsci/comp-chem@sha256:f22ef7a3b0d6e506782596e297472fb355338502aaba668ba64d6e3a3cebe031"
RUN = sys.argv[1] if len(sys.argv) > 1 else "r1"
NP = int(sys.argv[2]) if len(sys.argv) > 2 else 16
FAM = sys.argv[3] if len(sys.argv) > 3 else "c8g"
LIMIT = int(sys.argv[4]) if len(sys.argv) > 4 else 0
GIB = {"c8g": 2, "m8g": 4, "r8g": 8}
S3 = "s3://${COOKBOOK_BUCKET}/"
IN = S3 + "inputs/xtb-real/"
OUT = S3 + "runs/xtb-real/%s/" % RUN
R = ["run.sh", "xtb_run.py"]
pins = "".join("%s  %s\\n" % (hashlib.sha256(open(os.path.join(D, f), "rb").read()).hexdigest(), f) for f in R)
cmd = ("set +e\n# spawn runs task commands under bash -e; `set -uo pipefail` does not clear it (spawn#707).\n"
       "set -uo pipefail\ncd /tmp\n"
       "printf '%s' > scripts.sha256\n"
       "sha256sum -c scripts.sha256 || { echo 'FAIL: a staged script does not match its pin'; exit 1; }\n"
       "bash /tmp/run.sh %d %d\n" % (pins, NP, LIMIT))
inputs = [{"source": IN + "gmtkn55/gmtkn55-v1.tar.gz", "destination": "/tmp/in/gmtkn55-v1.tar.gz"},
          {"source": IN + "gmtkn55/gmtkn55-v1.sha256", "destination": "/tmp/in/gmtkn55-v1.sha256"}]
inputs += [{"source": IN + f, "destination": "/tmp/" + f} for f in R]
outs = ["xtb.jsonl.gz", "check.txt", "gradient-fail.txt", "tblite-probe.txt", "subsets.tsv", "reactions.tsv", "run.log", "host.txt", "footprint.txt",
        "sources.check"]
s = {
    "task_id": "cookbook-xtb-real-%s" % RUN,
    "container": IMAGE,
    "command": ["bash", "-c", cmd],
    "resources": {"cpu": NP, "memory_gib": GIB[FAM] * NP, "architecture": "arm64", "families": [FAM]},
    "inputs": inputs,
    "outputs": [{"source": "/tmp/out/" + f, "destination": OUT + f} for f in outs],
    "lifecycle": {"ttl": "3h" if not LIMIT else "1h", "on_complete": "terminate",
                  "cost_limit": 1.5 if not LIMIT else 0.3},
}
name = "probe-%s.task.json" % RUN if LIMIT else "01-gmtkn55.task.json"
with open(os.path.join(D, name), "w") as fh:
    json.dump(s, fh, indent=2)
    fh.write("\n")
print(os.path.join(D, name))
