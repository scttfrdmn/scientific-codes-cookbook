#!/usr/bin/env python3
"""Write measurements/hic-real's two arm64 specs, pinning the staged scripts by their current
sha256. x86/AMD come from scripts/spec_arch.py hic, never from a second copy.
    mkspec.py <run label, e.g. r1>"""
import hashlib
import json
import os
import sys

RUN = sys.argv[1]
D = os.path.dirname(os.path.abspath(__file__))
IN = "s3://${COOKBOOK_BUCKET}/inputs/hic-real"
OUT = "s3://${COOKBOOK_BUCKET}/runs/hic-real/%s" % RUN
PAIRTOOLS = "quay.io/aarchbio/pairtools@sha256:2b4ce4761b3e6acf9739ebbd598fbe5b4a9a42ab58fa35b23ae28733efaf913e"
COOLER = "quay.io/aarchbio/cooler@sha256:5f665b9b2c94e5ca6812216309cb2e8d462d7d4498d4b25206ac0515e89a7dd0"
THREADS = 16


def sha(f):
    return hashlib.sha256(open(os.path.join(D, f), "rb").read()).hexdigest()


def command(script, extra=()):
    pins = "".join("%s  /tmp/%s\\n" % (sha(f), f) for f in (script,) + tuple(extra))
    return ["bash", "-c",
            "set +e\ncd /tmp\nprintf '%s' > scripts.sha256\n"
            "sha256sum -c scripts.sha256 || { echo 'FAIL: staged script does not match its pin'; exit 1; }\n"
            "bash /tmp/%s %d\n" % (pins, script, THREADS)]


def inp(src, dst):
    return {"source": src, "destination": dst}


def spec(name, script, image, inputs, ttl, cap, extra=()):
    return {
        "task_id": "cookbook-hic-real-%s-%s" % (name, RUN),
        "command": command(script, extra),
        "container": image,
        "resources": {"cpu": THREADS, "memory_gib": 32, "disk_gib": 80,
                      "architecture": "arm64", "families": ["c8g"]},
        "inputs": [inp("%s/%s" % (IN, f), "/tmp/%s" % f) for f in (script,) + tuple(extra)] + inputs
                  + [inp("%s/inputs.sha256" % IN, "/tmp/inputs.sha256"), inp("%s/inputs.md5" % IN, "/tmp/inputs.md5")],
        "outputs": [{"source": "/tmp/out/", "destination": "%s/%s/" % (OUT, name)}],
        "lifecycle": {"ttl": ttl, "on_complete": "terminate", "cost_limit": cap},
    }


pairs = spec("pairs", "pairs.sh", PAIRTOOLS, [
    inp("%s/%s" % (IN, f), "/tmp/%s" % f)
    for f in ("4DNFILGIU5G7.pairs.gz", "4DNFI4EZMKAO.pairs.gz", "4DNFIHMMV629.pairs.gz", "4DNFI823LSII.chrom.sizes")
], "2h", 0.8)   # Graviton4 measured a 26 min window, $0.28
cool = spec("cool", "cool.sh", COOLER, [
    inp("%s/%s" % (IN, f), "/tmp/%s" % f) for f in ("4DNFIFBPTHCU.mcool", "4DNFI823LSII.chrom.sizes")
] + [inp("%s/pairs/combined.pairs.gz" % OUT, "/tmp/combined.pairs.gz"),
     inp("%s/pairs/check.txt" % OUT, "/tmp/pairs-check.txt")], "2h", 0.5, extra=("ident.py",))   # Graviton4: 12 min, $0.13
for fn, s in [("01-pairs.task.json", pairs), ("02-cool.task.json", cool)]:
    with open(os.path.join(D, fn), "w") as fh:
        json.dump(s, fh, indent=2)
        fh.write("\n")
    print(fn)
