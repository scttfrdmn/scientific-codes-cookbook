#!/usr/bin/env python3
"""Write the bismark-real TaskSpecs (arm64; x86 and AMD come from scripts/spec_arch.py).

    python3 mkspec.py <outdir> [probe]

Staging, arm64 only, once: 00-prep-trim re-encodes and trims the reads; 00-prep-bismark and
00-prep-bwameth build the two aligners' GRCh38 indices. Every chip reads the same staged bytes.

The workload, on every chip: 01 Bismark aligns and extracts, 02 samtools sorts, 03 bwa-meth aligns
the same reads, 04 MethylDackel calls both BAMs and makes the comparisons. With `probe`, the same
four specs run on the first 2M reads under runs/bismark-real/probe/, both Bismark parallel modes.
"""
import json
import os
import sys

out = sys.argv[1]
PROBE = len(sys.argv) > 2 and sys.argv[2] == "probe"
B = "s3://${COOKBOOK_BUCKET}/"
IN = B + "inputs/bismark-real/"
MM2 = B + "inputs/minimap2-real/"
RUN = B + ("runs/bismark-real/probe/" if PROBE else "runs/bismark-real/whole/")
FA = "GRCh38_no_alt_analysis_set.fa"
FQ = "ENCFF000LYO.fastq.gz"
TFQ = "ENCFF000LYO.trimmed.fastq.gz"
NTRIM = 31569432            # reads cutadapt wrote (prep-trim/trim-times.txt); set after 00-prep-trim
THREADS = 16
UPTO = 2000000 if PROBE else 0
MODES = ["p", "mc"]       # both, every run: the chunked mode must give the same calls; 04 uses p
BWA_T = 20           # bwa mem -T for bwa-meth: Trim Galore keeps reads down to 20 bp
BISMARK = "quay.io/aarchbio/bismark@sha256:1bdd5895c5b9b458f8f4c1a19fd1b177f3ffab951262c37c543f2ce767150302"
SAMTOOLS = "quay.io/aarchbio/samtools@sha256:1191739637fb6f46ef97c02b28f693b25ca3ca61f90e1337f349b7b7cc0be4f7"
METHYLDACKEL = "quay.io/aarchbio/methyldackel@sha256:c75367a11f9943fe378fcf266859810617e3569f70acd8a55fcb2597a9996586"
BWAMETH = "quay.io/aarchbio/bwameth@sha256:00689db68658a8e10393d0dd810a1a6bc63e0660e9a84961814f74356017cbfa"
CUTADAPT = "quay.io/aarchbio/cutadapt@sha256:434a8a2384580ec718463bac3723a5c407b083855af7bab5f802f9100033781d"  # recipes/cutadapt's pin
BWM = ["ref.fa.bwameth.c2t." + x for x in ("amb", "ann", "bwt", "pac", "sa")]

# BusyBox (bioconda's x86 images) has sha256sum but not --quiet; a plain -c is portable.
PINS = r"""set +e
# spawn runs task commands under bash -e; `set -uo pipefail` does not clear it (spawn#707).
cd /tmp
grep -E ' (%s)$' pins.sha256 > pins.sel
[ "$(wc -l < pins.sel)" -ge %d ] && sha256sum -c pins.sel > pins.check 2>&1 || { cat pins.check; echo "FAIL: a staged input does not match its pin"; exit 1; }
echo "pins: $(wc -l < pins.sel) staged inputs match"
"""


def pinned(names):
    return PINS % ("|".join(n.replace(".", r"\.") for n in names), len(names))


def io(src, name):
    return {"source": src, "destination": "/tmp/" + name}


def script(name):
    return [io(IN + name, name), io(IN + "pins.sha256", "pins.sha256")]


def life(ttl, cap):
    return {"ttl": ttl, "on_complete": "terminate", "cost_limit": cap}


specs = {}
specs["00-prep-trim"] = {
    "task_id": "cookbook-bismark-real-prep-trim",
    "container": CUTADAPT,
    "command": ["bash", "-c", pinned(["prep-trim.sh"]) + "bash prep-trim.sh 6 || exit 1\n"],
    "resources": {"cpu": 8, "memory_gib": 16, "architecture": "arm64", "families": ["c8g"]},
    "inputs": [io(IN + FQ, FQ)] + script("prep-trim.sh"),
    "outputs": [{"source": "/tmp/out/", "destination": IN + "prep-trim/"}],
    "lifecycle": life("2h", 0.5),
}
specs["00-prep-bismark"] = {
    "task_id": "cookbook-bismark-real-prep-bismark",
    "container": BISMARK,
    "command": ["bash", "-c", pinned(["prep-bismark.sh", FA]) + "bash prep-bismark.sh 8 || exit 1\n"],
    "resources": {"cpu": 16, "memory_gib": 64, "architecture": "arm64", "families": ["m8g"]},
    "inputs": [io(MM2 + FA, FA), io(IN + FQ, FQ)] + script("prep-bismark.sh"),
    "outputs": [{"source": "/tmp/out/", "destination": IN + "prep/"},
                {"source": "/tmp/genome/Bisulfite_Genome/", "destination": IN + "Bisulfite_Genome/"}],
    "lifecycle": life("5h", 3.0),
}
specs["00-prep-bwameth"] = {
    "task_id": "cookbook-bismark-real-prep-bwameth",
    "container": BWAMETH,
    "command": ["bash", "-c", pinned(["prep-bwameth.sh", FA]) + "bash prep-bwameth.sh || exit 1\n"],
    "resources": {"cpu": 8, "memory_gib": 64, "architecture": "arm64", "families": ["r8g"], "disk_gib": 60},
    "inputs": [io(MM2 + FA, FA)] + script("prep-bwameth.sh"),
    "outputs": [{"source": "/tmp/out/", "destination": IN + "prep-bwameth/"}],
    "lifecycle": life("8h", 3.0),
}

