#!/usr/bin/env python3
"""Write measurements/diamond-real's three arm64 specs, pinning the staged scripts by their current
sha256. x86 and AMD come from scripts/spec_arch.py, never from a second copy.
    mkspec.py"""
import hashlib
import json
import os

D = os.path.dirname(os.path.abspath(__file__))
IN = "s3://${COOKBOOK_BUCKET}/inputs/diamond-real"
DATA = IN + "/uniprot-2026_03"
OUT = "s3://${COOKBOOK_BUCKET}/runs/diamond-real"
DIAMOND = "quay.io/aarchbio/diamond@sha256:6356f5b243c35bf7fd8a9b8f3962f96c2693d8d593cba90de21fa508c6ce79f5"
# The fetch is not DIAMOND: any image with python3 does; this is the one fastjet-real fetches with.
FETCH = "quay.io/aarchsci/md@sha256:285377124854316a3604756eecde350dfb45b26b24b9f0e4f247d3b0a0dff58f"
THREADS = 16


def sha(f):
    return hashlib.sha256(open(os.path.join(D, f), "rb").read()).hexdigest()


def pinned(files, run):
    # spawn runs task commands under bash -e; `set -uo pipefail` does not clear it (spawn#707).
    return ["bash", "-c",
            "set +e\nset -uo pipefail\nexport PATH=/usr/local/bin:/opt/conda/bin:$PATH HOME=/tmp TMPDIR=/tmp\n"
            "cd /tmp\nmkdir -p /tmp/out\n{ %s; } > scripts.sha256\n"
            "sha256sum -c scripts.sha256 || { echo 'FAIL: a staged script does not match its pin'; exit 1; }\n"
            "%s\n" % ("; ".join("printf '%%s  %%s\\n' %s %s" % (sha(f), f) for f in files), run)]


fetch = {
    "task_id": "cookbook-diamond-real-fetch",
    "command": pinned(["fetch.py"],
                      "python3 -u fetch.py 2>&1 | tee /tmp/out/fetch.log\n"
                      "test \"${PIPESTATUS[0]}\" = 0 && grep -q 'FETCH OK' /tmp/out/fetch.log "
                      "|| { echo 'FAIL: fetch'; exit 1; }"),
    "container": FETCH,
    # 32 GiB so the 16 GiB tmpfs holds the 8.9 GB of downloads.
    "resources": {"cpu": 4, "memory_gib": 32, "architecture": "arm64", "families": ["r8g"]},
    "inputs": [{"source": "%s/fetch.py" % IN, "destination": "/tmp/fetch.py"}],
    "outputs": [{"source": "/tmp/out/", "destination": DATA + "/"}],
    "lifecycle": {"ttl": "3h", "on_complete": "terminate", "cost_limit": 0.6},
}
search = {
    "task_id": "cookbook-diamond-real-search",
    "command": pinned(["search.sh", "pins.sha256"], "bash /tmp/search.sh %d" % THREADS),
    "container": DIAMOND,
    # 32 GiB: the 16 GiB tmpfs holds the 8.9 GB of staged input; the database and work go on disk.
    "resources": {"cpu": THREADS, "memory_gib": 32, "disk_gib": 120,
                  "architecture": "arm64", "families": ["c8g"]},
    "inputs": [{"source": "%s/%s" % (IN, f), "destination": "/tmp/%s" % f} for f in ("search.sh", "pins.sha256")]
    + [{"source": "%s/%s" % (DATA, f), "destination": "/tmp/%s" % f}
       for f in ("uniref50.fasta.gz", "uniprot_sprot.fasta.gz", "HUMAN_9606_idmapping_selected.tab.gz")],
    "outputs": [{"source": "/tmp/out/", "destination": OUT + "/search/"}],
    "lifecycle": {"ttl": "8h", "on_complete": "terminate", "cost_limit": 3.5},   # Graviton4: 2h49m, $1.79
}
RUNS = {"arm64": "runs/diamond-real/search", "arm64run1": "runs/diamond-real/search-run1",
        "x86_64": "runs-x86_64/diamond-real/search", "amd": "runs-amd/diamond-real/search"}
compare = {
    "task_id": "cookbook-diamond-real-compare",
    "command": pinned(["compare.sh"], "bash /tmp/compare.sh"),
    "container": DIAMOND,
    "resources": {"cpu": 4, "memory_gib": 16, "disk_gib": 40, "architecture": "arm64", "families": ["m8g"]},
    "inputs": [{"source": "%s/compare.sh" % IN, "destination": "/tmp/compare.sh"}]
    + [{"source": "s3://${COOKBOOK_BUCKET}/%s/%s" % (RUNS[c], f),
        "destination": "/tmp/%s.%s" % (c, f)} for c in RUNS for f in ("hits.tsv.gz", "self.txt")],
    "outputs": [{"source": "/tmp/out/", "destination": OUT + "/compare/"}],
    "lifecycle": {"ttl": "2h", "on_complete": "terminate", "cost_limit": 0.3},
}
for fn, s in [("00-fetch.task.json", fetch), ("01-search.task.json", search), ("02-compare.task.json", compare)]:
    with open(os.path.join(D, fn), "w") as fh:
        json.dump(s, fh, indent=2)
        fh.write("\n")
    print(fn)
