#!/usr/bin/env python3
"""measurements/cromwell-wdl-real's specs.

    mkspec.py                                    write the arm64 specs, pinning staged scripts by sha256
    mkspec.py <arm64|x86_64|amd> <bucket> <nonce> <spec.task.json>   print a resolved spec's path

The run task is a HOST task (no `container`): Cromwell's local backend launches each WDL task as a
container through the Docker socket, and spawn's container path does not expose one. So the x86 and
AMD specs cannot come from scripts/spec_arch.py, which rewrites a spec's `container`; this does the
same rewrite for a host task -- architecture, family and cost cap from spec_arch's own tables, and
/runs/ -> /runs-<arch>/. The images are picked inside run.sh by `uname -m`, from the cromwell-wdl
and gatk4 recipes' pins."""
import hashlib
import json
import os
import sys
import tempfile

D = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(D, "..", "..", "scripts"))
from spec_arch import ARCH, COST_FACTOR, FAMILY  # noqa: E402  one family map, not a second copy

IN = "s3://${COOKBOOK_BUCKET}/inputs/cromwell-wdl-real"
G = "s3://${COOKBOOK_BUCKET}/inputs/gatk4"
OUT = "s3://${COOKBOOK_BUCKET}/runs/cromwell-wdl-real"
GATK = "quay.io/aarchbio/gatk4@sha256:92065598dde922a2223eb57863606c4bf8558b11af938ada9637efb9d0d8d0fc"
SCRIPTS = ["run.sh", "rss.py", "meta.py", "gvcf.py"]


def sha(f):
    return hashlib.sha256(open(os.path.join(D, f), "rb").read()).hexdigest()


def pinned(files, run, where="/tmp"):
    # spawn runs task commands under bash -e; `set -uo pipefail` does not clear it (spawn#707).
    return ["bash", "-c",
            "set +e\nexport HOME=/tmp TMPDIR=/tmp\ncd %s\nmkdir -p /tmp/out\n{ %s; } > /tmp/scripts.sha256\n"
            "sha256sum -c /tmp/scripts.sha256 || { echo 'FAIL: a staged script does not match its pin'; exit 1; }\n"
            "%s\n" % (where, "; ".join("printf '%%s  %%s\\n' %s %s" % (sha(f), f) for f in files), run)]


def inp(src, dst):
    return {"source": src, "destination": dst}


stage = {
    "task_id": "cookbook-cromwell-wdl-real-stage",
    "command": pinned(["fetch.sh", "pins.sha256"], "bash /tmp/fetch.sh"),
    "resources": {"cpu": 2, "memory_gib": 4, "architecture": "arm64", "families": ["c8g"]},
    "inputs": [inp("%s/fetch.sh" % IN, "/tmp/fetch.sh"), inp("%s/pins.sha256" % IN, "/tmp/pins.sha256")],
    "outputs": [{"source": "/tmp/out/", "destination": IN + "/"}],
    "lifecycle": {"ttl": "30m", "on_complete": "terminate", "cost_limit": 0.05},
}
intervals = {
    "task_id": "cookbook-cromwell-wdl-real-intervals",
    "command": pinned(["intervals.sh"], "bash /tmp/intervals.sh"),
    "container": GATK,
    "resources": {"cpu": 2, "memory_gib": 4, "architecture": "arm64", "families": ["c8g"]},
    "inputs": [inp("%s/intervals.sh" % IN, "/tmp/intervals.sh"), inp("%s/chr20.dict" % G, "/tmp/chr20.dict"),
               inp("%s/wgs_calling_regions.hg38.interval_list" % IN, "/tmp/wgs_calling_regions.hg38.interval_list")],
    "outputs": [{"source": "/tmp/out/", "destination": IN + "/intervals/"}],
    "lifecycle": {"ttl": "30m", "on_complete": "terminate", "cost_limit": 0.05},
}


