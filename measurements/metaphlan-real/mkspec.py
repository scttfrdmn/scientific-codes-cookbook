#!/usr/bin/env python3
"""Write the two metaphlan-real TaskSpecs (arm64; x86 and AMD come from scripts/spec_arch.py).

    python3 mkspec.py <outdir>

00-prep turns the RODA .sra runs into paired FASTQ.gz once (staging, arm64 only). 01-profile is
the workload, run on every chip from the same staged bytes.
"""
import json
import os
import sys

out = sys.argv[1]
# Nicholls et al. 2019 (PRJEB29504): Illumina shotgun of ZymoBIOMICS D6300 (even) and D6310 (log).
# (accession, ENA read_count = spots, ENA base_count, truth table)
SAMPLES = [("ERR2984773", 8785731, 2653290762, "zymo_d6300.tsv"),
           ("ERR2935805", 47832553, 9662175706, "zymo_d6310.tsv")]
B = "s3://${COOKBOOK_BUCKET}/"
IN = B + "inputs/metaphlan-real/"
DB = B + "inputs/metaphlan/"
IDX = "mpa_vJun23_CHOCOPhlAnSGB_202403"
SRA_TOOLS = "quay.io/aarchbio/sra-tools@sha256:7145cf2f6f2e9265b7ace204d86b6ed1cbbd0b8b3ef31cbadf32bfa271229afd"
METAPHLAN = "quay.io/aarchbio/metaphlan@sha256:7149c1bb1e23d01d273d56cc86502e36a2b976c598175327f6a32c42abfbbe48"

PINS = r"""set +e
# spawn runs task commands under bash -e; `set -uo pipefail` does not clear it (spawn#707).
cd /tmp
grep -E ' (%s)$' pins.sha256 > pins.sel
[ "$(wc -l < pins.sel)" -ge %d ] && sha256sum -c pins.sel || { echo "FAIL: a staged input does not match its pin"; exit 1; }
"""


def pinned(names, at_least):
    return PINS % ("|".join(n.replace(".", r"\.") for n in names), at_least)


def io(src, name):
    return {"source": src, "destination": "/tmp/" + name}


ACCS = [x[0] for x in SAMPLES]
prep = {
    "task_id": "cookbook-metaphlan-real-prep",
    "container": SRA_TOOLS,
    # the .sra pins are recorded by this task's first run, so they are checked when present
    "command": ["bash", "-c", pinned(["prep.sh"] + [a + ".sra" for a in ACCS], 1)
                + "".join("bash prep.sh %s %d %d 8 || exit 1\n" % (a, n, b) for a, n, b, _ in SAMPLES)],
    "resources": {"cpu": 8, "memory_gib": 64, "architecture": "arm64", "families": ["r8g"], "disk_gib": 80},
    "inputs": [io(IN + a + ".sra", a + ".sra") for a in ACCS]
              + [io(IN + "prep.sh", "prep.sh"), io(IN + "pins.sha256", "pins.sha256")],
    "outputs": [{"source": "/tmp/out/", "destination": IN + "prep/"}],
    "lifecycle": {"ttl": "3h", "on_complete": "terminate", "cost_limit": 1.0},
}

files = [IDX + ".tar", IDX + "_bt2.tar", "zymo_d6300.tsv", "zymo_d6310.tsv", "profile.sh", "score.py"]
reads = []
for a in ACCS:
    files += [a + "_1.fastq.gz", a + "_2.fastq.gz", a + ".reads_ge70"]
    reads += [io(IN + "prep/%s/%s_%d.fastq.gz" % (a, a, m), "%s_%d.fastq.gz" % (a, m)) for m in (1, 2)]
    reads.append(io(IN + "prep/%s/reads_ge70.txt" % a, a + ".reads_ge70"))
profile = {
    "task_id": "cookbook-metaphlan-real-profile",
    "container": METAPHLAN,
    "command": ["bash", "-c", pinned(files, len(files))
                + "bash profile.sh 16 %s\n" % " ".join("%s:%s" % (a, t) for a, _, _, t in SAMPLES)],
    "resources": {"cpu": 16, "memory_gib": 124, "architecture": "arm64", "families": ["r8g"]},
    "inputs": [io(DB + IDX + ".tar", IDX + ".tar"), io(DB + IDX + "_bt2.tar", IDX + "_bt2.tar"),
               io(DB + "zymo_d6300.tsv", "zymo_d6300.tsv"), io(IN + "zymo_d6310.tsv", "zymo_d6310.tsv")]
              + reads
              + [io(IN + "profile.sh", "profile.sh"), io(IN + "score.py", "score.py"),
                 io(IN + "pins.sha256", "pins.sha256")],
    "outputs": [{"source": "/tmp/out/", "destination": B + "runs/metaphlan-real/zymo/"}],
    "lifecycle": {"ttl": "4h", "on_complete": "terminate", "cost_limit": 2.0},
}

for name, s in (("00-prep", prep), ("01-profile", profile)):
    with open(os.path.join(out, name + ".task.json"), "w") as fh:
        json.dump(s, fh, indent=2)
        fh.write("\n")
