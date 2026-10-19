"""Write a task spec with the current sha256 of the staged scripts pinned into its command.
   python3 measurements/yt-real/mkspec.py <spec file> <run label> <family> <vCPU> <workers> [outputs...]
real.task.json is the committed arm64 spec; spec_arch.py makes the Intel and AMD ones from it."""
import hashlib
import json
import os
import sys

D = os.path.dirname(os.path.abspath(__file__))
IMAGE = "quay.io/aarchsci/astro@sha256:eaa6cb334d95db68662c51646a139c0ff635e9ebf3cb603cf5856ad40c26d066"
SPEC, RUN, FAMILY, CPU, NP = sys.argv[1], sys.argv[2], sys.argv[3], int(sys.argv[4]), int(sys.argv[5])
ONLY = sys.argv[6:]
MEM = {"c8g": 2, "m8g": 4, "r8g": 8}[FAMILY] * CPU
SCRIPTS = ["run.sh", "real.py"]
pins = "".join("%s  %s\\n" % (hashlib.sha256(open(os.path.join(D, f), "rb").read()).hexdigest(), f)
               for f in SCRIPTS)
cmd = ("set +e\nset -uo pipefail\ncd /tmp\n"
       "echo '== the staged scripts must match their pins =='\n"
       "printf '%s' > scripts.sha256\n"
       "sha256sum -c scripts.sha256 || { echo 'FAIL: a staged script does not match its pin'; exit 1; }\n"
       "bash /tmp/run.sh %d %s\n") % (pins, NP, " ".join(ONLY))
S3 = "s3://${COOKBOOK_BUCKET}/"
IN = S3 + "inputs/yt-real/"
OUT = S3 + "runs/yt-real/%s/" % RUN
spec = {
    "task_id": "cookbook-yt-real-%s" % RUN,
    "command": ["bash", "-c", cmd],
    "container": IMAGE,
    "resources": {"cpu": CPU, "memory_gib": MEM, "architecture": "arm64", "families": [FAMILY]},
    "inputs": [{"source": IN + "Enzo_64/", "destination": "/tmp/data/Enzo_64/"},
               {"source": IN + "files.sha256", "destination": "/tmp/files.sha256"}]
    + [{"source": IN + f, "destination": "/tmp/" + f} for f in SCRIPTS],
    "outputs": [{"source": "/tmp/out/" + f, "destination": OUT + f}
                for f in ["check.tsv", "observations.tsv", "real.log", "host.txt", "footprint.txt",
                          "staged.sha256"]],
    "lifecycle": {"ttl": "1h", "on_complete": "terminate", "cost_limit": 0.3},
}
with open(os.path.join(D, SPEC), "w") as fh:
    json.dump(spec, fh, indent=2)
    fh.write("\n")
print(os.path.join(D, SPEC))
