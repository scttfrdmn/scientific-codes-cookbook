#!/usr/bin/env python3
"""Write measurements/kallisto-real's two arm64 specs, pinning the staged scripts by their current
sha256. x86/AMD come from scripts/spec_arch.py (02 only), never from a second copy. 02 quantifies on
every chip; 03 checks all three chips' outputs against salmon-real and rsem-real, on Graviton4.
    mkspec.py <run label, e.g. r1>"""
import hashlib
import json
import os
import sys

RUN = sys.argv[1]
D = os.path.dirname(os.path.abspath(__file__))
B = "s3://${COOKBOOK_BUCKET}"
IN = "%s/inputs/kallisto-real" % B
# the recipes' own pins (recipes/kallisto, recipes/rsem frontmatter); RSEM's image is used for its R
KALLISTO = "quay.io/aarchbio/kallisto@sha256:b8f0e24c8a014b202f7ef9eeafffd4a6fa1cd27ac4214850cd65b0b1f684f18d"
RSEM = "quay.io/aarchbio/rsem@sha256:76f449f796624084750956f93d40cc50e59184990e7fd82e36fed2837dd40847"


def sha(f):
    return hashlib.sha256(open(os.path.join(D, f), "rb").read()).hexdigest()


def command(files, run):
    pins = "".join("%s  %s\\n" % (sha(s), s) for s in files)
    return ["bash", "-c",
            "set +e\ncd /tmp\nprintf '%s' > scripts.sha256\n"
            "sha256sum -c scripts.sha256 || { echo 'FAIL: staged script does not match its pin'; exit 1; }\n"
            "%s\n" % (pins, run)]


def o(src, dst):
    return {"source": src, "destination": dst}


def spec(name, files, run, image, family, cpu, mem, inputs, outputs, ttl, cap):
    return {
        "task_id": "cookbook-kallisto-real-%s-%s" % (name, RUN),
        "command": command(files, run),
        "container": image,
        "resources": {"cpu": cpu, "memory_gib": mem, "architecture": "arm64", "families": [family]},
        "inputs": [o("%s/%s" % (IN, s), "/tmp/%s" % s) for s in files] + inputs,
        "outputs": outputs,
        "lifecycle": {"ttl": ttl, "on_complete": "terminate", "cost_limit": cap},
    }


quant = spec("quant", ["quant.sh", "reads.md5"], "bash /tmp/quant.sh 16", KALLISTO, "c8g", 16, 30, [
    o("%s/inputs/kallisto/kallisto.idx" % B, "/tmp/kallisto.idx")] + [
    o("%s/inputs/salmon-real/ERR188026_%d.fastq.gz" % (B, i), "/tmp/ERR188026_%d.fastq.gz" % i) for i in (1, 2)],
    [o("/tmp/out/", "%s/runs/kallisto-real/%s/quant/" % (B, RUN))], "1h", 0.5)
SIB = [("rsem-real/salmon.quant.sf", "salmon.quant.sf"), ("rsem-real/prepare/gene_map.tsv", "gene_map.tsv")]
ins = [o("%s/inputs/%s" % (B, s), "/tmp/%s" % d) for s, d in SIB]
ins += [o("%s/runs/rsem-real/r2/quant/ERR188026.%s.results" % (B, f), "/tmp/ERR188026.%s.results" % f)
        for f in ("isoforms", "genes")]
for c, p in [("arm64", "runs"), ("x86_64", "runs-x86_64"), ("amd", "runs-amd")]:
    q = "%s/%s/kallisto-real/%s/quant" % (B, p, RUN)
    ins.append(o("%s/pins-check.txt" % q, "/tmp/c/%s/pins-check.txt" % c))
    ins += [o("%s/%s/%s" % (q, r, f), "/tmp/c/%s/%s/%s" % (c, r, f))
            for r in ("A", "B") for f in ("abundance.tsv", "run_info.json")]
check = spec("check", ["check.R", "siblings.sha256"],
             "sha256sum -c siblings.sha256 || { echo 'FAIL: a sibling output does not match its pin'; exit 1; }\n"
             "export PATH=/usr/local/bin:/opt/conda/bin:$PATH HOME=/tmp TMPDIR=/tmp\n"
             "Rscript /tmp/check.R /tmp/c /tmp/gene_map.tsv /tmp/salmon.quant.sf "
             "/tmp/ERR188026.isoforms.results /tmp/ERR188026.genes.results",
             RSEM, "m8g", 2, 8, ins, [o("/tmp/c/check.txt", "%s/runs/kallisto-real/%s/check.txt" % (B, RUN))], "1h", 0.1)
for fn, s in [("02-quant.task.json", quant), ("03-check.task.json", check)]:
    with open(os.path.join(D, fn), "w") as fh:
        json.dump(s, fh, indent=2)
        fh.write("\n")
    print(fn)