tag = "-probe" if PROBE else ""
genome = [io(MM2 + FA, "genome/" + FA), io(MM2 + FA + ".fai", "genome/" + FA + ".fai")]
reads = [io(IN + "prep-trim/" + TFQ, TFQ)]
work = {
    "01-bismark": {
        "task_id": "cookbook-bismark-real-bismark" + tag,
        "container": BISMARK,
        "command": ["bash", "-c", pinned(["align-bismark.sh", "genome/" + FA, TFQ, "index.sha256"])
                    + "bash align-bismark.sh %d %d %d %s || exit 1\n" % (THREADS, UPTO, NTRIM, " ".join(MODES))],
        "resources": {"cpu": 16, "memory_gib": 64, "architecture": "arm64", "families": ["m8g"], "disk_gib": 80},
        "inputs": genome + reads
                  + [{"source": IN + "Bisulfite_Genome/", "destination": "/tmp/genome/Bisulfite_Genome/"},
                     io(IN + "prep/index.sha256", "index.sha256")]
                  + script("align-bismark.sh"),
        "outputs": [{"source": "/tmp/out/", "destination": RUN + "bismark/"}],
        "lifecycle": life("2h", 1.5),
    },
    "02-sort": {
        "task_id": "cookbook-bismark-real-sort" + tag,
        "container": SAMTOOLS,
        "command": ["bash", "-c", pinned(["sort.sh"]) + "bash sort.sh 8 %s || exit 1\n" % " ".join(MODES)],
        "resources": {"cpu": 8, "memory_gib": 32, "architecture": "arm64", "families": ["m8g"], "disk_gib": 40},
        "inputs": [io(RUN + "bismark/%s/%s" % (m, f), "%s/%s" % (m, f)) for m in MODES for f in ("bismark.bam", "SE_report.txt")]
                  + script("sort.sh"),
        "outputs": [{"source": "/tmp/out/", "destination": RUN + "sort/"}],
        "lifecycle": life("1h", 0.5),
    },
    "03-bwameth": {
        "task_id": "cookbook-bismark-real-bwameth" + tag,
        "container": BWAMETH,
        "command": ["bash", "-c", pinned(["align-bwameth.sh", TFQ] + BWM)
                    + "bash align-bwameth.sh %d %d %d %d || exit 1\n" % (THREADS, UPTO, NTRIM, BWA_T)],
        "resources": {"cpu": 16, "memory_gib": 64, "architecture": "arm64", "families": ["m8g"], "disk_gib": 60},
        "inputs": reads + [io(IN + "prep-bwameth/bwameth/" + f, f) for f in BWM] + script("align-bwameth.sh"),
        "outputs": [{"source": "/tmp/out/", "destination": RUN + "bwameth/"}],
        "lifecycle": life("2h", 1.5),
    },
    "04-call": {
        "task_id": "cookbook-bismark-real-call" + tag,
        "container": METHYLDACKEL,
        "command": ["bash", "-c", pinned(["call.sh", "genome/" + FA]) + "bash call.sh %d || exit 1\n" % THREADS],
        "resources": {"cpu": 16, "memory_gib": 64, "architecture": "arm64", "families": ["m8g"], "disk_gib": 40},
        "inputs": genome
                  + [io(RUN + "sort/%s/%s" % (MODES[0], f), f) for f in ("bismark.sorted.bam", "bismark.sorted.bam.bai")]
                  + [io(RUN + "bismark/%s/CpG.cov.gz" % MODES[0], "bismark.CpG.cov.gz")]
                  + [io(RUN + "bwameth/" + f, f) for f in ("bwameth.sorted.bam", "bwameth.sorted.bam.bai")]
                  + script("call.sh"),
        "outputs": [{"source": "/tmp/out/", "destination": RUN + "call/"}],
        "lifecycle": life("1h", 1.0),
    },
}
if not PROBE:
    specs.update(work)
    assert NTRIM > 0, "set NTRIM from prep-trim/trim-times.txt before writing the workload specs"
else:
    specs = {k + "-probe": v for k, v in work.items()}

for name, s in specs.items():
    with open(os.path.join(out, name + ".task.json"), "w") as fh:
        json.dump(s, fh, indent=2)
        fh.write("\n")
