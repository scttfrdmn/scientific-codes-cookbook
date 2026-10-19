"""Write real.task.json with the current sha256 of the staged scripts pinned into its command.
   python3 measurements/pymatgen-real/mkspec.py <run label> [family] [workers]"""
import hashlib
import json
import os
import sys

D = os.path.dirname(os.path.abspath(__file__))
IMAGE = "quay.io/aarchsci/dft@sha256:0740fab9721da533ce153cae3590b1c6822dd0decfa1838b0753e76ba4434a4e"
RUN = sys.argv[1]
FAMILY = sys.argv[2] if len(sys.argv) > 2 else "c8g"
NP = int(sys.argv[3]) if len(sys.argv) > 3 else 16
MEM = {"c8g": 2, "m8g": 4, "r8g": 8}[FAMILY] * NP
SCRIPTS = ["run.sh", "real.py", "inputs.sha256"]
pins = "".join("%s  %s\\n" % (hashlib.sha256(open(os.path.join(D, f), "rb").read()).hexdigest(), f) for f in SCRIPTS)
cmd = ("set +e\nset -uo pipefail\ncd /tmp\n"
       "echo '== the staged scripts must match their pins =='\n"
       "printf '%s' > scripts.sha256\n"
       "sha256sum -c scripts.sha256 || { echo 'FAIL: a staged script does not match its pin'; exit 1; }\n"
       "bash /tmp/run.sh %d\n") % (pins, NP)
S3 = "s3://${COOKBOOK_BUCKET}/"
IN = S3 + "inputs/pymatgen-real/"
OUT = S3 + "runs/pymatgen-real/%s/" % RUN
spec = {
    "task_id": "cookbook-pymatgen-real-%s" % RUN,
    "command": ["bash", "-c", cmd],
    "container": IMAGE,
    "resources": {"cpu": NP, "memory_gib": MEM, "architecture": "arm64", "families": [FAMILY]},
    "inputs": [{"source": IN + "mp-2025-09-25/summary/", "destination": "/tmp/mp/summary/"},
               {"source": IN + "mp-2025-09-25/thermo/", "destination": "/tmp/mp/thermo/"}]
    + [{"source": IN + f, "destination": "/tmp/" + f} for f in SCRIPTS],
    "outputs": [{"source": "/tmp/out/" + f, "destination": OUT + f}
                for f in ["check.txt", "real.log", "host.txt", "footprint.txt", "staged.sha256"]]
    + [{"source": "/tmp/pymatgen-real-out.tar.gz", "destination": OUT + "pymatgen-real-out.tar.gz"}],
    "lifecycle": {"ttl": "2h", "on_complete": "terminate", "cost_limit": 1.0},
}
with open(os.path.join(D, "real.task.json"), "w") as fh:
    json.dump(spec, fh, indent=2)
    fh.write("\n")
print(os.path.join(D, "real.task.json"))
