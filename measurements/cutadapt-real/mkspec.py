#!/usr/bin/env python3
"""Write the cutadapt-real TaskSpec (arm64; x86 and AMD come from scripts/spec_arch.py).

    python3 mkspec.py <outdir>

One task: the recipe's invocation on both whole Nicholls et al. 2019 Zymo runs, which
measurements/metaphlan-real already staged and pinned (reused, not copied).
"""
import json
import os
import sys

out = sys.argv[1]
# (accession, ENA read_count = pairs, ENA base_count, extra checks)
SAMPLES = [("ERR2984773", 8785731, 2653290762, "full"),   # D6300 even, MiSeq 2x151
           ("ERR2935805", 47832553, 9662175706, "")]      # D6310 log, HiSeq 1500 2x101
B = "s3://${COOKBOOK_BUCKET}/"
IN = B + "inputs/cutadapt-real/"
READS = B + "inputs/metaphlan-real/prep/"
CUTADAPT = "quay.io/aarchbio/cutadapt@sha256:434a8a2384580ec718463bac3723a5c407b083855af7bab5f802f9100033781d"


def io(src, name):
    return {"source": src, "destination": "/tmp/" + name}


files = ["trim.sh", "check.py"]
inputs = [io(IN + f, f) for f in files] + [io(IN + "pins.sha256", "pins.sha256")]
for a, _, _, _ in SAMPLES:
    for m in (1, 2):
        f = "%s_%d.fastq.gz" % (a, m)
        files.append(f)
        inputs.append(io(READS + "%s/%s" % (a, f), f))
sel = "|".join(f.replace(".", r"\.") for f in files)
cmd = r"""set +e
# spawn runs task commands under bash -e; `set -uo pipefail` does not clear it (spawn#707).
cd /tmp
grep -E ' (%s)$' pins.sha256 > pins.sel
[ "$(wc -l < pins.sel)" -eq %d ] && sha256sum -c pins.sel || { echo "FAIL: a staged input does not match its pin"; exit 1; }
bash trim.sh 16 %s
""" % (sel, len(files), " ".join("%s:%d:%d:%s" % s for s in SAMPLES))

spec = {
    "task_id": "cookbook-cutadapt-real",
    "container": CUTADAPT,
    "command": ["bash", "-c", cmd],
    # 16 GiB of tmpfs (half the RAM) holds the 9.1 GB of staged reads; the checked-only outputs
    # (~45 GB, uncompressed where only a check reads them) go on the container disk.
    "resources": {"cpu": 16, "memory_gib": 32, "architecture": "arm64", "families": ["c8g"], "disk_gib": 100},
    "inputs": inputs,
    "outputs": [{"source": "/tmp/out/", "destination": B + "runs/cutadapt-real/zymo/"}],
    "lifecycle": {"ttl": "3h", "on_complete": "terminate", "cost_limit": 1.5},
}
with open(os.path.join(out, "01-trim.task.json"), "w") as fh:
    json.dump(spec, fh, indent=2)
    fh.write("\n")
