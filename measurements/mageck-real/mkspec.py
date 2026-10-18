#!/usr/bin/env python3
"""Write the mageck-real TaskSpecs, with the staged scripts' sha256 pinned into each command.

    python3 mkspec.py        # rewrites fetch.task.json and screen.task.json beside this file

The arm64 spec is what is committed; scripts/spec_arch.py turns screen.task.json into the Intel
(x86_64) and AMD (amd) legs: the recipe's x86 pin, c8i / c8a, outputs under runs-x86_64/ and
runs-amd/.
"""
import hashlib
import json
import os

D = os.path.dirname(os.path.abspath(__file__))
B = "s3://${COOKBOOK_BUCKET}/inputs/mageck-real/"
ARM = "quay.io/aarchbio/mageck@sha256:92a0f37e3ebe2deb7186eaaed15cb9803caf4fa1a8302d6b6c70d71d913edcba"
# the curl + coreutils image recipes/relion's fetch already uses
FETCH_IMG = "quay.io/aarchbio/metaphlan@sha256:7149c1bb1e23d01d273d56cc86502e36a2b976c598175327f6a32c42abfbbe48"
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


write("fetch.task.json", {
    "task_id": "cookbook-mageck-real-fetch",
    "container": FETCH_IMG,
    "command": ["bash", "-c", PRE + pins(["fetch.sh"]) + "bash /tmp/fetch.sh\n"],
    "resources": {"cpu": 8, "memory_gib": 32, "architecture": "arm64", "families": ["m8g"]},
    "inputs": [{"source": B + "fetch.sh", "destination": "/tmp/fetch.sh"}],
    "outputs": [{"source": "/tmp/stage/", "destination": B + "data/"}],
    "lifecycle": {"ttl": "90m", "on_complete": "terminate", "cost_limit": 0.6},
})

if not os.path.exists(os.path.join(D, "inputs.sha256")):
    raise SystemExit("wrote fetch.task.json; inputs.sha256 comes from the fetch run (data/inputs.sha256)")
SCRIPTS = ["run.sh", "recount.py", "check.py", "inputs.sha256"]
DATA = ["brunello.tsv", "CEGv2.txt", "NEGv1.txt", "plasmid.fastq.gz", "repA.fastq.gz", "repB.fastq.gz"]
write("screen.task.json", {
    "task_id": "cookbook-mageck-real",
    "container": ARM,
    "command": ["bash", "-c", PRE + pins(SCRIPTS) + "bash /tmp/run.sh 8\n"],
    "resources": {"cpu": 8, "memory_gib": 32, "architecture": "arm64", "families": ["m8g"]},
    "inputs": [{"source": B + "data/" + f, "destination": "/tmp/data/" + f} for f in DATA]
    + [{"source": B + f, "destination": "/tmp/" + f} for f in SCRIPTS],
    "outputs": [{"source": "/tmp/out/", "destination": "s3://${COOKBOOK_BUCKET}/runs/mageck-real/r3/"}],
    "lifecycle": {"ttl": "4h", "on_complete": "terminate", "cost_limit": 1.5},
})

# attribution probe: the same Python on each image, no MAGeCK (interp.py)
write("interp.task.json", {
    "task_id": "cookbook-mageck-real-interp",
    "container": ARM,
    "command": ["bash", "-c", PRE + pins(["interp.py"]) + "export PATH=/opt/conda/bin:/usr/local/bin:$PATH HOME=/tmp\n"
                "mkdir -p /tmp/out\npython3 -u /tmp/interp.py /tmp/data/plasmid.fastq.gz > /tmp/out/interp.txt 2>&1\n"
                "cat /tmp/out/interp.txt\ngrep -q 'INTERP OK' /tmp/out/interp.txt || exit 1\n"],
    "resources": {"cpu": 2, "memory_gib": 8, "architecture": "arm64", "families": ["m8g"]},
    "inputs": [{"source": B + "data/" + f, "destination": "/tmp/data/" + f} for f in ("plasmid.fastq.gz", "brunello.tsv")]
    + [{"source": B + "interp.py", "destination": "/tmp/interp.py"}],
    "outputs": [{"source": "/tmp/out/", "destination": "s3://${COOKBOOK_BUCKET}/runs/mageck-real/interp/"}],
    "lifecycle": {"ttl": "1h", "on_complete": "terminate", "cost_limit": 0.3},
})
