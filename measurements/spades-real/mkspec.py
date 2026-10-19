#!/usr/bin/env python3
"""Write the three spades-real TaskSpecs (arm64; x86 and AMD come from scripts/spec_arch.py).

    python3 mkspec.py <outdir>

00-prep turns the RODA .sra run into paired FASTQ.gz once and fetches the closed reference
(staging, arm64 only). 01-assemble is the workload, run on every chip from the same staged bytes.
02-evaluate scores all three chips' assemblies against the reference in one QUAST run (arm64).
"""
import json
import os
import sys

out = sys.argv[1]
# Ecoli_MG1655_NN, PRJDB8030 (a library-prep comparison on wild-type isolates): MiSeq 2x300.
ACC, PAIRS, BASES = "DRR171213", 760683, 450083225
REF = "GCF_000005845.2.fna.gz"
THREADS, MEM_CAP = 8, 14
B = "s3://${COOKBOOK_BUCKET}/"
IN = B + "inputs/spades-real/"
SRA_TOOLS = "quay.io/aarchbio/sra-tools@sha256:7145cf2f6f2e9265b7ace204d86b6ed1cbbd0b8b3ef31cbadf32bfa271229afd"
SPADES = "quay.io/aarchbio/spades@sha256:f8b7ad9acda742d695be9176c1fec0e9a33579a6a19294d3d2a3516ade3de81c"
QUAST = "quay.io/aarchbio/quast@sha256:54122e645394aa741656c54ecdde8737b2ad8cc0ef6ead72392c1be8248ae692"
CHIPS = {"arm64": "runs", "x86_64": "runs-x86_64", "amd": "runs-amd"}

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


prep = {
    "task_id": "cookbook-spades-real-prep",
    "container": SRA_TOOLS,
    # the .sra pin is recorded by this task's first run, so it is checked when present
    "command": ["bash", "-c", pinned(["prep.sh", ACC + ".sra"], 1)
                + "bash prep.sh %s %d %d 8 || exit 1\n" % (ACC, PAIRS, BASES)],
    "resources": {"cpu": 8, "memory_gib": 16, "architecture": "arm64", "families": ["c8g"], "disk_gib": 30},
    "inputs": [io(IN + ACC + ".sra", ACC + ".sra"), io(IN + "prep.sh", "prep.sh"),
               io(IN + "pins.sha256", "pins.sha256")],
    "outputs": [{"source": "/tmp/out/", "destination": IN + "prep/"}],
    "lifecycle": {"ttl": "1h", "on_complete": "terminate", "cost_limit": 0.3},
}

files = ["assemble.sh", ACC + "_1.fastq.gz", ACC + "_2.fastq.gz"]
assemble = {
    "task_id": "cookbook-spades-real-assemble",
    "container": SPADES,
    "command": ["bash", "-c", pinned(files, len(files))
                + "bash assemble.sh %s %d %d\n" % (ACC, THREADS, MEM_CAP)],
    "resources": {"cpu": THREADS, "memory_gib": 16, "architecture": "arm64", "families": ["c8g"], "disk_gib": 60},
    "inputs": [io(IN + "prep/%s_%d.fastq.gz" % (ACC, m), "%s_%d.fastq.gz" % (ACC, m)) for m in (1, 2)]
              + [io(IN + "assemble.sh", "assemble.sh"), io(IN + "pins.sha256", "pins.sha256")],
    "outputs": [{"source": "/tmp/out/", "destination": B + "runs/spades-real/%s/" % ACC}],
    "lifecycle": {"ttl": "90m", "on_complete": "terminate", "cost_limit": 0.4},
}

efiles = ["evaluate.sh", REF, "pairs.txt"]
einputs = [io(IN + "prep/" + REF, REF), io(IN + "prep/pairs.txt", "pairs.txt"),
           io(IN + "evaluate.sh", "evaluate.sh"), io(IN + "pins.sha256", "pins.sha256")]
for chip, p in CHIPS.items():
    for f in ("contigs.fasta", "spades.log"):
        einputs.append(io(B + "%s/spades-real/%s/%s" % (p, ACC, f), "%s.%s" % (chip, f)))
evaluate = {
    "task_id": "cookbook-spades-real-evaluate",
    "container": QUAST,
    "command": ["bash", "-c", pinned(efiles, len(efiles)) + "bash evaluate.sh %s 4 %s\n" % (REF, " ".join(CHIPS))],
    "resources": {"cpu": 4, "memory_gib": 8, "architecture": "arm64", "families": ["c8g"]},
    "inputs": einputs,
    "outputs": [{"source": "/tmp/out/", "destination": B + "runs/spades-real/evaluate/"}],
    "lifecycle": {"ttl": "30m", "on_complete": "terminate", "cost_limit": 0.1},
}

for name, s in (("00-prep", prep), ("01-assemble", assemble), ("02-evaluate", evaluate)):
    with open(os.path.join(out, name + ".task.json"), "w") as fh:
        json.dump(s, fh, indent=2)
        fh.write("\n")
