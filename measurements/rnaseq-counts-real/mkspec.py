#!/usr/bin/env python3
"""Write measurements/rnaseq-counts-real's three arm64 specs, pinning the staged scripts by their
current sha256. x86/AMD come from scripts/spec_arch.py, never from a second copy. Task 0 (STAR)
builds the shared input once, on Graviton4; tasks 1 and 2 run on every chip.
    mkspec.py <run label, e.g. r1>"""
import hashlib
import json
import os
import sys

RUN = sys.argv[1]
D = os.path.dirname(os.path.abspath(__file__))
IN = "s3://${COOKBOOK_BUCKET}/inputs/rnaseq-counts-real"
OUT = "s3://${COOKBOOK_BUCKET}/runs/rnaseq-counts-real/%s" % RUN
STAR = "quay.io/aarchbio/star@sha256:90331f64bd73eadaefbebc8d4aecfed3ebed1a7e40b761041acbbcf53b3e13ae"
SUBREAD = "quay.io/aarchbio/subread@sha256:0c2f26ea5f115a11e0178d6f9680001015bddd1fd1339e8f706e68bcd8fe306a"
HTSEQ = "quay.io/aarchbio/htseq@sha256:a578d1c21dffe43dfc0075ade4f1862881ec825eab61b67197993bb609aa9811"


def sha(f):
    return hashlib.sha256(open(os.path.join(D, f), "rb").read()).hexdigest()


def command(scripts, run):
    pins = "".join("%s  %s\\n" % (sha(s), s) for s in scripts)
    return ["bash", "-c",
            "set +e\ncd /tmp\nprintf '%s' > scripts.sha256\n"
            "sha256sum -c scripts.sha256 || { echo 'FAIL: staged script does not match its pin'; exit 1; }\n"
            "%s\n" % (pins, run)]


def spec(name, scripts, run, image, family, cpu, mem, inputs, out, ttl, cap):
    return {
        "task_id": "cookbook-rnaseq-counts-real-%s-%s" % (name, RUN),
        "command": command(scripts, run),
        "container": image,
        "resources": {"cpu": cpu, "memory_gib": mem, "disk_gib": 40,
                      "architecture": "arm64", "families": [family]},
        "inputs": [{"source": "%s/%s" % (IN, s), "destination": "/tmp/%s" % s} for s in scripts] + inputs,
        "outputs": [{"source": "/tmp/out/", "destination": out}],
        "lifecycle": {"ttl": ttl, "on_complete": "terminate", "cost_limit": cap},
    }


GTF = {"source": "s3://${COOKBOOK_BUCKET}/inputs/star-real/GRCh38.116.gtf.gz",
       "destination": "/tmp/GRCh38.116.gtf.gz"}
COUNT_IN = [
    {"source": "%s/star/Aligned.out.bam" % IN, "destination": "/tmp/reads.bam"},
    GTF,
    {"source": "%s/star/Log.final.out" % IN, "destination": "/tmp/star.Log.final.out"},
    {"source": "%s/star/ReadsPerGene.out.tab" % IN, "destination": "/tmp/ReadsPerGene.out.tab"},
    {"source": "%s/inputs.sha256" % IN, "destination": "/tmp/inputs.sha256"},
]
# The index (28.6 GiB) and the reads stage into tmpfs (half of 128 GiB); STAR itself needs ~32 GiB.
align = spec("align", ["align.sh"], "bash /tmp/align.sh 16", STAR, "r8g", 16, 128, [
    {"source": "s3://${COOKBOOK_BUCKET}/inputs/star-index-GRCh38-116/", "destination": "/tmp/idx/"},
    {"source": "s3://${COOKBOOK_BUCKET}/inputs/salmon-real/ERR188026_1.fastq.gz", "destination": "/tmp/ERR188026_1.fastq.gz"},
    {"source": "s3://${COOKBOOK_BUCKET}/inputs/salmon-real/ERR188026_2.fastq.gz", "destination": "/tmp/ERR188026_2.fastq.gz"},
    GTF,
], "%s/star/" % IN, "90m", 2.0)
fc = spec("featurecounts", ["featurecounts.sh"], "bash /tmp/featurecounts.sh 8", SUBREAD, "c8g", 8, 16,
          COUNT_IN, "%s/featurecounts/" % OUT, "90m", 0.6)
hts = spec("htseq", ["htseq.sh", "compare.py"], "bash /tmp/htseq.sh", HTSEQ, "r8g", 2, 16,
           COUNT_IN + [{"source": "%s/featurecounts/%s" % (OUT, f), "destination": "/tmp/%s" % f}
                       for f in ("fc_counts.tsv", "fc.summary")],
           "%s/htseq/" % OUT, "5h", 1.0)
for fn, s in [("00-star-align.task.json", align), ("01-featurecounts.task.json", fc), ("02-htseq.task.json", hts)]:
    with open(os.path.join(D, fn), "w") as fh:
        json.dump(s, fh, indent=2)
        fh.write("\n")
    print(fn)
