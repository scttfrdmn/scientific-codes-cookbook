"""Write diag-<arch>.task.json: diag.sh in a pinned micromamba image (arm64 on c8g, x86_64 on c8i).
    python3 measurements/xtb-real/mkdiag.py <label> [upstream]"""
import hashlib
import json
import os
import sys

D = os.path.dirname(os.path.abspath(__file__))
RUN = sys.argv[1] if len(sys.argv) > 1 else "d1"
MODE = sys.argv[2] if len(sys.argv) > 2 else ""   # "upstream": x86 only, upstream release binary + 6.6.1
# mambaorg/micromamba 2.9.0-debian13, the per-architecture entries of the manifest list recipes/fsl pins
MM = {"arm64": ("docker.io/mambaorg/micromamba@sha256:26c186ed2c9fdfd7aab1e7f8416b6ef54358557db8a0d9a7d11a07d1d224f1b2", "c8g", "runs"),
      "x86_64": ("docker.io/mambaorg/micromamba@sha256:9a2239d03b9464c761ac87c13f45b37ec0f774db7d1b152a5f9add146bee2e72", "c8i", "runs-x86_64")}
R = ["diag.sh", "diag.py", "xtb_run.py"]
IN = "s3://${COOKBOOK_BUCKET}/inputs/xtb-real/"
pins = "".join("%s  %s\\n" % (hashlib.sha256(open(os.path.join(D, f), "rb").read()).hexdigest(), f) for f in R)
for arch, (img, fam, pre) in MM.items():
    cmd = ("set +e\nset -uo pipefail\ncd /tmp\nprintf '%s' > scripts.sha256\n"
           "sha256sum -c scripts.sha256 || { echo 'FAIL: a staged script does not match its pin'; exit 1; }\n"
           "bash /tmp/diag.sh 8 %s\n" % (pins, MODE))
    out = "s3://${COOKBOOK_BUCKET}/%s/xtb-real/%s/" % (pre, RUN)
    s = {"task_id": "cookbook-xtb-real-%s-%s" % (RUN, arch.replace("_", "")), "container": img,
         "command": ["bash", "-c", cmd],
         "resources": {"cpu": 8, "memory_gib": 16, "architecture": arch, "families": [fam], "disk_gib": 30},
         "inputs": [{"source": IN + "gmtkn55/gmtkn55-v1.tar.gz", "destination": "/tmp/in/gmtkn55-v1.tar.gz"},
                    {"source": IN + "gmtkn55/gmtkn55-v1.sha256", "destination": "/tmp/in/gmtkn55-v1.sha256"}]
                   + [{"source": IN + f, "destination": "/tmp/" + f} for f in R],
         "outputs": [{"source": "/tmp/out/", "destination": out}],
         "lifecycle": {"ttl": "1h", "on_complete": "terminate", "cost_limit": 0.4}}
    with open(os.path.join(D, "diag-%s.task.json" % arch), "w") as fh:
        json.dump(s, fh, indent=2)
        fh.write("\n")
