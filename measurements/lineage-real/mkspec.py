#!/usr/bin/env python3
"""Write measurements/lineage-real's arm64 specs, pinning the staged scripts by their current
sha256. x86/AMD come from scripts/spec_arch.py (01 and 02 only), never from a second copy.
Task 00 chooses the genomes once, on Graviton4; 01 (Nextclade) and 02 (pangolin + every check)
run on every chip, and each chip's 02 reads that chip's own 01.
    mkspec.py <run label, e.g. r1> [threads] [query]"""
import hashlib
import json
import os
import sys

RUN = sys.argv[1]
T = int(sys.argv[2]) if len(sys.argv) > 2 else 8
Q = sys.argv[3] if len(sys.argv) > 3 else "query.fasta.gz"   # a sizing probe: sub.fasta.gz
D = os.path.dirname(os.path.abspath(__file__))
IN = "s3://${COOKBOOK_BUCKET}/inputs/lineage-real"
OUT = "s3://${COOKBOOK_BUCKET}/runs/lineage-real/%s" % RUN
# the recipe's own pins (recipes/lineage/README.md frontmatter)
NEXTCLADE = "quay.io/aarchbio/nextclade@sha256:7cbf30abb4fa7fbc9b9a2c608b9ed361d8527514af139fb6c1883dce16e16f62"
PANGOLIN = "quay.io/aarchbio/pangolin@sha256:32551903d08cbe39fc70fcf924326fec08c100b8416346acd0302bb0e7f2a03a"


def sha(f):
    return hashlib.sha256(open(os.path.join(D, f), "rb").read()).hexdigest()


def command(scripts, run):
    pins = "".join("%s  %s\\n" % (sha(s), s) for s in scripts)
    return ["bash", "-c",
            "set +e\ncd /tmp\nprintf '%s' > scripts.sha256\n"
            "sha256sum -c scripts.sha256 || { echo 'FAIL: staged script does not match its pin'; exit 1; }\n"
            "%s\n" % (pins, run)]


def o(src, dst):
    return {"source": src, "destination": dst}


def spec(name, scripts, run, image, family, cpu, mem, inputs, outputs, ttl, cap, disk=40, label=None):
    return {
        "task_id": "cookbook-lineage-real-%s-%s" % (name, label or RUN),
        "command": command(scripts, run),
        "container": image,
        "resources": {"cpu": cpu, "memory_gib": mem, "disk_gib": disk,
                      "architecture": "arm64", "families": [family]},
        "inputs": [o("%s/%s" % (IN, s), "/tmp/%s" % s) for s in scripts] + inputs,
        "outputs": outputs,
        "lifecycle": {"ttl": ttl, "on_complete": "terminate", "cost_limit": cap},
    }


NS = [o("%s/nextstrain/%s" % (IN, f), "/tmp/nextstrain/%s" % f)
      for f in ("sequences.fasta.zst", "metadata.tsv.zst", "cog_uk_accessions.tsv.zst")]
QUERY = [o("%s/sel/%s" % (IN, f), "/tmp/%s" % f)
         for f in ("query.fasta.gz", "sub.fasta.gz", "truth.tsv", "inputs.sha256")]
choose = spec("choose", ["choose.sh", "choose.py"], "bash /tmp/choose.sh 100000 2000 20261020", PANGOLIN,
              "r8g", 4, 32, NS, [o("/tmp/out/", "%s/sel/" % IN)], "3h", 0.8, label="r1")
nextclade = spec("nextclade", ["nextclade.sh"], "bash /tmp/nextclade.sh %d %s" % (T, Q), NEXTCLADE, "m8g", T, 4 * T,
                 QUERY + [o("s3://${COOKBOOK_BUCKET}/inputs/lineage/dataset.tar", "/tmp/dataset.tar"),
                          o("s3://${COOKBOOK_BUCKET}/inputs/lineage/pins.sha256", "/tmp/dataset.sha256")],
                 [o("/tmp/out/", "%s/nextclade/" % OUT)], "4h", 0.6)
pangolin = spec("pangolin", ["pangolin.sh", "timed.py", "check.py"], "bash /tmp/pangolin.sh %d %s" % (T, Q), PANGOLIN,
                "m8g", T, 4 * T,
                QUERY + [o("s3://${COOKBOOK_BUCKET}/inputs/lineage/alias_key.json", "/tmp/alias_key_v1.41.json")] +
                [o("%s/nextclade/%s" % (OUT, f), "/tmp/%s" % f) for f in ("nextclade.tsv.gz", "nextclade_sub.tsv")],
                [o("/tmp/out/", "%s/pangolin/" % OUT)], "6h", 1.5, disk=80)
for fn, s in [("00-choose.task.json", choose), ("01-nextclade.task.json", nextclade),
              ("02-pangolin.task.json", pangolin)]:
    with open(os.path.join(D, fn), "w") as fh:
        json.dump(s, fh, indent=2)
        fh.write("\n")
    print(fn)
