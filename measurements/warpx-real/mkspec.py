"""Write a spec with the current sha256 of the staged scripts pinned into its command.
   python3 measurements/warpx-real/mkspec.py <name> <label> <rf periods> <threads> <family.size> <cost_limit> [repeat]
   probe: mkspec.py probe.task.json probe 64 1,2,4,8,16 c8g.4xlarge 0.6 4
   real:  mkspec.py real.task.json r1 1280 <T> <box> <cap>"""
import hashlib
import json
import os
import sys

D = os.path.dirname(os.path.abspath(__file__))
IMAGE = "quay.io/aarchsci/pic@sha256:e02db0b494ce602c6948104fe4a9085b6fc004611f501c48c27eed1874100b95"
VCPU = {"large": 2, "xlarge": 4, "2xlarge": 8, "4xlarge": 16, "8xlarge": 32}
NAME, RUN, CYC, THREADS, BOX, CAP = sys.argv[1:7]
REPEAT = sys.argv[7] if len(sys.argv) > 7 else ""
EXTRA = sys.argv[8] if len(sys.argv) > 8 else ""
fam, size = BOX.split(".")
SCRIPTS = ["run.sh", "check.py", "inputs.sha256"]
DATA = ["inputs_base_1d_picmi.py", "analysis_1d.py"] + [f + ".dat" for f in (
    "electron_scattering", "excitation_1", "excitation_2", "ionization", "ion_scattering", "ion_back_scatter")]
pins = "".join("%s  %s\\n" % (hashlib.sha256(open(os.path.join(D, f), "rb").read()).hexdigest(), f) for f in SCRIPTS)
cmd = ("set +e\nset -uo pipefail\ncd /tmp\n"
       "echo '== the staged scripts must match their pins =='\n"
       "printf '%s' > scripts.sha256\n"
       "sha256sum -c scripts.sha256 || { echo 'FAIL: a staged script does not match its pin'; exit 1; }\n"
       "bash /tmp/run.sh %s %s '%s' '%s'\n") % (pins, CYC, THREADS, REPEAT, EXTRA)
S3 = "s3://${COOKBOOK_BUCKET}/"
spec = {
    "task_id": "cookbook-warpx-real-%s" % RUN,
    "command": ["bash", "-c", cmd],
    "container": IMAGE,
    "resources": {"cpu": VCPU[size], "memory_gib": 2 * VCPU[size], "architecture": "arm64", "families": [fam]},
    "inputs": [{"source": S3 + "inputs/warpx-real/" + f, "destination": "/tmp/" + f} for f in DATA + SCRIPTS],
    "outputs": [{"source": "/tmp/out/", "destination": S3 + "runs/warpx-real/%s/out/" % RUN},
                {"source": "/tmp/warpx-real-out.tar", "destination": S3 + "runs/warpx-real/%s/warpx-real-out.tar" % RUN}],
    "lifecycle": {"ttl": "3h", "on_complete": "terminate", "cost_limit": float(CAP)},
}
with open(os.path.join(D, NAME), "w") as fh:
    json.dump(spec, fh, indent=2)
    fh.write("\n")
print(os.path.join(D, NAME))
