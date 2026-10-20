"""Write 00-fetch.task.json and real.task.json, each staged script pinned by sha256 in the command.
    python3 measurements/spacy-real/mkspec.py <run label> [family] [vcpus] [first N shards]"""
import hashlib
import json
import os
import sys

D = os.path.dirname(os.path.abspath(__file__))
IMAGE = "quay.io/aarchsci/nlp@sha256:436dc03828933d9e176ee751038a8a63e4d41b393ed693bfed9f442efe90e397"
RUN = sys.argv[1] if len(sys.argv) > 1 else "r1"
FAMILY = sys.argv[2] if len(sys.argv) > 2 else "c8g"
NP = int(sys.argv[3]) if len(sys.argv) > 3 else 32
NS = sys.argv[4] if len(sys.argv) > 4 else ""
MEM = {"c8g": 2, "m8g": 4, "r8g": 8}[FAMILY] * NP
FIRST, LAST = 10000000, 10099999          # PMCID block: 100,000 consecutive ids
S3 = "s3://${COOKBOOK_BUCKET}/"
IN = S3 + "inputs/spacy-real/"
OUT = S3 + "runs/spacy-real/%s/" % RUN
HEAD = ("set +e\n# spawn runs task commands under bash -e; `set -uo pipefail` does not clear it (spawn#707).\n"
        "set -uo pipefail\ncd /tmp\n")


def pins(files):
    return "".join("%s  %s\\n" % (hashlib.sha256(open(os.path.join(D, f), "rb").read()).hexdigest(), f)
                   for f in files)


def check(files):
    return ("printf '%s' > scripts.sha256\n"
            "sha256sum -c scripts.sha256 || { echo 'FAIL: a staged script does not match its pin'; exit 1; }\n"
            % pins(files))


def write(name, spec):
    with open(os.path.join(D, name), "w") as fh:
        json.dump(spec, fh, indent=2)
        fh.write("\n")
    print(os.path.join(D, name))


write("00-fetch.task.json", {
    "task_id": "cookbook-spacy-real-fetch",
    "container": IMAGE,
    "command": ["bash", "-c", HEAD + check(["fetch.py"]) +
                "export PATH=/opt/conda/bin:$PATH TMPDIR=/tmp HOME=/tmp\nmkdir -p /tmp/out\n"
                "python3 -u fetch.py %d %d /tmp/out 2>&1 | tee /tmp/out/fetch.log\n"
                "test \"${PIPESTATUS[0]}\" = 0 || { echo 'FAIL: fetch'; exit 1; }\n"
                "(cd /tmp/out/ewt && sha256sum * > /tmp/out/ewt.sha256)\ncat /tmp/out/ewt.sha256\n"
                "du -sh /tmp/out/*\necho DONE\n" % (FIRST, LAST)],
    "resources": {"cpu": 8, "memory_gib": 32, "architecture": "arm64", "families": ["m8g"]},
    "inputs": [{"source": IN + "fetch.py", "destination": "/tmp/fetch.py"}],
    "outputs": [{"source": "/tmp/out/pmc/", "destination": IN + "pmc/"},
                {"source": "/tmp/out/ewt/", "destination": IN + "ewt/"},
                {"source": "/tmp/out/ewt.sha256", "destination": IN + "fetched-ewt.sha256"},
                {"source": "/tmp/out/fetch.log", "destination": IN + "fetch.log"}],
    "lifecycle": {"ttl": "90m", "on_complete": "terminate", "cost_limit": 0.4},
})

if os.path.exists(os.path.join(D, "shards.sha256")):
    R = ["run.sh", "real.py", "ewt.py", "shards.sha256", "ewt.sha256"]
    write("real.task.json", {
        "task_id": "cookbook-spacy-real-%s" % RUN,
        "container": IMAGE,
        "command": ["bash", "-c", HEAD + check(R) + "bash /tmp/run.sh %d %s\n" % (NP, NS)],
        "resources": {"cpu": NP, "memory_gib": MEM, "architecture": "arm64", "families": [FAMILY]},
        "inputs": [{"source": IN + "pmc/", "destination": "/tmp/pmc/"},
                   {"source": IN + "ewt/", "destination": "/tmp/ewt/"}]
        + [{"source": IN + f, "destination": "/tmp/" + f} for f in R],
        "outputs": [{"source": "/tmp/out/", "destination": OUT}],
        "lifecycle": {"ttl": "3h", "on_complete": "terminate", "cost_limit": 2.5},
    })
