"""Write the spec with the current sha256 of the staged scripts pinned into its command.
   python3 measurements/lammps-real/mkspec.py <steps> <run label> <spec name>
   (r1: real-1k.task.json, 1000 steps; r2: real.task.json, 5000 steps)"""
import hashlib
import json
import os
import sys

D = os.path.dirname(os.path.abspath(__file__))
IMAGE = "quay.io/aarchsci/md@sha256:285377124854316a3604756eecde350dfb45b26b24b9f0e4f247d3b0a0dff58f"
RANKS, X, Y, Z = 64, 4, 4, 4
STEPS, RUN, NAME = int(sys.argv[1]), sys.argv[2], sys.argv[3]
SCRIPTS = ["run.sh", "check.py", "inputs.sha256"]
pins = "".join("%s  %s\\n" % (hashlib.sha256(open(os.path.join(D, f), "rb").read()).hexdigest(), f) for f in SCRIPTS)
cmd = ("set +e\nset -uo pipefail\ncd /tmp\n"
       "echo '== the staged scripts must match their pins =='\n"
       "printf '%s' > scripts.sha256\n"
       "sha256sum -c scripts.sha256 || { echo 'FAIL: a staged script does not match its pin'; exit 1; }\n"
       "bash /tmp/run.sh %d %d %d %d %d\n") % (pins, RANKS, X, Y, Z, STEPS)
S3 = "s3://${COOKBOOK_BUCKET}/"
spec = {
    "task_id": "cookbook-lammps-real-%s" % RUN,
    "command": ["bash", "-c", cmd],
    "container": IMAGE,
    "resources": {"cpu": 64, "memory_gib": 128, "architecture": "arm64", "families": ["c8g"]},
    "inputs": [{"source": S3 + "inputs/lammps/data.rhodo", "destination": "/tmp/data.rhodo"}]
    + [{"source": S3 + "inputs/lammps-real/" + f, "destination": "/tmp/" + f}
       for f in ["in.rhodo.scaled"] + SCRIPTS],
    "outputs": [{"source": "/tmp/" + s, "destination": S3 + "runs/lammps-real/%s/" % RUN + os.path.basename(s)}
                for s in ["out/check.txt", "out/results.tsv", "out/host.txt", "out/lmp-real.log", "lammps-real-out.tar"]],
    "lifecycle": {"ttl": "1h", "on_complete": "terminate", "cost_limit": 2.0},
}
with open(os.path.join(D, NAME), "w") as fh:
    json.dump(spec, fh, indent=2)
    fh.write("\n")
print(os.path.join(D, NAME))
