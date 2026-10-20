#!/usr/bin/env python3
"""Write this measurement's TaskSpecs, with every staged script pinned by sha256.

    python3 measurements/psi4-real/mkspec.py <name> <arch> <family size> <run.sh args...>

<arch> is arm64, x86_64 or amd. arm64 runs the psi4 recipe's own pin (aarch.science `dft`). The x86
legs do NOT come from scripts/spec_arch.py: that resolves the recipe's x86 route, the `dft` env's
linux-64 twin, whose psi4 1.11 does not import (README, "x86"). They run psi4's own upstream image
at the same psi4 version, psi4/psi4:1.11.0, by its linux/amd64 digest. Everything else follows
spec_arch.py's rules: same-generation family (8g -> 8i / 8a), cost_limit x1.3 / x1.6, and outputs
under runs-x86_64/ or runs-amd/.

e.g. mkspec.py real arm64 m8g.4xlarge real 4 4 14000   ->  measurements/psi4-real/real.task.json
     mkspec.py real amd   m8g.4xlarge real 4 4 14000   ->  measurements/psi4-real/real-amd.task.json
"""
import hashlib
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ARM = "quay.io/aarchsci/dft@sha256:b356499318a2a257b475cbd2d35372d0e91c2fba2b35e9b5c2051596814bc049"
X86 = "docker.io/psi4/psi4@sha256:8c4c13f5e4e11b7506d2fd9f76751a7b45f41cb9fbb169ab520904d1d1caacb1"   # 1.11.0, linux/amd64
FAMILY = {"arm64": "g", "x86_64": "i", "amd": "a"}
COST = {"arm64": 1.0, "x86_64": 1.3, "amd": 1.6}
SCRIPTS = ["run.sh", "psi4_s66.py", "check_s66.py"]
SIZE = {"large": (2, 8), "xlarge": (4, 16), "2xlarge": (8, 32), "4xlarge": (16, 64), "8xlarge": (32, 128)}


def main(name, arch, box, *args):
    fam, size = box.split(".")
    fam = fam[:-1] + FAMILY[arch]
    cpu, mem = SIZE[size]
    if fam.startswith("r"):
        mem *= 2
    if fam.startswith("c"):
        mem //= 2
    pre = "runs" if arch == "arm64" else "runs-" + arch
    pins = "".join("%s  %s\\n" % (hashlib.sha256(open(os.path.join(HERE, s), "rb").read()).hexdigest(), s) for s in SCRIPTS)
    cmd = ("set +e\n# spawn runs task commands under bash -e; `set -uo pipefail` does not clear it (spawn#707).\n"
           "set -uo pipefail\ncd /tmp\nprintf '%s' > scripts.sha256\n"
           "sha256sum -c scripts.sha256 || { echo 'FAIL: a staged script does not match its pin'; exit 1; }\n"
           "bash /tmp/run.sh %s\n") % (pins, " ".join(args))
    ttl, cap = {"probe": ("4h", 2.5), "real": ("6h", 3.0), "import": ("30m", 0.1)}[name]
    spec = {
        "task_id": "cookbook-psi4-real-%s%s" % (name, "" if arch == "arm64" else "-" + arch.replace("_", "")),
        "container": ARM if arch == "arm64" else X86,
        "command": ["bash", "-c", cmd],
        "resources": {"cpu": cpu, "memory_gib": mem, "architecture": "arm64" if arch == "arm64" else "x86_64",
                      "families": [fam], "disk_gib": 300},
        "inputs": [{"source": "s3://${COOKBOOK_BUCKET}/inputs/psi4-real/dess66/DESS66.zip", "destination": "/tmp/in/DESS66.zip"},
                   {"source": "s3://${COOKBOOK_BUCKET}/inputs/psi4-real/dess66/dess66.sha256", "destination": "/tmp/in/dess66.sha256"}]
                  + [{"source": "s3://${COOKBOOK_BUCKET}/inputs/psi4-real/%s" % s, "destination": "/tmp/%s" % s} for s in SCRIPTS],
        "outputs": [{"source": "/tmp/out/", "destination": "s3://${COOKBOOK_BUCKET}/%s/psi4-real/%s/" % (pre, name)}],
        "lifecycle": {"ttl": ttl, "on_complete": "terminate", "cost_limit": round(cap * COST[arch], 2)},
    }
    out = os.path.join(HERE, "%s%s.task.json" % (name, "" if arch == "arm64" else "-" + arch))
    with open(out, "w") as fh:
        json.dump(spec, fh, indent=2)
        fh.write("\n")
    print(out)


if __name__ == "__main__":
    main(*sys.argv[1:])
