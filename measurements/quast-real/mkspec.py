#!/usr/bin/env python3
"""Write the two quast-real TaskSpecs (arm64; x86 and AMD come from scripts/spec_arch.py quast).

    python3 mkspec.py <outdir>

00-prep fetches the nine published assemblies and the reference annotation from NCBI once (arm64,
staging). 01-evaluate is the workload, run on every chip from the same pinned bytes.
"""
import json
import os
import sys

out = sys.argv[1]
THREADS = 8
B = "s3://${COOKBOOK_BUCKET}/"
IN = B + "inputs/quast-real/"
SRA_TOOLS = "quay.io/aarchbio/sra-tools@sha256:7145cf2f6f2e9265b7ace204d86b6ed1cbbd0b8b3ef31cbadf32bfa271229afd"
QUAST = "quay.io/aarchbio/quast@sha256:54122e645394aa741656c54ecdde8737b2ad8cc0ef6ead72392c1be8248ae692"
HERE = os.path.dirname(os.path.abspath(__file__))
LABELS = [l.split("\t")[0] for l in open(os.path.join(HERE, "assemblies.tsv")) if not l.startswith("#")]

# spawn runs task commands under bash -e; `set -uo pipefail` does not clear it (spawn#707).
PINS = r"""set +e
cd /tmp
grep -E ' (%s)$' pins.sha256 > pins.sel
[ "$(wc -l < pins.sel)" -eq %d ] && sha256sum -c pins.sel || { echo "FAIL: a staged input does not match its pin"; exit 1; }
"""


def pinned(names):
    return PINS % ("|".join(n.replace(".", r"\.") for n in names), len(names))


def io(src, name):
    return {"source": src, "destination": "/tmp/" + name}


prep = {
    "task_id": "cookbook-quast-real-prep",
    "container": SRA_TOOLS,
    "command": ["bash", "-c", pinned(["prep.sh", "assemblies.tsv"]) + "bash prep.sh assemblies.tsv || exit 1\n"],
    "resources": {"cpu": 2, "memory_gib": 4, "architecture": "arm64", "families": ["c8g"]},
    "inputs": [io(IN + f, f) for f in ("prep.sh", "assemblies.tsv", "pins.sha256")],
    "outputs": [{"source": "/tmp/out/", "destination": IN + "prep/"}],
    "lifecycle": {"ttl": "30m", "on_complete": "terminate", "cost_limit": 0.05},
}

scripts = ["run.sh", "mkcontrols.py", "check.py", "assemblies.tsv", "spades-real.report.tsv"]
data = ["GCF_000005845.2.fna.gz", "GCF_000005845.2.gff.gz", "spades_contigs.fasta",
        "spades_scaffolds.fasta"] + [l + ".fna.gz" for l in LABELS]
inputs = [io(IN + f, f) for f in scripts + ["pins.sha256"]]
inputs += [io(B + "inputs/spades-real/prep/GCF_000005845.2.fna.gz", "GCF_000005845.2.fna.gz"),
           io(IN + "prep/GCF_000005845.2.gff.gz", "GCF_000005845.2.gff.gz"),
           # spades-real's assembly: byte-identical on all three chips, so each chip's own copy is
           # the same bytes, and the pin proves it
           io(B + "runs/spades-real/DRR171213/contigs.fasta", "spades_contigs.fasta"),
           io(B + "runs/spades-real/DRR171213/scaffolds.fasta", "spades_scaffolds.fasta")]
inputs += [io(IN + "prep/" + l + ".fna.gz", l + ".fna.gz") for l in LABELS]
evaluate = {
    "task_id": "cookbook-quast-real-evaluate",
    "container": QUAST,
    "command": ["bash", "-c", pinned(scripts + data) + "bash run.sh %d assemblies.tsv || exit 1\n" % THREADS],
    "resources": {"cpu": THREADS, "memory_gib": 16, "architecture": "arm64", "families": ["c8g"]},
    "inputs": inputs,
    "outputs": [{"source": "/tmp/out/", "destination": B + "runs/quast-real/"}],
    "lifecycle": {"ttl": "45m", "on_complete": "terminate", "cost_limit": 0.25},
}

for name, s in (("00-prep", prep), ("01-evaluate", evaluate)):
    with open(os.path.join(out, name + ".task.json"), "w") as fh:
        json.dump(s, fh, indent=2)
        fh.write("\n")
