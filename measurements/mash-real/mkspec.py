#!/usr/bin/env python3
"""Write the two mash-real TaskSpecs (arm64; x86 and AMD come from scripts/spec_arch.py mash).

    python3 mkspec.py <outdir>

00-prep fetches every genome GTDB R232 places in g__Corynebacterium from NCBI once (arm64,
staging). 01-mash is the workload, run on every chip from the same pinned bytes. 02-sourmash puts
the same bytes through sourmash (x86 and AMD from scripts/spec_arch.py sourmash).
"""
import json
import os
import sys

out = sys.argv[1]
THREADS = 16
GENUS = "g__Corynebacterium"
B = "s3://${COOKBOOK_BUCKET}/"
IN = B + "inputs/mash-real/"
SRA_TOOLS = "quay.io/aarchbio/sra-tools@sha256:7145cf2f6f2e9265b7ace204d86b6ed1cbbd0b8b3ef31cbadf32bfa271229afd"
MASH = "quay.io/aarchbio/mash@sha256:abad0c5f4d3365661ffc5533bc6eb5f1bd07d773ea61b1abd1bfc00c1df813fe"
SOURMASH = "quay.io/aarchbio/sourmash@sha256:29733e7ac937dd17d8c7b84130f36b41da1a33f02abab0b2276c92c2683abd10"

# spawn runs task commands under bash -e; `set -uo pipefail` does not clear it (spawn#707).
PINS = r"""set +e
cd /tmp
grep -E ' (%s)$' pins.sha256 > pins.sel
[ "$(wc -l < pins.sel)" -eq %d ] && sha256sum -c pins.sel || { echo "FAIL: a staged input does not match its pin"; exit 1; }
"""


def pinned(names):
    return PINS % ("|".join(n.replace(".", r"\.") for n in names), len(names))


def io(src, name):
    return {"source": src, "destination": "/tmp/" + name}


prep = {
    "task_id": "cookbook-mash-real-prep",
    "container": SRA_TOOLS,
    "command": ["bash", "-c", pinned(["prep.sh", "fetch1.sh"]) + "bash prep.sh %s || exit 1\n" % GENUS],
    "resources": {"cpu": 4, "memory_gib": 16, "architecture": "arm64", "families": ["m8g"], "disk_gib": 40},
    "inputs": [io(IN + f, f) for f in ("prep.sh", "fetch1.sh", "pins.sha256")],
    "outputs": [{"source": "/tmp/out/", "destination": IN + "prep/"}],
    "lifecycle": {"ttl": "2h", "on_complete": "terminate", "cost_limit": 0.40},
}

data = ["genomes.tar", "genomes.tsv", "species.tsv"]
mash = {
    "task_id": "cookbook-mash-real",
    "container": MASH,
    "command": ["bash", "-c", pinned(["run.sh"] + data) + "bash run.sh %d || exit 1\n" % THREADS],
    "resources": {"cpu": THREADS, "memory_gib": 32, "architecture": "arm64", "families": ["c8g"], "disk_gib": 40},
    "inputs": [io(IN + f, f) for f in ("run.sh", "pins.sha256")] + [io(IN + "prep/" + f, f) for f in data],
    "outputs": [{"source": "/tmp/out/", "destination": B + "runs/mash-real/"}],
    "lifecycle": {"ttl": "2h", "on_complete": "terminate", "cost_limit": 0.60},
}

# the same bytes through sourmash, read against this chip's own mash screen (spec_arch rewrites /runs/)
xcheck = {
    "task_id": "cookbook-mash-real-sourmash",
    "container": SOURMASH,
    "command": ["bash", "-c", pinned(["sourmash_xcheck.py"] + data)
                + "export PATH=/opt/conda/bin:/usr/local/bin:$PATH HOME=/tmp TMPDIR=/var/tmp\n"
                + "python3 -u sourmash_xcheck.py %d || exit 1\n" % THREADS],
    "resources": {"cpu": THREADS, "memory_gib": 32, "architecture": "arm64", "families": ["c8g"], "disk_gib": 40},
    "inputs": [io(IN + f, f) for f in ("sourmash_xcheck.py", "pins.sha256")] + [io(IN + "prep/" + f, f) for f in data]
              + [io(B + "runs/mash-real/nearest.tsv", "mash_nearest.tsv")],
    "outputs": [{"source": "/tmp/out/", "destination": B + "runs/mash-real-sourmash/"}],
    "lifecycle": {"ttl": "2h", "on_complete": "terminate", "cost_limit": 0.40},
}

for name, s in (("00-prep", prep), ("01-mash", mash), ("02-sourmash", xcheck)):
    with open(os.path.join(out, name + ".task.json"), "w") as fh:
        json.dump(s, fh, indent=2)
        fh.write("\n")