def run(name, scatter, jobs, res, ttl, cap):
    files = SCRIPTS
    return {
        "task_id": "cookbook-cromwell-wdl-real-%s" % name,
        "command": pinned(files, "bash /tmp/in/run.sh %s %d 2>&1 | tee /tmp/out/run.log; exit ${PIPESTATUS[0]}"
                          % (scatter, jobs), where="/tmp/in"),
        "resources": res,
        "inputs": [inp("%s/%s" % (IN, f), "/tmp/in/%s" % f) for f in files + ["pins.sha256"]]
        + [inp("%s/docker-27.5.1-%s.tgz" % (IN, a), "/tmp/in/docker-27.5.1-%s.tgz" % a) for a in ("aarch64", "x86_64")]
        + [inp("%s/haplotypecaller-gvcf-gatk4.wdl" % IN, "/tmp/in/haplotypecaller-gvcf-gatk4.wdl"),
           inp("%s/intervals/%s/" % (IN, scatter), "/tmp/in/%s/" % scatter),
           inp("s3://${COOKBOOK_BUCKET}/inputs/bwa-samtools/chr20.fa", "/tmp/in/chr20.fa"),
           inp("%s/chr20.fa.fai" % G, "/tmp/in/chr20.fa.fai"), inp("%s/chr20.dict" % G, "/tmp/in/chr20.dict"),
           inp("%s/NA12878.chr20.30x.bam" % G, "/tmp/in/NA12878.chr20.30x.bam"),
           inp("%s/NA12878.chr20.30x.bam.bai" % G, "/tmp/in/NA12878.chr20.30x.bam.bai")],
        "outputs": [{"source": "/tmp/out/", "destination": "%s/%s/" % (OUT, name)}],
        "lifecycle": {"ttl": ttl, "on_complete": "terminate", "cost_limit": cap},
    }


# The probe: 2 Mb in 4 shards, to prove the wiring and read the per-shard footprint.
probe = run("probe", "scatter-probe", 4,
            {"cpu": 16, "memory_gib": 32, "disk_gib": 40, "architecture": "arm64", "families": ["c8g"]}, "1h", 0.6)
# The workload: 16 shards at once. r8g.4xlarge because the published WDL asks 7 GB per
# HaplotypeCaller shard (-Xmx6G): 16 x 7 = 112 GB, which only the 128 GiB box honours.
full = run("run", "scatter-16", 16,
           {"cpu": 16, "memory_gib": 128, "disk_gib": 40, "architecture": "arm64", "families": ["r8g"]}, "6h", 4.0)

# The three chips' merged GVCFs, compared where they sit. Host python3; no image needed.
CHIPS = {"arm64": "runs", "x86_64": "runs-x86_64", "amd": "runs-amd"}
compare = {
    "task_id": "cookbook-cromwell-wdl-real-compare",
    "command": pinned(["compare.py"], "python3 -u compare.py /tmp/out/compare.txt %s 2>&1 | tee /tmp/out/compare.log"
                      % " ".join("%s=/tmp/%s.g.vcf.gz" % (c, c) for c in CHIPS)),
    "resources": {"cpu": 4, "memory_gib": 32, "architecture": "arm64", "families": ["r8g"]},
    "inputs": [inp("%s/compare.py" % IN, "/tmp/compare.py")]
    + [inp("s3://${COOKBOOK_BUCKET}/%s/cromwell-wdl-real/run/NA12878.chr20.30x.g.vcf.gz" % p, "/tmp/%s.g.vcf.gz" % c)
       for c, p in CHIPS.items()],
    "outputs": [{"source": "/tmp/out/", "destination": OUT + "/compare/"}],
    "lifecycle": {"ttl": "1h", "on_complete": "terminate", "cost_limit": 0.2},
}


def resolve(arch, bucket, nonce, path):
    spec = json.loads(open(path).read().replace("${COOKBOOK_BUCKET}", bucket))
    spec["task_id"] = "%s-%s" % (spec["task_id"], nonce) if arch == "arm64" \
        else "%s-%s-%s" % (spec["task_id"], arch.replace("_", ""), nonce)
    if arch != "arm64":
        if spec.get("container"):
            sys.exit("%s has a container: resolve it with scripts/spec_arch.py" % path)
        r = spec["resources"]
        r["architecture"] = ARCH[arch]
        r["families"] = [FAMILY[arch].get(f, f) for f in r.get("families", [])]
        life = spec["lifecycle"]
        life["cost_limit"] = round(float(life["cost_limit"]) * COST_FACTOR[arch], 2)
        for io in spec.get("outputs", []):
            io["destination"] = io["destination"].replace("/runs/", "/runs-%s/" % arch)
    fd, out = tempfile.mkstemp(prefix="cromwell-wdl-real.", suffix=".json", dir=os.environ.get("TMPDIR", "/tmp"))
    with os.fdopen(fd, "w") as fh:
        json.dump(spec, fh, indent=2)
    print(out)


if __name__ == "__main__":
    if len(sys.argv) == 5:
        resolve(*sys.argv[1:])
        sys.exit(0)
    for fn, s in [("00-stage.task.json", stage), ("00-intervals.task.json", intervals),
                  ("01-probe.task.json", probe), ("01-run.task.json", full),
                  ("02-compare.task.json", compare)]:
        with open(os.path.join(D, fn), "w") as fh:
            json.dump(s, fh, indent=2)
            fh.write("\n")
        print(fn)
