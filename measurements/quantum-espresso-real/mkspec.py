"""Write real.task.json with the current sha256 of the staged scripts pinned into its command.
   python3 measurements/quantum-espresso-real/mkspec.py <run label>"""
import hashlib
import json
import os
import sys

D = os.path.dirname(os.path.abspath(__file__))
IMAGE = "quay.io/aarchsci/dft@sha256:7574f6b15d6b0ec2d1bc27ebaabf97298e1ac619711754eb10da8b1f54600d03"
RANKS = 16
DECOMP_B = "-nk 2"
RUN = sys.argv[1]
SCRIPTS = ["run.sh", "check.py", "inputs.sha256"]
DATA = ["ausurf.in", "Au.pbe-nd-van.UPF"]
OUTS = ["check.txt", "results.tsv", "forces-A.tsv", "forces-B.tsv", "host.txt", "footprint.txt", "runs.txt",
        "input-edits.diff", "pw-A.out", "pw-B.out"]
pins = "".join("%s  %s\\n" % (hashlib.sha256(open(os.path.join(D, f), "rb").read()).hexdigest(), f) for f in SCRIPTS)
cmd = ("set +e\nset -uo pipefail\ncd /tmp\n"
       "echo '== the staged scripts must match their pins =='\n"
       "printf '%s' > scripts.sha256\n"
       "sha256sum -c scripts.sha256 || { echo 'FAIL: a staged script does not match its pin'; exit 1; }\n"
       "bash /tmp/run.sh %d '%s'\n") % (pins, RANKS, DECOMP_B)
S3 = "s3://${COOKBOOK_BUCKET}/"
OUT = S3 + "runs/quantum-espresso-real/%s/" % RUN
spec = {
    "task_id": "cookbook-quantum-espresso-real-%s" % RUN,
    "command": ["bash", "-c", cmd],
    "container": IMAGE,
    "resources": {"cpu": RANKS, "memory_gib": 32, "disk_gib": 30, "architecture": "arm64", "families": ["c8g"]},
    "inputs": [{"source": S3 + "inputs/quantum-espresso-real/" + f, "destination": "/tmp/" + f} for f in DATA + SCRIPTS],
    "outputs": [{"source": "/tmp/out/" + f, "destination": OUT + f} for f in OUTS]
    + [{"source": "/tmp/quantum-espresso-real-out.tar", "destination": OUT + "quantum-espresso-real-out.tar"}],
    "lifecycle": {"ttl": "4h", "on_complete": "terminate", "cost_limit": 2.0},
}
with open(os.path.join(D, "real.task.json"), "w") as fh:
    json.dump(spec, fh, indent=2)
    fh.write("\n")
print(os.path.join(D, "real.task.json"))
