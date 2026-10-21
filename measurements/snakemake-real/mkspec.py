#!/usr/bin/env python3
"""measurements/snakemake-real's one TaskSpec: the run without Snakemake (direct.sh).

    mkspec.py                                          write direct.task.json (arm64), scripts pinned by sha256
    mkspec.py <arm64|x86_64|amd> <bucket> <nonce>      print the path of a resolved spec for that chip

The workflow itself has no spec: Snakemake writes one per job through the executor, and the chip is
its --spawn-instance-type (run.sh). direct.sh is a HOST task -- it installs docker and runs the pinned
seqkit by digest exactly as the rule does -- so scripts/spec_arch.py, which rewrites a spec's
`container`, cannot resolve it. This does the same rewrite for a host task from spec_arch's own
tables: architecture, family, cost cap, and /runs/ -> /runs-<chip>/ (so each chip's direct run
reads that chip's workflow outputs)."""
import hashlib
import json
import os
import sys
import tempfile

D = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(D, "..", "..", "scripts"))
from spec_arch import ARCH, COST_FACTOR, FAMILY  # noqa: E402  one family map, not a second copy

IN = "s3://${COOKBOOK_BUCKET}/inputs/snakemake-real"
SMK = "s3://${COOKBOOK_BUCKET}/runs/snakemake-real/r1"
SPEC = os.path.join(D, "direct.task.json")


def sha(f):
    return hashlib.sha256(open(os.path.join(D, f), "rb").read()).hexdigest()


def inp(src, dst):
    return {"source": src, "destination": dst}


def direct():
    pins = "; ".join("printf '%%s  %%s\\n' %s %s" % (sha(f), f) for f in ("direct.sh", "cohort.py"))
    return {
        "task_id": "cookbook-snakemake-real-direct",
        # spawn runs task commands under bash -e; `set +e` first (spawn#707).
        "command": ["bash", "-c",
                    "set +e\nexport HOME=/tmp TMPDIR=/tmp\ncd /tmp/in\nmkdir -p /tmp/out\n{ %s; } > /tmp/scripts.sha256\n"
                    "sha256sum -c /tmp/scripts.sha256 || { echo 'FAIL: a staged script does not match its pin'; exit 1; }\n"
                    "bash /tmp/in/direct.sh ${COOKBOOK_BUCKET} 2>&1 | tee /tmp/out/direct.log; exit ${PIPESTATUS[0]}\n" % pins],
        "resources": {"cpu": 2, "memory_gib": 4, "disk_gib": 20, "architecture": "arm64", "families": ["c8g"]},
        "inputs": [inp("%s/%s" % (IN, f), "/tmp/in/%s" % f) for f in ("direct.sh", "cohort.py", "cohort-truth.tsv", "keys.tsv")]
        + [inp(SMK + "/stats/", "/tmp/in/smk/stats/"), inp(SMK + "/cohort.tsv", "/tmp/in/smk/cohort.tsv")],
        "outputs": [{"source": "/tmp/out/", "destination": "s3://${COOKBOOK_BUCKET}/runs/snakemake-real/direct/"}],
        "lifecycle": {"ttl": "2h", "on_complete": "terminate", "cost_limit": 0.10},
    }


def resolve(chip, bucket, nonce):
    text = open(SPEC).read().replace("${COOKBOOK_BUCKET}", bucket)
    spec = json.loads(text)
    spec["task_id"] = "%s-%s" % (spec["task_id"], nonce if chip == "arm64" else "%s-%s" % (chip.replace("_", ""), nonce))
    if chip != "arm64":
        res = spec["resources"]
        res["architecture"] = ARCH[chip]
        res["families"] = [FAMILY[chip].get(x, x) for x in res["families"]]
        spec["lifecycle"]["cost_limit"] = round(spec["lifecycle"]["cost_limit"] * COST_FACTOR[chip], 2)
        for io in spec["inputs"] + spec["outputs"]:
            for k in ("source", "destination"):
                io[k] = io[k].replace("/runs/", "/runs-%s/" % chip)
    fd, path = tempfile.mkstemp(prefix="snakemake-real-direct.", suffix=".%s.json" % chip,
                                dir=os.environ.get("TMPDIR", "/tmp"))
    with os.fdopen(fd, "w") as fh:
        json.dump(spec, fh, indent=2)
    return path


if __name__ == "__main__":
    if len(sys.argv) == 1:
        with open(SPEC, "w") as fh:
            json.dump(direct(), fh, indent=2)
            fh.write("\n")
        print(SPEC)
    elif len(sys.argv) == 4 and (sys.argv[1] == "arm64" or sys.argv[1] in ARCH):
        print(resolve(*sys.argv[1:]))
    else:
        sys.exit(__doc__)
