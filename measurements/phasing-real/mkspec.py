#!/usr/bin/env python3
"""Write the phasing-real TaskSpecs (arm64; Intel and AMD come from scripts/spec_arch.py).

    python3 mkspec.py <outdir>

00-prep   (once, arm64) samtools image: chr20 of HG002's HiFi and Illumina BAMs and of GIAB's VCFs.
01-phase  whatshap image, every chip: phase all chr20 benchmark calls from each read set, score both.
"""
import json
import os
import sys

out = sys.argv[1]
B = "s3://${COOKBOOK_BUCKET}/"
IN = B + "inputs/phasing-real/"
PREP = IN + "prep/"
RUN = B + "runs/phasing-real/"
SAMTOOLS = "quay.io/aarchbio/samtools@sha256:1191739637fb6f46ef97c02b28f693b25ca3ca61f90e1337f349b7b7cc0be4f7"
WHATSHAP = "quay.io/aarchbio/whatshap@sha256:2cfeba1f120ca6ef706d244cac8f4957a6952e78d859e497e6da64eac9a0fa4a"
V = "HG002_GRCh38_1_22_v4.2.1_"
VCFS = {"bench": V + "benchmark.vcf.gz",
        "hifiasm": V + "benchmark_hifiasm_v11_phasetransfer.vcf.gz",
        "sst": V + "benchmark_phased_MHCassembly_StrandSeqANDTrio.vcf.gz"}

PINS = r"""set +e
# spawn runs task commands under bash -e; `set -uo pipefail` does not clear it (spawn#707).
cd /tmp
grep -E ' (%s)$' pins.sha256 > pins.sel
[ "$(wc -l < pins.sel)" -ge %d ] && sha256sum -c pins.sel || { echo "FAIL: a staged input does not match its pin"; exit 1; }
"""


def pinned(names):
    return PINS % ("|".join(n.replace(".", r"\.") for n in names), len(names))


def io(src, name):
    return {"source": src, "destination": "/tmp/" + name}


def task(tid, image, script, pins, ins, out_dst, family, cpu, mem, ttl, cap):
    return {
        "task_id": tid,
        "container": image,
        "command": ["bash", "-c", pinned(pins) + "bash %s\n" % script],
        "resources": {"cpu": cpu, "memory_gib": mem, "architecture": "arm64", "families": [family], "disk_gib": 30},
        "inputs": ins + [io(IN + "pins.sha256", "pins.sha256")],
        "outputs": [{"source": "/tmp/out/", "destination": out_dst}],
        "lifecycle": {"ttl": ttl, "on_complete": "terminate", "cost_limit": cap},
    }


# staging is tmpfs (half the RAM): prep writes ~4.5 GB of slices, phase stages them, hence m8g
# prep pins its scripts; it RECORDS the GIAB VCFs' sha256 (and md5-checks hifiasm's against GIAB's),
# and stage-inputs.sh holds those records to pins.sha256
prep = task("cookbook-phasing-real-prep", SAMTOOLS, "prep.sh", ["prep.sh", "lib.sh"],
            [io(IN + f, f) for f in ("prep.sh", "lib.sh")]
            + [io(B + "inputs/bwa-samtools/chr20.fa", "chr20.fa")]
            + [io(IN + "giab/" + v, k + ".vcf.gz") for k, v in VCFS.items()]
            + [io(IN + "giab/" + v + ".tbi", k + ".vcf.gz.tbi") for k, v in VCFS.items()],
            PREP, "m8g", 8, 32, "2h", 0.6)

SLICES = ["hifi.chr20.bam", "hifi.chr20.bam.bai", "ilmn.chr20.bam", "ilmn.chr20.bam.bai",
          "bench.chr20.vcf.gz", "hifiasm.chr20.vcf.gz", "sst.chr20.vcf.gz", "chr20.fa.fai"]
phase = task("cookbook-phasing-real-phase", WHATSHAP, "phase.sh", ["phase.sh", "lib.sh", "check.py", "chr20.fa"] + SLICES,
             [io(IN + f, f) for f in ("phase.sh", "lib.sh", "check.py")]
             + [io(B + "inputs/bwa-samtools/chr20.fa", "chr20.fa")]
             + [io(PREP + f, f) for f in SLICES],
             RUN + "phase/", "m8g", 4, 16, "6h", 1.5)

for name, s in {"00-prep": prep, "01-phase": phase}.items():
    with open(os.path.join(out, name + ".task.json"), "w") as fh:
        json.dump(s, fh, indent=2)
        fh.write("\n")
