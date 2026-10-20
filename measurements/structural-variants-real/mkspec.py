#!/usr/bin/env python3
"""Write measurements/structural-variants-real's arm64 specs, pinning the staged scripts by their
current sha256. x86 and AMD come from scripts/spec_arch.py, never from a second copy.
    mkspec.py"""
import hashlib
import json
import os

D = os.path.dirname(os.path.abspath(__file__))
IN = "s3://${COOKBOOK_BUCKET}/inputs/structural-variants-real"
OUT = "s3://${COOKBOOK_BUCKET}/runs/structural-variants-real"
# The recipe's own pins (recipes/structural-variants/README.md frontmatter).
SAMTOOLS = "quay.io/aarchbio/samtools@sha256:1191739637fb6f46ef97c02b28f693b25ca3ca61f90e1337f349b7b7cc0be4f7"
MANTA = "quay.io/aarchbio/manta@sha256:0cae7f334d4a0929f985dfdbf24801de50c421e29fc529d71ba35f342ea1e0d5"
DELLY = "quay.io/aarchbio/delly@sha256:c1d0492cb7d52d56f0a01e790dd1434ba9ddbb47cc5c95cf90dc12c20d5aaeb5"
# Scoring only, arm64 only: aarch.bio truvari 5.5.0 (cosign-verified).
TRUVARI = "quay.io/aarchbio/truvari@sha256:de287df70065b73658d75a52cc24e595e38df4f32cff364aca31121e5754de82"
THREADS = 32
# 32 vCPU / 256 GiB: the 30x BAM (~59 GiB) and the reference sit in the half-RAM tmpfs; manta's pyflow
# is told the rest (-g) rather than left to size itself from MemTotal, which counts the tmpfs as free.
BOX = {"cpu": THREADS, "memory_gib": 256, "disk_gib": 120, "architecture": "arm64", "families": ["r8g"]}
MANTA_GB = 150
TRUTH = ["HG002_SVs_Tier1_v0.6.vcf.gz", "HG002_SVs_Tier1_v0.6.vcf.gz.tbi", "HG002_SVs_Tier1_v0.6.bed"]
DERIVED = ["HG002.hs37d5.30x.bam", "HG002.hs37d5.30x.bam.bai", "hs37d5.fa", "hs37d5.fa.fai"]
RUNS = {"arm64": "runs", "x86_64": "runs-x86_64", "amd": "runs-amd"}


def sha(f):
    return hashlib.sha256(open(os.path.join(D, f), "rb").read()).hexdigest()


def pinned(files, run):
    # spawn runs task commands under bash -e; `set -uo pipefail` does not clear it (spawn#707).
    return ["bash", "-c",
            "set +e\nset -uo pipefail\nexport PATH=/usr/local/bin:/opt/conda/bin:$PATH HOME=/tmp TMPDIR=/tmp\n"
            "cd /tmp\nmkdir -p /tmp/out\n{ %s; } > scripts.sha256\n"
            "sha256sum -c scripts.sha256 || { echo 'FAIL: a staged script does not match its pin'; exit 1; }\n"
            "%s\n" % ("; ".join("printf '%%s  %%s\\n' %s %s" % (sha(f), f) for f in files), run)]


def ins(files, prefix=IN):
    return [{"source": "%s/%s" % (prefix, f), "destination": "/tmp/%s" % f} for f in files]


prep = {
    "task_id": "cookbook-structural-variants-real-prep",
    "command": pinned(["prep.sh"], "bash /tmp/prep.sh %d" % THREADS),
    "container": SAMTOOLS,
    # tmpfs (128 GiB) holds the 117 GiB 60x BAM; the 30x BAM and the reference are written to disk.
    "resources": {"cpu": THREADS, "memory_gib": 256, "disk_gib": 200, "architecture": "arm64", "families": ["r8g"]},
    "inputs": ins(["prep.sh", "human.hg19.excl.tsv"])
    + ins(["HG002.hs37d5.60x.1.bam", "HG002.hs37d5.60x.1.bam.bai"] + TRUTH, IN + "/giab")
    + ins(["hs37d5.fa.gz", "hs37d5.fa.gz.fai"], IN + "/ref"),
    "outputs": [{"source": "/var/tmp/derived/", "destination": IN + "/derived/"},
                {"source": "/tmp/out/", "destination": IN + "/derived/prep/"}],
    "lifecycle": {"ttl": "3h", "on_complete": "terminate", "cost_limit": 2.5},
}
manta = {
    "task_id": "cookbook-structural-variants-real-manta",
    "command": pinned(["manta.sh", "pins.sha256"], "bash /tmp/manta.sh %d %d" % (THREADS, MANTA_GB)),
    "container": MANTA,
    "resources": BOX,
    "inputs": ins(["manta.sh", "pins.sha256"]) + ins(DERIVED, IN + "/derived"),
    "outputs": [{"source": "/tmp/out/", "destination": OUT + "/manta/"}],
    "lifecycle": {"ttl": "5h", "on_complete": "terminate", "cost_limit": 4.0},
}
delly = {
    "task_id": "cookbook-structural-variants-real-delly",
    "command": pinned(["delly.sh", "pins.sha256"], "bash /tmp/delly.sh %d" % THREADS),
    "container": DELLY,
    "resources": BOX,
    "inputs": ins(["delly.sh", "pins.sha256", "human.hg19.excl.tsv"]) + ins(DERIVED, IN + "/derived"),
    "outputs": [{"source": "/tmp/out/", "destination": OUT + "/delly/"}],
    "lifecycle": {"ttl": "8h", "on_complete": "terminate", "cost_limit": 6.0},
}


def score(chips, name):
    s = "s3://${COOKBOOK_BUCKET}/%s/structural-variants-real"
    return {
        "task_id": "cookbook-structural-variants-real-score",
        "command": pinned(["score.py", "pins.sha256"],
                          "grep -E '  HG002_SVs_Tier1_v0\\.6\\.' pins.sha256 > truth.sha256 && sha256sum -c truth.sha256 "
                          "|| { echo 'FAIL: the benchmark does not match its pin'; exit 1; }\n"
                          "python3 -u /tmp/score.py %s 2>&1 | tee /tmp/out/score.log\n"
                          "exit ${PIPESTATUS[0]}" % " ".join(chips)),
        "container": TRUVARI,
        "resources": {"cpu": 4, "memory_gib": 16, "disk_gib": 40, "architecture": "arm64", "families": ["m8g"]},
        "inputs": ins(["score.py", "pins.sha256"]) + ins(TRUTH, IN + "/giab")
        + [{"source": "%s/%s/%s" % (s % RUNS[c], t, f), "destination": "/tmp/%s.%s" % (c, f)}
           for c in chips for t, fs in (("manta", ["diploidSV.vcf.gz", "diploidSV.vcf.gz.tbi"]),
                                         ("delly", ["delly.bcf", "delly.bcf.csi"])) for f in fs],
        "outputs": [{"source": "/tmp/out/", "destination": OUT + "/%s/" % name}],
        "lifecycle": {"ttl": "1h", "on_complete": "terminate", "cost_limit": 0.3},
    }


specs = [("00-prep.task.json", prep), ("01-manta.task.json", manta), ("02-delly.task.json", delly),
         ("03-score.task.json", score(["arm64", "x86_64", "amd"], "score")),
         ("03-score-arm64.task.json", score(["arm64"], "score-arm64"))]
for fn, s in specs:
    with open(os.path.join(D, fn), "w") as fh:
        json.dump(s, fh, indent=2)
        fh.write("\n")
    print(fn)
