#!/usr/bin/env python3
"""Write the two megahit-real TaskSpecs (arm64; x86 and AMD come from scripts/spec_arch.py).

    python3 mkspec.py <outdir>

01-assemble is the workload, run on every chip from the same staged bytes: the whole ZymoBIOMICS
D6300 shotgun run that metaphlan-real staged and pinned. 02-evaluate scores all three chips'
assemblies against Zymo's own reference genomes in one metaQUAST run (arm64).
"""
import json
import os
import sys

out = sys.argv[1]
# Nicholls et al. 2019 (PRJEB29504), metaphlan-real's two whole runs, assembled one after the other:
# D6300 even community (MiSeq 2x151, 8,785,731 pairs) and D6310 log community (HiSeq 2x101,
# 47,832,553 pairs). Reads == 2 x ENA read_count.
SAMPLES = [("ERR2984773", 17571462), ("ERR2935805", 95665106)]
# Organisms abundant enough to assemble, named as in Zymo's reference zip: D6300's eight bacteria
# at 12% each (its two yeasts are 2% and assemble partly), and D6310's top three decades
# (89.1%, 8.9%, 0.89%); the rest of the log ladder is reported, not asserted.
RECOVER = {
    "ERR2984773": "Bacillus_subtilis,Enterococcus_faecalis,Escherichia_coli,Lactobacillus_fermentum,"
                  "Listeria_monocytogenes,Pseudomonas_aeruginosa,Salmonella_enterica,Staphylococcus_aureus",
    "ERR2935805": "Listeria_monocytogenes,Pseudomonas_aeruginosa,Bacillus_subtilis",
}
REFZIP = "ZymoBIOMICS.STD.refseq.v2.zip"
THREADS, MEM_BYTES = 16, 16000000000
B = "s3://${COOKBOOK_BUCKET}/"
IN = B + "inputs/megahit-real/"
READS_IN = B + "inputs/metaphlan-real/prep/"   # metaphlan-real's staged bytes, reused
MEGAHIT = "quay.io/aarchbio/megahit@sha256:d82953bf0096098b0b892edf7180f99b599e8ad17be14c47b1e8c2e1b6a8bdfd"
QUAST = "quay.io/aarchbio/quast@sha256:54122e645394aa741656c54ecdde8737b2ad8cc0ef6ead72392c1be8248ae692"
# what the evaluation reads: each chip's run, plus a second Graviton4 run of the same spec
EVAL = {"arm64": "runs/megahit-real", "arm64-repeat": "runs/megahit-real-repeat",
        "x86_64": "runs-x86_64/megahit-real", "amd": "runs-amd/megahit-real"}

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


files = ["assemble.sh", "canon.py"] + ["%s_%d.fastq.gz" % (a, m) for a, _ in SAMPLES for m in (1, 2)]
assemble = {
    "task_id": "cookbook-megahit-real-assemble",
    "container": MEGAHIT,
    "command": ["bash", "-c", pinned(files, len(files))
                + "F=0\n" + "".join("bash assemble.sh %s %d %d %d || F=1\n" % (a, THREADS, r, MEM_BYTES) for a, r in SAMPLES)
                + "exit $F\n"],
    "resources": {"cpu": THREADS, "memory_gib": 32, "architecture": "arm64", "families": ["c8g"], "disk_gib": 80},
    "inputs": [io(READS_IN + "%s/%s_%d.fastq.gz" % (a, a, m), "%s_%d.fastq.gz" % (a, m)) for a, _ in SAMPLES for m in (1, 2)]
              + [io(IN + f, f) for f in ("assemble.sh", "canon.py", "pins.sha256")],
    "outputs": [{"source": "/tmp/out/", "destination": B + "runs/megahit-real/"}],
    "lifecycle": {"ttl": "3h", "on_complete": "terminate", "cost_limit": 1.5},
}

