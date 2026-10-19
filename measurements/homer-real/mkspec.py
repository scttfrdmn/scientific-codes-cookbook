#!/usr/bin/env python3
"""Write the homer-real TaskSpecs (arm64; x86 and AMD come from scripts/spec_arch.py).

    python3 mkspec.py <outdir>

whole.task.json is the workload: every peak, all of GRCh38. canary.task.json is the same task on
chr21's peaks only (the genome is still whole, so it clocks the preparse), under runs/.../canary/.
"""
import json
import os
import sys

out = sys.argv[1]
B = "s3://${COOKBOOK_BUCKET}/"
IN = B + "inputs/homer-real/"
MM2 = B + "inputs/minimap2-real/"
FA = "GRCh38_no_alt_analysis_set.fa"
HOMER = "quay.io/aarchbio/homer@sha256:b70d88a0e541fef5a5aa5312da987abccc439f3d1f06c358da272913fa7346c2"
THREADS = 16

CMD = r"""set +e
# spawn runs task commands under bash -e; `set -uo pipefail` does not clear it (spawn#707).
cd /tmp
sha256sum -c pins.sha256 > pins.check 2>&1 || { cat pins.check; echo "FAIL: a staged input does not match its pin"; exit 1; }
echo "pins: $(wc -l < pins.sha256) staged inputs match"
bash homer-real.sh %d %s || exit 1
"""


def io(src, name):
    return {"source": src, "destination": "/tmp/" + name}


def spec(name, chrom, ttl, cap):
    return {
        "task_id": "cookbook-homer-real-" + name,
        "container": HOMER,
        "command": ["bash", "-c", CMD % (THREADS, chrom)],
        "resources": {"cpu": 16, "memory_gib": 64, "architecture": "arm64", "families": ["m8g"]},
        "inputs": [io(MM2 + FA, FA), io(MM2 + FA + ".fai", FA + ".fai")]
                  + [io(IN + f, f) for f in ("ENCFF796WRU.bed.gz", "encode.md5", "homer-real.sh", "pins.sha256")],
        "outputs": [{"source": "/tmp/out/", "destination": B + "runs/homer-real/" + name + "/"}],
        "lifecycle": {"ttl": ttl, "on_complete": "terminate", "cost_limit": cap},
    }


for name, s in {"canary": spec("canary", "chr21", "3h", 2.0),
                "whole": spec("whole", "", "2h", 1.0)}.items():
    with open(os.path.join(out, name + ".task.json"), "w") as fh:
        json.dump(s, fh, indent=2)
        fh.write("\n")
