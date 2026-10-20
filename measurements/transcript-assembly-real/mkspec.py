#!/usr/bin/env python3
"""Write measurements/transcript-assembly-real's four arm64 specs, pinning the staged scripts by
their current sha256. x86/AMD come from scripts/spec_arch.py (02 and 03 only), never from a
second copy. Tasks 00 (hisat2) and 01 (samtools) build the shared alignment once, on Graviton4;
02 (StringTie) and 03 (gffcompare) run on every chip.
    mkspec.py <run label, e.g. r3>   (00 and 01 keep their r1 task ids: they ran once)"""
import hashlib
import json
import os
import sys

RUN = sys.argv[1]
D = os.path.dirname(os.path.abspath(__file__))
IN = "s3://${COOKBOOK_BUCKET}/inputs/transcript-assembly-real"
OUT = "s3://${COOKBOOK_BUCKET}/runs/transcript-assembly-real/%s" % RUN
# the recipe's own pins (recipes/transcript-assembly/README.md frontmatter)
HISAT2 = "quay.io/aarchbio/hisat2@sha256:5a967484d6a941ddaba4b57f88ff892a579e0a18cd8c305b45cf9f49bacf10e8"
SAMTOOLS = "quay.io/aarchbio/samtools@sha256:1191739637fb6f46ef97c02b28f693b25ca3ca61f90e1337f349b7b7cc0be4f7"
STRINGTIE = "quay.io/aarchbio/stringtie@sha256:e0eb6ef15f2c8d329c3b24381b7614b80e97f056dece5fbd2f4ec2f6121b6964"
GFFCOMPARE = "quay.io/aarchbio/gffcompare@sha256:9674f300816f52f289453f514c14ebcc8dd6ab1361b2f29ac1c1b283e66e3547"
GTF = {"source": "s3://${COOKBOOK_BUCKET}/inputs/star-real/GRCh38.116.gtf.gz", "destination": "/tmp/GRCh38.116.gtf.gz"}


def sha(f):
    return hashlib.sha256(open(os.path.join(D, f), "rb").read()).hexdigest()


def command(scripts, run):
    pins = "".join("%s  %s\\n" % (sha(s), s) for s in scripts)
    return ["bash", "-c",
            "set +e\ncd /tmp\nprintf '%s' > scripts.sha256\n"
            "sha256sum -c scripts.sha256 || { echo 'FAIL: staged script does not match its pin'; exit 1; }\n"
            "%s\n" % (pins, run)]


def spec(name, scripts, run, image, family, cpu, mem, inputs, outputs, ttl, cap, disk=40, label=None):
    return {
        "task_id": "cookbook-ta-real-%s-%s" % (name, label or RUN),
        "command": command(scripts, run),
        "container": image,
        "resources": {"cpu": cpu, "memory_gib": mem, "disk_gib": disk,
                      "architecture": "arm64", "families": [family]},
        "inputs": [{"source": "%s/%s" % (IN, s), "destination": "/tmp/%s" % s} for s in scripts] + inputs,
        "outputs": outputs,
        "lifecycle": {"ttl": ttl, "on_complete": "terminate", "cost_limit": cap},
    }


def o(src, dst):
    return {"source": src, "destination": dst}


BAMD = "%s/bam" % IN
align = spec("align", ["align.sh", "timed.py"], "bash /tmp/align.sh 16", HISAT2, "m8g", 16, 64, [
    o("s3://${COOKBOOK_BUCKET}/inputs/salmon-real/ERR188026_1.fastq.gz", "/tmp/ERR188026_1.fastq.gz"),
    o("s3://${COOKBOOK_BUCKET}/inputs/salmon-real/ERR188026_2.fastq.gz", "/tmp/ERR188026_2.fastq.gz"),
    o("s3://${COOKBOOK_BUCKET}/inputs/star-real/GRCh38.primary_assembly.fa.gz", "/tmp/GRCh38.primary_assembly.fa.gz"),
], [o("/tmp/out/", "%s/align/" % IN), o("/tmp/idx/", "%s/hisat2-index-GRCh38-116/" % IN),
    o("/tmp/sam/", "%s/sam/" % IN)], "5h", 3.0, disk=60, label="r1")
sort = spec("sort", ["sort.sh"], "bash /tmp/sort.sh 8", SAMTOOLS, "r8g", 8, 64, [
    o("%s/sam/aln.sam" % IN, "/tmp/aln.sam"), o("%s/align/sam.sha256" % IN, "/tmp/sam.sha256"),
], [o("/tmp/out/", "%s/" % BAMD)], "2h", 1.0, disk=60, label="r1")
ST_IN = [o("%s/%s" % (BAMD, f), "/tmp/%s" % f)
         for f in ("ERR188026.hisat2.bam", "ERR188026.hisat2.bam.bai", "junctions.tsv", "chrom.sizes")]
st = spec("stringtie", ["stringtie.sh", "timed.py", "check_st.py"], "bash /tmp/stringtie.sh 8", STRINGTIE,
          "m8g", 8, 32, ST_IN + [GTF, o("%s/inputs.sha256" % IN, "/tmp/inputs.sha256")],
          [o("/tmp/out/", "%s/stringtie/" % OUT)], "1h", 0.5)
gc = spec("gffcompare", ["gffcompare.sh"], "bash /tmp/gffcompare.sh", GFFCOMPARE, "m8g", 4, 16,
          [GTF, o("%s/ref.sha256" % IN, "/tmp/ref.sha256")] +
          [o("%s/stringtie/%s" % (OUT, f), "/tmp/%s" % f) for f in ("denovo_p8.gtf", "guided_p8.gtf", "check.json",
                                                                 "denovo_p8.chainmatch.ids", "guided_p8.chainmatch.ids")],
          [o("/tmp/out/", "%s/gffcompare/" % OUT)], "1h", 0.4)
for fn, s in [("00-hisat2-align.task.json", align), ("01-sort.task.json", sort),
              ("02-stringtie.task.json", st), ("03-gffcompare.task.json", gc)]:
    with open(os.path.join(D, fn), "w") as fh:
        json.dump(s, fh, indent=2)
        fh.write("\n")
    print(fn)
