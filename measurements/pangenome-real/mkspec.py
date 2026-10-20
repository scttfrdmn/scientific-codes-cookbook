#!/usr/bin/env python3
"""Write measurements/pangenome-real's arm64 specs, pinning the staged scripts by their current
sha256. Intel and AMD come from scripts/spec_arch.py, never from a second copy.
    python3 mkspec.py

00-prep     (once, arm64) samtools image: HG002 chr20 reads -> paired FASTQ + novoalign's placements.
01-giraffe  vg image, every chip: index the HPRC chr20 graph, map with Giraffe, check, surject, compare.
"""
import hashlib
import json
import os

D = os.path.dirname(os.path.abspath(__file__))
B = "s3://${COOKBOOK_BUCKET}"
IN = B + "/inputs/pangenome-real"
OUT = B + "/runs/pangenome-real"
# samtools: the pin measurements/phasing-real cut these reads with. vg: recipes/pangenome's own.
SAMTOOLS = "quay.io/aarchbio/samtools@sha256:1191739637fb6f46ef97c02b28f693b25ca3ca61f90e1337f349b7b7cc0be4f7"
VG = "quay.io/aarchbio/vg@sha256:0c9767627faac4a98586fe99ffebb4e701903deb8cd5983ff12746344e8b0ccf"
THREADS = 16


def sha(f):
    return hashlib.sha256(open(os.path.join(D, f), "rb").read()).hexdigest()


def pinned(files, run):
    # spawn runs task commands under bash -e; `set -uo pipefail` does not clear it (spawn#707).
    return ["bash", "-c",
            "set +e\nset -uo pipefail\nexport PATH=/opt/conda/bin:/usr/local/bin:$PATH HOME=/tmp TMPDIR=/var/tmp\n"
            "cd /tmp\nmkdir -p /tmp/out\n{ %s; } > scripts.sha256\n"
            "sha256sum -c scripts.sha256 || { echo 'FAIL: a staged script does not match its pin'; exit 1; }\n"
            "%s\n" % ("; ".join("printf '%%s  %%s\\n' %s %s" % (sha(f), f) for f in files), run)]


def ins(pairs):
    return [{"source": s, "destination": "/tmp/" + d} for s, d in pairs]


prep = {
    "task_id": "cookbook-pangenome-real-prep",
    "container": SAMTOOLS,
    "command": pinned(["prep.sh", "lib.sh"], "bash /tmp/prep.sh 8"),
    # tmpfs (16 GiB) holds the 2.7 GB BAM, the 1.6 GB graph and the ~6 GB of FASTQ written back
    "resources": {"cpu": 8, "memory_gib": 32, "disk_gib": 60, "architecture": "arm64", "families": ["m8g"]},
    "inputs": ins([(IN + "/prep.sh", "prep.sh"), (IN + "/lib.sh", "lib.sh"),
                   (B + "/inputs/phasing-real/prep/ilmn.chr20.bam", "ilmn.chr20.bam"),
                   (IN + "/hprc/chr20.d9.vg", "chr20.d9.vg")]),
    "outputs": [{"source": "/tmp/out/", "destination": IN + "/prep/"}],
    "lifecycle": {"ttl": "2h", "on_complete": "terminate", "cost_limit": 0.6},
}
# Sized from a 200k-pair probe on r8g.4xlarge: autoindex peaked at 3.2 GiB, giraffe at 1.6 GiB. The
# box is set by the work files on disk (a ~9 GB GAF) and by BusyBox sort, which holds the whole GAF
# in memory to hash it on x86; 64 GiB covers that. TTL is a hang backstop; cost_limit is the guard.
giraffe = {
    "task_id": "cookbook-pangenome-real-giraffe",
    "container": VG,
    "command": pinned(["giraffe.sh", "lib.sh", "pins.sha256"], "bash /tmp/giraffe.sh %d" % THREADS),
    "resources": {"cpu": THREADS, "memory_gib": 64, "disk_gib": 100, "architecture": "arm64", "families": ["m8g"]},
    "inputs": ins([(IN + "/giraffe.sh", "giraffe.sh"), (IN + "/lib.sh", "lib.sh"), (IN + "/pins.sha256", "pins.sha256"),
                   (IN + "/hprc/chr20.d9.vg", "chr20.d9.vg"),
                   (IN + "/prep/r1.fq.gz", "r1.fq.gz"), (IN + "/prep/r2.fq.gz", "r2.fq.gz"),
                   (IN + "/prep/linear.tsv.gz", "linear.tsv.gz"),
                   (B + "/inputs/bwa-samtools/chr20.fa", "chr20.fa")]),
    "outputs": [{"source": "/tmp/out/", "destination": OUT + "/giraffe/"}],
    "lifecycle": {"ttl": "4h", "on_complete": "terminate", "cost_limit": 1.5},
}

for name, s in {"00-prep": prep, "01-giraffe": giraffe}.items():
    with open(os.path.join(D, name + ".task.json"), "w") as fh:
        json.dump(s, fh, indent=2)
        fh.write("\n")
