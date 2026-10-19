#!/usr/bin/env python3
"""Write the humann-real TaskSpecs (arm64; x86 and AMD come from scripts/spec_arch.py).

    python3 mkspec.py <outdir>

00-dbs streams HUMAnN's full ChocoPhlAn and UniRef90 into the bucket once (staging, arm64 only).
01-canary is 01-humann on the first 200,000 pairs plus the mate-ID comparison; 01-humann is the
workload, run on every chip from the same staged bytes.
"""
import json
import os
import sys

out = sys.argv[1]
ACC = "ERR2984773"   # ZymoBIOMICS D6300 (even), Nicholls et al. 2019, PRJEB29504 -- metaphlan-real's reads
B = "s3://${COOKBOOK_BUCKET}/"
IN = B + "inputs/humann-real/"
HUMANN = "quay.io/aarchbio/humann@sha256:c614308013fd2a7f7cef181d18ad0ce3c785b1b3243f62f0428fa313c1852ac6"

PINS = r"""set +e
# spawn runs task commands under bash -e; `set -uo pipefail` does not clear it (spawn#707).
cd /tmp
grep -E ' (%s)$' pins.sha256 > pins.sel
[ "$(wc -l < pins.sel)" -ge %d ] && sha256sum -c pins.sel || { echo "FAIL: a staged input does not match its pin"; exit 1; }
"""


def pinned(names):
    return PINS % ("|".join(n.replace(".", r"\.") for n in names), len(names))


def io(src, name):
    return {"source": src, "destination": "/tmp/" + name}


dbs = {
    "task_id": "cookbook-humann-real-dbs",
    "container": HUMANN,
    "command": ["bash", "-c", pinned(["fetch_dbs.py", "hashtree.py"]) + r"""mkdir -p out/db
df -k /tmp | awk 'NR==2{printf "tmpfs_gib %.1f\n", $2/1048576}'
python3 -u fetch_dbs.py chocophlan out/db > out/fetch-chocophlan.txt 2>&1 & P1=$!
python3 -u fetch_dbs.py uniref out/db > out/fetch-uniref.txt 2>&1 & P2=$!
wait $P1; R1=$?; wait $P2; R2=$?
cat out/fetch-chocophlan.txt out/fetch-uniref.txt
[ "$R1" = 0 ] && [ "$R2" = 0 ] || { echo "FAIL: a download did not match its advertised length"; exit 1; }
python3 -u hashtree.py write out/db out/db.sha256 || exit 1
df -k /tmp | awk 'NR==2{printf "tmpfs_used_gib %.1f\n", $3/1048576}'
echo "DBS OK"
"""],
    "resources": {"cpu": 16, "memory_gib": 124, "architecture": "arm64", "families": ["r8g"]},
    "inputs": [io(IN + f, f) for f in ("fetch_dbs.py", "hashtree.py", "pins.sha256")],
    "outputs": [{"source": "/tmp/out/", "destination": IN}],
    "lifecycle": {"ttl": "3h", "on_complete": "terminate", "cost_limit": 1.5},
}

files = ["run.sh", "check.py", "hashtree.py", "db.sha256", ACC + ".profile.txt", "zymo_d6300.tsv",
         ACC + "_1.fastq.gz", ACC + "_2.fastq.gz"]
inputs = [{"source": IN + "db/", "destination": "/tmp/db/"}] \
    + [io(IN + f, f) for f in ("run.sh", "check.py", "hashtree.py", "db.sha256", ACC + ".profile.txt", "pins.sha256")] \
    + [io(B + "inputs/metaphlan/zymo_d6300.tsv", "zymo_d6300.tsv")] \
    + [io(B + "inputs/metaphlan-real/prep/%s/%s_%d.fastq.gz" % (ACC, ACC, m), "%s_%d.fastq.gz" % (ACC, m)) for m in (1, 2)]


def run(task, pairs, extra, prefix, ttl, cap, manifest=True):
    # the canary runs before db.sha256 exists, and writes it
    fs = files if manifest else [f for f in files if f != "db.sha256"]
    ins = inputs if manifest else [i for i in inputs if not i["source"].endswith("/db.sha256")]
    return {
        "task_id": task,
        "container": HUMANN,
        "command": ["bash", "-c", pinned(fs) + "bash run.sh 16 %s %d zymo_d6300.tsv %s\n" % (ACC, pairs, extra)],
        # 128 GiB for the tmpfs (half of RAM) that holds the 49 GiB of databases; the work
        # files (the 7 GB FASTQ, HUMAnN's SAM and temp) go on the container disk.
        "resources": {"cpu": 16, "memory_gib": 124, "architecture": "arm64", "families": ["r8g"], "disk_gib": 120},
        "inputs": ins,
        "outputs": [{"source": "/tmp/out/", "destination": B + "runs/humann-real/" + prefix + "/"}],
        "lifecycle": {"ttl": ttl, "on_complete": "terminate", "cost_limit": cap},
    }


specs = {
    "00-dbs": dbs,
    "01-canary": run("cookbook-humann-real-canary", 200000, "rawids", "canary", "3h", 1.5, manifest=False),
    "01-humann": run("cookbook-humann-real", 0, "", "zymo", "5h", 3.0),
}
for name, s in specs.items():
    with open(os.path.join(out, name + ".task.json"), "w") as fh:
        json.dump(s, fh, indent=2)
        fh.write("\n")
