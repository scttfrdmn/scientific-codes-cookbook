#!/usr/bin/env python3
"""Write the qc-aggregate-real TaskSpecs, with the staged scripts' sha256 pinned into each command.

    python3 mkspec.py        # rewrites probe.task.json, fastqc.task.json, multiqc.task.json

The arm64 specs are what is committed; scripts/spec_arch.py turns them into the Intel (x86_64)
and AMD (amd) legs: the recipe's x86 pins, m8i / m8a, outputs under runs-x86_64/ and runs-amd/.
The FASTQs are not staged by this measurement: all eleven are already in the bucket from the
bwa, salmon, metaphlan and mageck real workloads, and are read where they are (cohort.tsv).
"""
import hashlib
import json
import os

D = os.path.dirname(os.path.abspath(__file__))
BKT = "s3://${COOKBOOK_BUCKET}/"
B = BKT + "inputs/qc-aggregate-real/"
FASTQC = "quay.io/aarchbio/fastqc@sha256:a4a8edb4754d731a0fd513cb4594a14766d662d5156c4e93299bdd57ef4b2fc3"
MULTIQC = "quay.io/aarchbio/multiqc@sha256:69df62f478fd50e624ab0c9af81fc23f36afd8c049fd7b3221f8d444cb0c9ffc"
RUN = BKT + "runs/qc-aggregate-real/r2/"
PRE = "set +e\nset -uo pipefail\n# spawn runs task commands under bash -e; `set -uo pipefail` does not clear it (spawn#707).\ncd /tmp\n"


def sha(f):
    return hashlib.sha256(open(os.path.join(D, f), "rb").read()).hexdigest()


def pins(files):
    lines = "".join("%s  %s\\n" % (sha(f), f) for f in files)
    return ("printf '%s' > scripts.sha256\nsha256sum -c scripts.sha256 || "
            "{ echo \"FAIL: a staged script does not match its pin\"; exit 1; }\n" % lines)


def write(name, spec):
    with open(os.path.join(D, name), "w") as o:
        json.dump(spec, o, indent=2)
        o.write("\n")


COHORT = [l.rstrip("\n").split("\t") for l in open(os.path.join(D, "cohort.tsv"))][1:]
FQ_IN = [{"source": BKT + key, "destination": "/tmp/fq/" + f} for f, key, *_ in COHORT]

write("probe.task.json", {
    "task_id": "cookbook-qc-aggregate-real-probe",
    "container": FASTQC,
    "command": ["bash", "-c", PRE + pins(["probe.sh", "recount.pl", "ena.md5"]) + "bash /tmp/probe.sh\n"],
    "resources": {"cpu": 16, "memory_gib": 64, "architecture": "arm64", "families": ["m8g"]},
    "inputs": FQ_IN + [{"source": B + f, "destination": "/tmp/" + f} for f in ("probe.sh", "recount.pl", "ena.md5")],
    "outputs": [{"source": "/tmp/out/", "destination": BKT + "runs/qc-aggregate-real/probe/"}],
    "lifecycle": {"ttl": "90m", "on_complete": "terminate", "cost_limit": 0.6},
})

if not os.path.exists(os.path.join(D, "inputs.sha256")):
    raise SystemExit("wrote probe.task.json; inputs.sha256 comes from the probe run (probe/inputs.sha256)")

# Sized from the probe: FastQC ran 15.8M x 75 bp in 54 s on one Graviton4 core at 0.39 GiB anon,
# so the cohort's wall is its largest file (99M reads) and the box is picked by tmpfs: 21 GiB of
# FASTQ needs 32 GiB of it, i.e. 64 GiB of RAM, which comes with 16 vCPU on m8g.
S1 = ["fastqc.sh", "recount.pl", "check_fastqc.pl", "cohort.tsv", "ena.md5", "inputs.sha256"]
write("fastqc.task.json", {
    "task_id": "cookbook-qc-aggregate-real-fastqc",
    "container": FASTQC,
    "command": ["bash", "-c", PRE + pins(S1) + "bash /tmp/fastqc.sh 11\n"],
    "resources": {"cpu": 16, "memory_gib": 64, "architecture": "arm64", "families": ["m8g"]},
    "inputs": FQ_IN + [{"source": B + f, "destination": "/tmp/" + f} for f in S1],
    "outputs": [{"source": "/tmp/out/", "destination": RUN + "fastqc/"}],
    "lifecycle": {"ttl": "90m", "on_complete": "terminate", "cost_limit": 0.6},
})

S2 = ["multiqc.sh", "check_multiqc.py"]
write("multiqc.task.json", {
    "task_id": "cookbook-qc-aggregate-real-multiqc",
    "container": MULTIQC,
    "command": ["bash", "-c", PRE + pins(S2) + "bash /tmp/multiqc.sh\n"],
    "resources": {"cpu": 2, "memory_gib": 4, "architecture": "arm64", "families": ["c8g"]},
    "inputs": [{"source": RUN + "fastqc/", "destination": "/tmp/in/"}]
    + [{"source": B + f, "destination": "/tmp/" + f} for f in S2],
    "outputs": [{"source": "/tmp/out/", "destination": RUN + "multiqc/"}],
    "lifecycle": {"ttl": "30m", "on_complete": "terminate", "cost_limit": 0.05},
})
