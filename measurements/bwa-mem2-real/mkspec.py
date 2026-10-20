#!/usr/bin/env python3
"""Write measurements/bwa-mem2-real's arm64 specs, pinning the staged scripts by their current
sha256. Intel and AMD come from scripts/spec_arch.py, never from a second copy.
    python3 mkspec.py

01-bwa   bwa image, every chip: HG00096's three low-coverage runs, one `bwa mem` each.
02-mem2  bwa-mem2 image, every chip: the same three runs, then record-by-record against 01's output
         on the same chip (spec_arch.py moves /runs/ to /runs-<arch>/ in both, so the chain stays
         on one architecture).
"""
import hashlib
import json
import os

D = os.path.dirname(os.path.abspath(__file__))
B = "s3://${COOKBOOK_BUCKET}"
IN = B + "/inputs/bwa-mem2-real"
REAL = B + "/inputs/bwa-real"
OUT = B + "/runs/bwa-mem2-real"
REF = "GRCh38_full_analysis_set_plus_decoy_hla.fa"
# recipes/bwa-mem2's own pins
BWA = "quay.io/aarchbio/bwa@sha256:19f0eceab80740b821be7ada082d4434acf778912aac658dd1b4c6692dd2e9ba"
MEM2 = "quay.io/aarchbio/bwa-mem2@sha256:f9759b09a39aab57d879babbcb5a876a9ed4a55a698eb10b476f70a9cec81c15"
THREADS = 16
RUNS = ["SRR062634", "SRR062635", "SRR062641"]


def sha(f):
    return hashlib.sha256(open(os.path.join(D, f), "rb").read()).hexdigest()


def pinned(files, run):
    # spawn runs task commands under bash -e; `set -uo pipefail` does not clear it (spawn#707).
    return ["bash", "-c",
            "set +e\nset -uo pipefail\nexport PATH=/opt/conda/bin:/usr/local/bin:$PATH HOME=/tmp TMPDIR=/var/tmp\n"
            "cd /tmp\n{ %s; } > scripts.sha256\n"
            "sha256sum -c scripts.sha256 || { echo 'FAIL: a staged script does not match its pin'; exit 1; }\n"
            "%s\n" % ("; ".join("printf '%%s  %%s\\n' %s %s" % (sha(f), f) for f in files), run)]


def ins(pairs):
    return [{"source": s, "destination": "/tmp/" + d} for s, d in pairs]


def reads():
    out = []
    for r in RUNS:
        base = REAL if r == "SRR062634" else IN + "/reads"   # SRR062634 is measurements/bwa-real's
        for m in (1, 2):
            f = "%s_%d.filt.fastq.gz" % (r, m)
            out.append((base + "/" + f, f))
    return out


SCRIPTS = ["align.sh", "lib.sh", "sample.tsv"]
scripts = [(IN + "/" + f, f) for f in SCRIPTS]
# Sized from recipes/bwa-mem2's measured runs of SRR062634 on all three chips (r8g/r8i/r8a.4xlarge,
# 16 threads): bwa 817-1175 s and bwa-mem2 529-1114 s per run. tmpfs (half of 128 GiB) holds the
# 11.7 GB of reads plus the index (5.6 GB bwa; the 17.7 GB tar for bwa-mem2) and the 18 GB of bwa
# core fields; SAM goes to the root disk. TTL is a hang backstop; cost_limit is the guard.
bwa = {
    "task_id": "cookbook-bwamem2-real-bwa",
    "container": BWA,
    "command": pinned(SCRIPTS, "bash /tmp/align.sh bwa %d" % THREADS),
    "resources": {"cpu": THREADS, "memory_gib": 124, "disk_gib": 80, "architecture": "arm64", "families": ["r8g"]},
    "inputs": ins(scripts + [(REAL + "/" + REF + "." + e, REF + "." + e) for e in ("amb", "ann", "bwt", "pac", "sa", "alt")]
                  + reads()),
    "outputs": [{"source": "/tmp/out/", "destination": OUT + "/bwa/out/"},
                {"source": "/tmp/red/", "destination": OUT + "/bwa/red/"}],
    "lifecycle": {"ttl": "5h", "on_complete": "terminate", "cost_limit": 2.0},
}
mem2 = {
    "task_id": "cookbook-bwamem2-real-mem2",
    "container": MEM2,
    "command": pinned(SCRIPTS, "bash /tmp/align.sh bwa-mem2 %d" % THREADS),
    "resources": {"cpu": THREADS, "memory_gib": 124, "disk_gib": 100, "architecture": "arm64", "families": ["r8g"]},
    "inputs": ins(scripts + [(B + "/inputs/bwa-mem2/mem2-index.tar", "mem2-index.tar"),
                             (REAL + "/" + REF + ".alt", REF + ".alt")] + reads()
                  + [(OUT + "/bwa/out/results.tsv", "bwa-results.tsv")])
              + [{"source": OUT + "/bwa/red/", "destination": "/tmp/bwa-red/"}],
    "outputs": [{"source": "/tmp/out/", "destination": OUT + "/mem2/out/"}],
    "lifecycle": {"ttl": "4h", "on_complete": "terminate", "cost_limit": 1.6},
}

for name, s in {"01-bwa": bwa, "02-mem2": mem2}.items():
    with open(os.path.join(D, name + ".task.json"), "w") as fh:
        json.dump(s, fh, indent=2)
        fh.write("\n")
