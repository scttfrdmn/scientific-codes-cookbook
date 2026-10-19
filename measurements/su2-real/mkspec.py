"""Write this directory's arm64 specs (x86/AMD come from scripts/spec_arch.py on these).
   python3 measurements/su2-real/mkspec.py"""
import json
import os

D = os.path.dirname(os.path.abspath(__file__))
IMAGE = "quay.io/aarchsci/cfd-fv@sha256:830eaf94cc9a6b36d6307b08a702610f16ac9212235a984047fb8e329092ae46"
S3 = "s3://${COOKBOOK_BUCKET}/"


def spec(name, ranks, cpu, mem, fam, ttl, cap):
    cmd = ("set +e\n# spawn runs task commands under bash -e; `set -uo pipefail` does not clear it (spawn#707).\n"
           "cd /tmp\nsha256sum -c --quiet pins.sha256 || { echo \"FAIL: a staged input does not match its pin\"; exit 1; }\n"
           "echo \"pins: $(grep -c . pins.sha256) files verified\"\n"
           "bash run.sh %d\n") % ranks
    return {
        "task_id": "cookbook-su2-real-%s" % name,
        "container": IMAGE,
        "command": ["bash", "-c", cmd],
        "resources": {"cpu": cpu, "memory_gib": mem, "architecture": "arm64", "families": [fam]},
        "inputs": [{"source": S3 + "inputs/su2-real/data/", "destination": "/tmp/data/"}]
        + [{"source": S3 + "inputs/su2-real/" + f, "destination": "/tmp/" + f}
           for f in ("real.py", "run.sh", "pins.sha256")],
        "outputs": [{"source": "/tmp/out/", "destination": S3 + "runs/su2-real/%s/" % name}],
        "lifecycle": {"ttl": ttl, "on_complete": "terminate", "cost_limit": cap},
    }


SPECS = {
    "real.task.json": spec("r2", 16, 16, 32, "c8g", "1h", 1.0),
}
for fn, s in SPECS.items():
    with open(os.path.join(D, fn), "w") as fh:
        json.dump(s, fh, indent=2)
        fh.write("\n")
    print(os.path.join(D, fn))
