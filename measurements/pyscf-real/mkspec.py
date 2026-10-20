"""Write the two TaskSpecs with the sha256 of each staged script pinned into the command.
    python3 measurements/pyscf-real/mkspec.py <run label> [vcpus] [max heavy atoms] [ccsd(t) heavy atoms] [probe]
Writes 01-pyscf.task.json and 02-psi4.task.json (or probe-*.task.json with `probe`)."""
import hashlib
import json
import os
import sys

D = os.path.dirname(os.path.abspath(__file__))
IMAGE = {  # each recipe's own pin
    "pyscf": "quay.io/aarchsci/comp-chem@sha256:b5c1b1ec10cfbb5374d0e733310244f84b59dc4ce99620b645c4efb6f49c439d",
    "psi4": "quay.io/aarchsci/dft@sha256:b356499318a2a257b475cbd2d35372d0e91c2fba2b35e9b5c2051596814bc049",
}
RUN = sys.argv[1] if len(sys.argv) > 1 else "r1"
NP = int(sys.argv[2]) if len(sys.argv) > 2 else 16
HA = int(sys.argv[3]) if len(sys.argv) > 3 else 6
CC = int(sys.argv[4]) if len(sys.argv) > 4 else 6
MODE = sys.argv[5] if len(sys.argv) > 5 else "real"
# PySCF: up to ~3 GiB per worker (measured), so 4 GiB/vCPU. Psi4: CCSD(T) scratch goes to the
# /tmp tmpfs (half the RAM) -- on the root disk, 16 workers' conventional MP2/CC files were I/O bound
# (~10x slower) and filled 40 GiB.
FAMILY = {"pyscf": "m8g", "psi4": "r8g"}
GIB = {"c8g": 2, "m8g": 4, "r8g": 8}
S3 = "s3://${COOKBOOK_BUCKET}/"
IN = S3 + "inputs/pyscf-real/"
OUT = S3 + "runs/pyscf-real/%s/" % RUN
HEAD = ("set +e\n# spawn runs task commands under bash -e; `set -uo pipefail` does not clear it (spawn#707).\n"
        "set -uo pipefail\ncd /tmp\n")
R = ["run.sh", "qm9.py", "pyscf_run.py", "psi4_run.py"]
pins = "".join("%s  %s\\n" % (hashlib.sha256(open(os.path.join(D, f), "rb").read()).hexdigest(), f) for f in R)
check = ("printf '%s' > scripts.sha256\n"
         "sha256sum -c scripts.sha256 || { echo 'FAIL: a staged script does not match its pin'; exit 1; }\n" % pins)
LOGS = {"pyscf": ["pyscf.jsonl", "atoms.json", "check.txt"], "psi4": ["psi4.jsonl", "crosscheck.txt"]}
TTL = {"pyscf": "8h", "psi4": "6h"}
CAP = {"pyscf": 2.5, "psi4": 1.8}


def spec(tool):
    inputs = [{"source": IN + "qm9/dsgdb9nsd.xyz.tar.bz2", "destination": "/tmp/qm9/dsgdb9nsd.xyz.tar.bz2"},
              {"source": IN + "qm9/uncharacterized.txt", "destination": "/tmp/qm9/uncharacterized.txt"},
              {"source": IN + "qm9/qm9.sha256", "destination": "/tmp/qm9.sha256"}]
    inputs += [{"source": IN + f, "destination": "/tmp/" + f} for f in R]
    if tool == "psi4":
        inputs.append({"source": OUT + "pyscf/pyscf.jsonl", "destination": "/tmp/in/pyscf.jsonl"})
    cmd = HEAD + check + "bash /tmp/run.sh %s %d %d %d %s\n" % (tool, NP, HA, CC, MODE)
    s = {
        "task_id": "cookbook-pyscf-real-%s-%s" % (tool, RUN),
        "container": IMAGE[tool],
        "command": ["bash", "-c", cmd],
        "resources": {"cpu": NP, "memory_gib": GIB[FAMILY[tool]] * NP, "architecture": "arm64",
                      "families": [FAMILY[tool]], "disk_gib": 40},
        "inputs": inputs,
        "outputs": [{"source": "/tmp/out/" + f, "destination": OUT + tool + "/" + f}
                    for f in LOGS[tool] + ["run.log", "host.txt", "footprint.txt", "sources.check"]],
        "lifecycle": {"ttl": TTL[tool], "on_complete": "terminate", "cost_limit": CAP[tool]},
    }
    name = ("probe-%s.task.json" if MODE == "probe" else {"pyscf": "01-%s.task.json", "psi4": "02-%s.task.json"}[tool]) % tool
    with open(os.path.join(D, name), "w") as fh:
        json.dump(s, fh, indent=2)
        fh.write("\n")
    print(os.path.join(D, name))


for t in ("pyscf", "psi4"):
    spec(t)
