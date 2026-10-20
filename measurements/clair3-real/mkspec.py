#!/usr/bin/env python3
"""Write the clair3-real TaskSpecs (arm64; Intel and AMD call specs come from scripts/spec_arch.py).

    python3 mkspec.py <outdir>

01-call    clair3 image, every chip: Clair3 hifi_revio on HG002 chr20 HiFi (phasing-real's slice).
02-eval    picard image (its JVM) + RTG Tools 3.13's RTG.jar, Graviton4, once: vcfeval on all three VCFs.
03-strat   bcftools image, Graviton4, once: GIAB v3.3 stratifications, bookkeeping, cross-chip diffs.
"""
import json
import os
import sys

out = sys.argv[1]
B = "s3://${COOKBOOK_BUCKET}/"
IN = B + "inputs/clair3-real/"
PH = B + "inputs/phasing-real/prep/"
RUNS = {"arm64": B + "runs/clair3-real/", "x86_64": B + "runs-x86_64/clair3-real/", "amd": B + "runs-amd/clair3-real/"}
CLAIR3 = "quay.io/aarchbio/clair3@sha256:b464803435a71829228bedd4641d994018106952d746517188761d7dbd7bd289"
PICARD = "quay.io/aarchbio/picard@sha256:c6a742e8277b9010df9aa3b9a6bb40651792ff319627cc1c2bf8a70ac633e6bd"
BCFTOOLS = "quay.io/aarchbio/bcftools@sha256:8171fe74464620a0585cc8998fd9bacbfc04480ac5571229f22f390ecfd5658e"
STRATA = ["easy", "alldifficult", "lowmapsegdup", "notlowmapsegdup", "trhomopolymer", "segdups", "lowmap", "complexsv"]

PINS = r"""set +e
# spawn runs task commands under bash -e; `set -uo pipefail` does not clear it (spawn#707).
cd /tmp
grep -E ' (%s)$' pins.sha256 > pins.sel
[ "$(wc -l < pins.sel)" -ge %d ] && sha256sum -c pins.sel || { echo "FAIL: a staged input does not match its pin"; exit 1; }
"""


def io(src, name):
    return {"source": src, "destination": "/tmp/" + name}


def task(tid, image, script, pins, ins, out_dst, family, cpu, mem, ttl, cap):
    pins = [script, "lib.sh"] + pins
    cmd = PINS % ("|".join(n.replace(".", r"\.") for n in pins), len(pins)) + "bash %s\n" % script
    return {
        "task_id": tid,
        "container": image,
        "command": ["bash", "-c", cmd],
        "resources": {"cpu": cpu, "memory_gib": mem, "architecture": "arm64", "families": [family], "disk_gib": 40},
        "inputs": [io(IN + f, f) for f in (script, "lib.sh", "pins.sha256")] + ins,
        "outputs": [{"source": "/tmp/out/", "destination": out_dst}],
        "lifecycle": {"ttl": ttl, "on_complete": "terminate", "cost_limit": cap},
    }


REF = [io(B + "inputs/bwa-samtools/chr20.fa", "chr20.fa"), io(PH + "chr20.fa.fai", "chr20.fa.fai")]
BENCH = [io(PH + "bench.chr20.vcf.gz", "bench.chr20.vcf.gz"),
         io(IN + "giab/HG002_GRCh38_1_22_v4.2.1_benchmark_noinconsistent.bed", "bench.bed")]
VCFS = [io(RUNS[c] + "call/clair3.vcf.gz", c + ".vcf.gz") for c in RUNS] \
    + [io(RUNS[c] + "call/clair3.vcf.gz.tbi", c + ".vcf.gz.tbi") for c in RUNS]

# 16 threads on a 4xlarge: the same thread count and size on every chip. Measured on m8g.4xlarge:
# 412 s of Clair3, cgroup peak 14.1 GiB, so c8g.4xlarge (32 GiB; 16 GiB tmpfs for a 1.5 GB BAM) fits
call = task("cookbook-clair3-real-call", CLAIR3, "call.sh",
            ["hifi.chr20.bam", "hifi.chr20.bam.bai", "chr20.fa", "chr20.fa.fai"],
            [io(PH + "hifi.chr20.bam", "hifi.chr20.bam"), io(PH + "hifi.chr20.bam.bai", "hifi.chr20.bam.bai")] + REF,
            RUNS["arm64"] + "call/", "c8g", 16, 32, "2h", 1.0)
ev = task("cookbook-clair3-real-eval", PICARD, "eval.sh",
          ["RTG.jar", "chr20.fa", "chr20.fa.fai", "bench.chr20.vcf.gz", "bench.bed"],
          [io(IN + "RTG.jar", "RTG.jar")] + REF + BENCH + VCFS,
          RUNS["arm64"] + "eval/", "m8g", 2, 8, "1h", 0.2)
st = task("cookbook-clair3-real-strat", BCFTOOLS, "strat.sh",
          ["bench.chr20.vcf.gz", "bench.bed"] + ["strat.%s.bed.gz" % s for s in STRATA],
          BENCH + [io(IN + "strat/%s.bed.gz" % s, "strat.%s.bed.gz" % s) for s in STRATA] + VCFS
          + [io(RUNS["arm64"] + "eval/%s/" % c, c + "/") for c in RUNS]
          + [io(RUNS["arm64"] + "eval/results.tsv", "eval.results.tsv")],
          RUNS["arm64"] + "strat/", "m8g", 2, 8, "1h", 0.2)

for name, s in {"01-call": call, "02-eval": ev, "03-strat": st}.items():
    with open(os.path.join(out, name + ".task.json"), "w") as fh:
        json.dump(s, fh, indent=2)
        fh.write("\n")