# The recipe's own determinism setting, -t 1, on the even run: one thread, so no scheduling can
# reorder anything. Same instance size on every chip (c8*.2xlarge); only one core is used.
A1, R1 = SAMPLES[0]
t1files = ["assemble.sh", "canon.py", A1 + "_1.fastq.gz", A1 + "_2.fastq.gz"]
assemble_t1 = {
    "task_id": "cookbook-megahit-real-assemble-t1",
    "container": MEGAHIT,
    "command": ["bash", "-c", pinned(t1files, len(t1files)) + "bash assemble.sh %s 1 %d %d\n" % (A1, R1, MEM_BYTES)],
    "resources": {"cpu": 8, "memory_gib": 16, "architecture": "arm64", "families": ["c8g"], "disk_gib": 40},
    "inputs": [io(READS_IN + "%s/%s_%d.fastq.gz" % (A1, A1, m), "%s_%d.fastq.gz" % (A1, m)) for m in (1, 2)]
              + [io(IN + f, f) for f in ("assemble.sh", "canon.py", "pins.sha256")],
    "outputs": [{"source": "/tmp/out/", "destination": B + "runs/megahit-real-t1/"}],
    "lifecycle": {"ttl": "4h", "on_complete": "terminate", "cost_limit": 1.0},
}
# The same -t 1 run with MEGAHIT's portable (non-POPCNT/BMI2) core forced on x86: the core the
# arm64 build runs, since aarch64 has neither instruction. Run on AMD only (amd target).
assemble_nohw = json.loads(json.dumps(assemble_t1))
assemble_nohw["task_id"] = "cookbook-megahit-real-assemble-nohw"
assemble_nohw["command"][2] = assemble_nohw["command"][2].replace(
    "%d\n" % MEM_BYTES, "%d --no-hw-accel\n" % MEM_BYTES)
assemble_nohw["outputs"][0]["destination"] = B + "runs/megahit-real-nohw/"
T1 = {"arm64-t1": "runs/megahit-real-t1", "x86_64-t1": "runs-x86_64/megahit-real-t1", "amd-t1": "runs-amd/megahit-real-t1",
      "amd-nohw-t1": "runs-amd/megahit-real-nohw"}

efiles = ["evaluate.sh", "canon.py", "compare.py", REFZIP]
einputs = [io(IN + f, f) for f in (REFZIP, "evaluate.sh", "canon.py", "compare.py", "pins.sha256")]
LABELS = {a: dict(EVAL, **(T1 if a == A1 else {})) for a, _ in SAMPLES}
for a, labels in LABELS.items():
    for chip, p in labels.items():
        for f in ("final.contigs.fa", "megahit.log"):
            einputs.append(io(B + "%s/%s/%s" % (p, a, f), "%s.%s.%s" % (a, chip, f)))
evaluate = {
    "task_id": "cookbook-megahit-real-evaluate",
    "container": QUAST,
    "command": ["bash", "-c", pinned(efiles, len(efiles)) + "F=0\n" + "".join(
        "bash evaluate.sh %s %s 8 %s %s || F=1\n" % (REFZIP, a, RECOVER[a], " ".join(LABELS[a])) for a, _ in SAMPLES) + "exit $F\n"],
    "resources": {"cpu": 8, "memory_gib": 16, "architecture": "arm64", "families": ["c8g"], "disk_gib": 40},
    "inputs": einputs,
    "outputs": [{"source": "/tmp/out/", "destination": B + "runs/megahit-real/evaluate/"}],
    "lifecycle": {"ttl": "2h", "on_complete": "terminate", "cost_limit": 0.6},
}

for name, s in (("01-assemble", assemble), ("01-assemble-t1", assemble_t1), ("01-assemble-nohw", assemble_nohw), ("02-evaluate", evaluate)):
    with open(os.path.join(out, name + ".task.json"), "w") as fh:
        json.dump(s, fh, indent=2)
        fh.write("\n")
