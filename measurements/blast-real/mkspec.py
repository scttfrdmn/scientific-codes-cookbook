#!/usr/bin/env python3
"""Write measurements/blast-real's two arm64 specs, pinning the staged scripts by their current
sha256. x86/AMD come from scripts/spec_arch.py, never from a second copy.
    mkspec.py <run label, e.g. r1>"""
import hashlib
import json
import os
import sys

RUN = sys.argv[1]
D = os.path.dirname(os.path.abspath(__file__))
IN = "s3://${COOKBOOK_BUCKET}/inputs/blast-real"
OUT = "s3://${COOKBOOK_BUCKET}/runs/blast-real/%s" % RUN
BLAST = "quay.io/aarchbio/blast@sha256:377bfb5dc686ed9df1ce95f31835225c573bb6b75c17b0da80dfeb0c3b747b0c"
DIAMOND = "quay.io/aarchbio/diamond@sha256:6356f5b243c35bf7fd8a9b8f3962f96c2693d8d593cba90de21fa508c6ce79f5"
THREADS = 16


def sha(f):
    return hashlib.sha256(open(os.path.join(D, f), "rb").read()).hexdigest()


def command(script):
    return ["bash", "-c",
            "set +e\ncd /tmp\nprintf '%s  %s\\n' > scripts.sha256\n"
            "sha256sum -c scripts.sha256 || { echo 'FAIL: staged script does not match its pin'; exit 1; }\n"
            "bash /tmp/%s %d\n" % (sha(script), script, script, THREADS)]


def spec(name, script, image, inputs, ttl, cap):
    return {
        "task_id": "cookbook-blast-real-%s-%s" % (name, RUN),
        "command": command(script),
        "container": image,
        "resources": {"cpu": THREADS, "memory_gib": 32, "disk_gib": 40,
                      "architecture": "arm64", "families": ["c8g"]},
        "inputs": [{"source": "%s/%s" % (IN, script), "destination": "/tmp/%s" % script}] + inputs,
        "outputs": [{"source": "/tmp/out/", "destination": "%s/%s/" % (OUT, name)}],
        "lifecycle": {"ttl": ttl, "on_complete": "terminate", "cost_limit": cap},
    }


blast = spec("blast", "run.sh", BLAST, [
    {"source": "%s/swissprot-2026-10-10-01-05-01/" % IN, "destination": "/tmp/db/"},
    {"source": "%s/inputs.sha256" % IN, "destination": "/tmp/inputs.sha256"},
], "4h", 1.5)   # Graviton4 measured 3,779 s of blastp, $0.69 billed
dmnd = spec("diamond", "diamond.sh", DIAMOND, [
    {"source": "%s/blast/%s" % (OUT, f), "destination": "/tmp/%s" % d}
    for f, d in [("queries.fa", "queries.fa"), ("swissprot.fa.gz", "swissprot.fa.gz"),
                 ("blast_self.tsv", "blast_self.tsv"), ("check.txt", "blast-check.txt")]
], "2h", 1.0)
for fn, s in [("01-blast.task.json", blast), ("02-diamond.task.json", dmnd)]:
    with open(os.path.join(D, fn), "w") as fh:
        json.dump(s, fh, indent=2)
        fh.write("\n")
    print(fn)
