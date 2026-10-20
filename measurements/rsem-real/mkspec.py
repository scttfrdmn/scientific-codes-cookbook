#!/usr/bin/env python3
"""Write measurements/rsem-real's four arm64 specs, pinning the staged scripts by their current
sha256. x86/AMD come from scripts/spec_arch.py (02 only), never from a second copy. Tasks 00
(RSEM reference) and 01 (bowtie2) build the shared inputs once, on Graviton4; 02 (RSEM's EM and
the checks) runs on every chip; 03 compares the three chips' outputs.
    mkspec.py <run label, e.g. r1>   (00 and 01 keep their r1 task ids: they ran once)"""
import hashlib
import json
import os
import sys

RUN = sys.argv[1]
D = os.path.dirname(os.path.abspath(__file__))
B = "s3://${COOKBOOK_BUCKET}"
IN = "%s/inputs/rsem-real" % B
OUT = "%s/runs/rsem-real/%s" % (B, RUN)
# the recipe's own pins (recipes/rsem/README.md frontmatter)
RSEM = "quay.io/aarchbio/rsem@sha256:76f449f796624084750956f93d40cc50e59184990e7fd82e36fed2837dd40847"
BOWTIE2 = "quay.io/aarchbio/bowtie2@sha256:a6807f0611a1c276235f47d175471ebbaec863aa751a0c3771c324be57d8fc59"
# rsem-parse-alignments reads .grp/.ti and rsem-run-em .seq (parseIt.cpp, EM.cpp); the FASTAs are for aligners
REF = ["ens116.grp", "ens116.ti", "ens116.seq", "ens116.idx.fa", "ens116.n2g.idx.fa",
       "ens116.transcripts.fa"]


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
        "task_id": "cookbook-rsem-real-%s-%s" % (name, label or RUN),
        "command": command(scripts, run),
        "container": image,
        "resources": {"cpu": cpu, "memory_gib": mem, "disk_gib": disk,
                      "architecture": "arm64", "families": [family]},
        "inputs": [o("%s/%s" % (IN, s), "/tmp/%s" % s) for s in scripts] + inputs,
        "outputs": outputs,
        "lifecycle": {"ttl": ttl, "on_complete": "terminate", "cost_limit": cap},
    }


READS = [o("%s/inputs/salmon-real/ERR188026_%d.fastq.gz" % (B, i), "/tmp/ERR188026_%d.fastq.gz" % i) for i in (1, 2)]
prep = spec("prepare", ["prepare.sh"], "bash /tmp/prepare.sh", RSEM, "m8g", 4, 16, [
    o("%s/inputs/salmon-real/ensembl116_cdna.fa.gz" % B, "/tmp/ensembl116_cdna.fa.gz"),
    o("%s/runs/salmon/r1/quant-c8g-16t.sf" % B, "/tmp/salmon.quant.sf"),
], [o("/tmp/out/", "%s/prepare/" % IN), o("/tmp/ref/", "%s/ref/" % IN)], "1h", 0.3, label="r1")
align = spec("align", ["align.sh", "timed.py"], "bash /tmp/align.sh 32", BOWTIE2, "m8g", 32, 128, READS + [
    o("%s/ref/ens116.transcripts.fa" % IN, "/tmp/ens116.transcripts.fa"),
    o("%s/prepare/ref.sha256" % IN, "/tmp/ref.sha256"),
], [o("/tmp/out/", "%s/align/" % IN), o("/tmp/sam/", "%s/sam/" % IN)], "5h", 4.0, disk=60, label="r1")
quant = spec("quant", ["quant.sh", "check.R"], "bash /tmp/quant.sh 16", RSEM, "m8g", 16, 64,
             [o("%s/ref/%s" % (IN, f), "/tmp/%s" % f) for f in REF[:3]] + [
                 o("%s/sam/aln.sam.gz" % IN, "/tmp/aln.sam.gz"),
                 o("%s/prepare/gene_map.tsv" % IN, "/tmp/gene_map.tsv"),
                 o("%s/salmon.quant.sf" % IN, "/tmp/salmon.quant.sf"),
                 o("%s/inputs.sha256" % IN, "/tmp/inputs.sha256")],
             [o("/tmp/out/", "%s/quant/" % OUT)], "4h", 2.0, disk=80)
CHIPS = [("arm64", "runs"), ("x86_64", "runs-x86_64"), ("amd", "runs-amd")]
cross = spec("crosschip", ["crosschip.R"], "Rscript /tmp/crosschip.R /tmp/c", RSEM, "m8g", 2, 8,
             [o("%s/%s/rsem-real/%s/quant/ERR188026.%s" % (B, p, RUN, f), "/tmp/c/%s/ERR188026.%s" % (c, f))
              for c, p in CHIPS for f in ("isoforms.results", "genes.results", "cnt", "theta")],
             [o("/tmp/c/crosschip.txt", "%s/crosschip.txt" % OUT)], "1h", 0.1)
for fn, s in [("00-prepare.task.json", prep), ("01-bowtie2-align.task.json", align),
              ("02-quant.task.json", quant), ("03-crosschip.task.json", cross)]:
    with open(os.path.join(D, fn), "w") as fh:
        json.dump(s, fh, indent=2)
        fh.write("\n")
    print(fn)
