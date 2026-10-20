"""Write probe.task.json and real.task.json with the sha256 of each staged script pinned into the
command.   python3 measurements/popgen-real/mkspec.py <run label> [family] [vcpus] [ttl] [cost_limit]"""
import hashlib
import json
import os
import sys

D = os.path.dirname(os.path.abspath(__file__))
IMAGE = "quay.io/aarchsci/popgen@sha256:aac6868e30955dc02250502f7c282625be12093081af943f48f2cbe90ae8760c"
RUN = sys.argv[1] if len(sys.argv) > 1 else "r1"
FAMILY = sys.argv[2] if len(sys.argv) > 2 else "m8g"
NP = int(sys.argv[3]) if len(sys.argv) > 3 else 16
TTL = sys.argv[4] if len(sys.argv) > 4 else "3h"
CAP = float(sys.argv[5]) if len(sys.argv) > 5 else 2.0
MEM = {"c8g": 2, "m8g": 4, "r8g": 8}[FAMILY] * NP
S3 = "s3://${COOKBOOK_BUCKET}/"
VCF = "ALL.chr%d.phase3_shapeit2_mvncall_integrated_v5a.20130502.genotypes.vcf.gz"
DATA = S3 + "inputs/plink-real/"
IN = S3 + "inputs/popgen-real/"
HEAD = ("set +e\n# spawn runs task commands under bash -e; `set -uo pipefail` does not clear it (spawn#707).\n"
        "set -uo pipefail\ncd /tmp\n")
SCRIPTS = ["run.sh", "real.py"]
OUTS = ["check.txt", "real.log", "host.txt", "footprint.txt", "jobs.jsonl", "phases.txt", "pins.check"]


def pins(files):
    return "".join("%s  %s\\n" % (hashlib.sha256(open(os.path.join(D, f), "rb").read()).hexdigest(), f)
                   for f in files)


def spec(name, label, chroms, ttl, cap):
    s = {
        "task_id": "cookbook-popgen-real-%s" % label,
        "container": IMAGE,
        "command": ["bash", "-c", HEAD +
                    "printf '%s' > scripts.sha256\n" % pins(SCRIPTS) +
                    "sha256sum -c scripts.sha256 || { echo 'FAIL: a staged script does not match its pin'; exit 1; }\n"
                    "bash /tmp/run.sh %d %s\n" % (NP, " ".join(map(str, chroms)))],
        "resources": {"cpu": NP, "memory_gib": MEM, "architecture": "arm64", "families": [FAMILY],
                      "disk_gib": 40},
        "inputs": [{"source": DATA + VCF % c, "destination": "/tmp/" + VCF % c} for c in chroms]
        + [{"source": DATA + "integrated_call_samples_v3.20130502.ALL.panel",
            "destination": "/tmp/integrated_call_samples_v3.20130502.ALL.panel"}]
        + [{"source": IN + f, "destination": "/tmp/" + f}
           for f in SCRIPTS + ["pins.sha256", "plink-real-id-all.txt"]],
        "outputs": [{"source": "/tmp/out/" + f,
                     "destination": S3 + "runs/popgen-real/%s/%s" % (label, f)} for f in OUTS],
        "lifecycle": {"ttl": ttl, "on_complete": "terminate", "cost_limit": cap},
    }
    with open(os.path.join(D, name), "w") as fh:
        json.dump(s, fh, indent=2)
        fh.write("\n")
    print(os.path.join(D, name))


spec("probe.task.json", "probe", [21, 22], "45m", 0.4)

# which columns of the simulated tables differ across builds (chr22 only, one process)
C = ["real.py", "colhash.py"]
with open(os.path.join(D, "colhash.task.json"), "w") as fh:
    json.dump({
        "task_id": "cookbook-popgen-real-colhash",
        "container": IMAGE,
        "command": ["bash", "-c", HEAD + "printf '%s' > scripts.sha256\n" % pins(C) +
                    "sha256sum -c scripts.sha256 || { echo 'FAIL: a staged script does not match its pin'; exit 1; }\n"
                    "export OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 HOME=/tmp\nmkdir -p /tmp/out\n"
                    "python3 -u colhash.py 2>&1 | tee /tmp/out/colhash.txt\n"
                    "test \"${PIPESTATUS[0]}\" = 0 || { echo 'FAIL: colhash'; exit 1; }\n"],
        "resources": {"cpu": 2, "memory_gib": 8, "architecture": "arm64", "families": ["m8g"]},
        "inputs": [{"source": IN + f, "destination": "/tmp/" + f} for f in C],
        "outputs": [{"source": "/tmp/out/colhash.txt",
                     "destination": S3 + "runs/popgen-real/colhash/colhash.txt"}],
        "lifecycle": {"ttl": "30m", "on_complete": "terminate", "cost_limit": 0.08},
    }, fh, indent=2)
    fh.write("\n")
print(os.path.join(D, "colhash.task.json"))
spec("real.task.json", RUN, list(range(1, 23)), TTL, CAP)
