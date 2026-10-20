"""Write repro-x86_64.task.json: repro.sh on c8i.large in the pinned x86 micromamba image.
    python3 measurements/xtb-real/mkrepro.py <label>"""
import hashlib
import json
import os
import sys

D = os.path.dirname(os.path.abspath(__file__))
RUN = sys.argv[1] if len(sys.argv) > 1 else "repro1"
IMG = "docker.io/mambaorg/micromamba@sha256:9a2239d03b9464c761ac87c13f45b37ec0f774db7d1b152a5f9add146bee2e72"
IN = "s3://${COOKBOOK_BUCKET}/inputs/xtb-real/"
sha = hashlib.sha256(open(os.path.join(D, "repro.sh"), "rb").read()).hexdigest()
cmd = ("set +e\nset -uo pipefail\ncd /tmp\n"
       "echo '%s  /tmp/repro.sh' | sha256sum -c || { echo 'FAIL: repro.sh does not match its pin'; exit 1; }\n"
       "bash /tmp/repro.sh\n" % sha)
s = {"task_id": "cookbook-xtb-real-%s-x8664" % RUN, "container": IMG, "command": ["bash", "-c", cmd],
     "resources": {"cpu": 2, "memory_gib": 4, "architecture": "x86_64", "families": ["c8i"], "disk_gib": 20},
     "inputs": [{"source": IN + "gmtkn55/gmtkn55-v1.tar.gz", "destination": "/tmp/in/gmtkn55-v1.tar.gz"},
                {"source": IN + "gmtkn55/gmtkn55-v1.sha256", "destination": "/tmp/in/gmtkn55-v1.sha256"},
                {"source": IN + "repro.sh", "destination": "/tmp/repro.sh"}],
     "outputs": [{"source": "/tmp/out/", "destination": "s3://${COOKBOOK_BUCKET}/runs-x86_64/xtb-real/%s/" % RUN}],
     "lifecycle": {"ttl": "40m", "on_complete": "terminate", "cost_limit": 0.1}}
with open(os.path.join(D, "repro-x86_64.task.json"), "w") as fh:
    json.dump(s, fh, indent=2)
    fh.write("\n")
