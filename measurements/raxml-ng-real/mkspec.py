#!/usr/bin/env python3
"""Write the raxml-ng-real TaskSpecs (arm64; x86 and AMD come from scripts/spec_arch.py).

    python3 mkspec.py <outdir> [canary]

01-raxml   RAxML-NG search + bootstrap + supports + checks           (raxml-ng image)
02-iqtree  IQ-TREE re-evaluates RAxML-NG's best tree under LG+G4      (iqtree image)

The full run reads iqtree-real's staged PF01255.25 seed alignment (919 x 533), the same bytes.
`canary` points the same scripts at the recipe's 114 x 477 fixture with 4 replicates, to prove the
wiring for cents first.
"""
import json
import os
import sys

out = sys.argv[1]
canary = len(sys.argv) > 2 and sys.argv[2] == "canary"
B = "s3://${COOKBOOK_BUCKET}/"
IN = B + "inputs/raxml-ng-real/"
RAXML = "quay.io/aarchbio/raxml-ng@sha256:3a6bbc162ff43249ada42bd92828ac0024855c33b614c0cdbaaa1e3e3eed89e5"
IQTREE = "quay.io/aarchbio/iqtree@sha256:dc6d9f62d56fd1ca92bfb2a9fbd162d419d4f866de67e6e879ba0f895d2a6fb7"
if canary:
    aln_src, aln, label, nb = B + "inputs/iqtree/pfam38.2_seed_alignment.fa", "pfam38.2_seed_alignment.fa", "canary", 4
    ttl, caps = "1h", (0.25, 0.10)
else:
    aln_src, aln, label, nb = B + "inputs/iqtree-real/pf01255.25_seed.fa", "pf01255.25_seed.fa", "pf01255", 20
    ttl, caps = "14h", (4.00, 0.30)
RUN = B + "runs/raxml-ng-real/" + label + "/"

PINS = r"""set +e
# spawn runs task commands under bash -e; `set -uo pipefail` does not clear it (spawn#707).
cd /tmp
grep -E ' (%s)$' pins.sha256 > pins.sel
[ "$(wc -l < pins.sel)" -eq %d ] && sha256sum -c pins.sel || { echo "FAIL: a staged input does not match its pin"; exit 1; }
"""


def spec(task, image, script, args, inputs, outdir, cap, pinned=(), ttl_=None):
    names = [aln, script] + list(pinned)
    cmd = PINS % ("|".join(x.replace(".", r"\.") for x in names), len(names)) + "bash %s %s\n" % (script, args)
    return {
        "task_id": task,
        "container": image,
        "command": ["bash", "-c", cmd],
        "resources": {"cpu": 8, "memory_gib": 16, "architecture": "arm64", "families": ["c8g"]},
        "inputs": [{"source": aln_src, "destination": "/tmp/" + aln},
                   {"source": IN + script, "destination": "/tmp/" + script},
                   {"source": IN + "pins.sha256", "destination": "/tmp/pins.sha256"}] + inputs,
        "outputs": [{"source": "/tmp/out/", "destination": outdir}],
        "lifecycle": {"ttl": ttl_ or ttl, "on_complete": "terminate", "cost_limit": cap},
    }


def inp(src, name):
    return {"source": src, "destination": "/tmp/" + name}


s1 = spec("cookbook-raxml-ng-real-" + label, RAXML, "real.sh", "%s 8 %d" % (aln, nb), [],
          RUN + "raxml/", caps[0])
s2 = spec("cookbook-raxml-ng-real-%s-iq" % label, IQTREE, "iq.sh", "%s 8" % aln,
          [inp(IN + "eval.sh", "eval.sh"),
           inp(RUN + "raxml/rx.raxml.bestTree", "rx.raxml.bestTree"),
           inp(RUN + "raxml/rx.raxml.log", "rx.raxml.log")],
          RUN + "iqtree/", caps[1], pinned=["eval.sh"], ttl_="2h")
os.makedirs(out, exist_ok=True)
for name, s in (("01-raxml", s1), ("02-iqtree", s2)):
    with open(os.path.join(out, name + ".task.json"), "w") as f:
        json.dump(s, f, indent=2)
        f.write("\n")
    print(os.path.join(out, name + ".task.json"))
