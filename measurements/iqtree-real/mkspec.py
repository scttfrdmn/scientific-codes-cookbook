#!/usr/bin/env python3
"""Write the four iqtree-real TaskSpecs (arm64; x86 and AMD come from scripts/spec_arch.py).

    python3 mkspec.py <outdir> [canary]

The full run reads the PF01255.25 seed alignment (919 x 533). `canary` points the same scripts at
the recipe's own 114 x 477 fixture, to prove the wiring for cents before the hours-long run.
"""
import json
import os
import sys

out = sys.argv[1]
canary = len(sys.argv) > 2 and sys.argv[2] == "canary"
B = "s3://${COOKBOOK_BUCKET}/"
IN = B + "inputs/iqtree-real/"
if canary:
    aln_src, aln, n, s, label = B + "inputs/iqtree/pfam38.2_seed_alignment.fa", "pfam38.2_seed_alignment.fa", 114, 477, "canary"
    ttl, cap1, cap2 = "2h", 0.40, 0.10
else:
    aln_src, aln, n, s, label = IN + "pf01255.25_seed.fa", "pf01255.25_seed.fa", 919, 533, "pf01255"
    ttl, cap1, cap2 = "16h", 3.00, 0.30
RUN = B + "runs/iqtree-real/" + label

PINS = r"""set +e
# spawn runs task commands under bash -e; `set -uo pipefail` does not clear it (spawn#707).
cd /tmp
grep -E ' (%s)$' pins.sha256 > pins.sel
[ "$(wc -l < pins.sel)" -eq %d ] && sha256sum -c pins.sel || { echo "FAIL: a staged input does not match its pin"; exit 1; }
"""


def spec(task, image, script, args, inputs, outdir, cap, pinned=(), pre=""):
    names = [aln, script] + list(pinned)
    cmd = PINS % ("|".join(x.replace(".", r"\.") for x in names), len(names)) + pre + "bash %s %s\n" % (script, args)
    return {
        "task_id": task,
        "container": image,
        "command": ["bash", "-c", cmd],
        "resources": {"cpu": 8, "memory_gib": 16, "architecture": "arm64", "families": ["c8g"]},
        "inputs": [{"source": aln_src, "destination": "/tmp/" + aln},
                   {"source": IN + script, "destination": "/tmp/" + script},
                   {"source": IN + "pins.sha256", "destination": "/tmp/pins.sha256"}] + inputs,
        "outputs": [{"source": "/tmp/out/", "destination": outdir}],
        "lifecycle": {"ttl": ttl, "on_complete": "terminate", "cost_limit": cap},
    }


s1 = spec("cookbook-iqtree-real-%s" % label,
          "quay.io/aarchbio/iqtree@sha256:dc6d9f62d56fd1ca92bfb2a9fbd162d419d4f866de67e6e879ba0f895d2a6fb7",
          "real.sh", "%s 8 %d %d" % (aln, n, s), [], RUN + "/iqtree/", cap1)
IQ = "quay.io/aarchbio/iqtree@sha256:dc6d9f62d56fd1ca92bfb2a9fbd162d419d4f866de67e6e879ba0f895d2a6fb7"
TREE = {"source": RUN + "/iqtree/real.treefile", "destination": "/tmp/real.treefile"}
s2 = spec("cookbook-iqtree-real-%s-eval" % label, IQ, "eval.sh", "%s 8" % aln, [TREE], RUN + "/eval/", cap2)
s3 = spec("cookbook-iqtree-real-%s-rax" % label,
          "quay.io/aarchbio/raxml-ng@sha256:3a6bbc162ff43249ada42bd92828ac0024855c33b614c0cdbaaa1e3e3eed89e5",
          "rax.sh", "%s 8" % aln,
          [TREE] + [{"source": RUN + "/eval/evconv." + x, "destination": "/tmp/evconv." + x} for x in ("iqtree", "treefile")],
          RUN + "/raxml/", cap2)
# Graviton4's ML tree, re-optimised on the other chips: the same topology everywhere, so the LG+G4
# optimum is a cross-chip fixed point even though each chip's own search ends on a different tree.
s4 = spec("cookbook-iqtree-real-%s-eval-g4tree" % label, IQ, "eval.sh", "%s 8" % aln,
          [{"source": IN + "g4-ml.treefile", "destination": "/tmp/g4-ml.treefile"}],
          RUN + "/eval-g4tree/", cap2, pinned=["g4-ml.treefile"], pre="cp g4-ml.treefile real.treefile\n")
# The control: task 1's ModelFinder alone, again, on one chip. If it reproduces that chip's own
# fits, a cross-chip difference is the chip; if not, it is the run.
s5 = spec("cookbook-iqtree-real-%s-mf-repeat" % label, IQ, "real.sh", "", [], RUN + "/mf-repeat/", cap2)
s5["command"][2] = s5["command"][2].replace(
    "bash real.sh \n",
    "mkdir -p out\niqtree3 -s %s -m MF -mset LG,WAG,JTT,Q.pfam -T 8 --seed 12345 --prefix mf -redo > out/mf.stdout 2>&1\n"
    "echo \"exit $?\"\ncp mf.log mf.iqtree mf.model.gz out/\n" % aln)
for sp in (s2, s3, s4, s5):
    sp["lifecycle"]["ttl"] = "2h"
for name, sp in (("01-iqtree", s1), ("02-eval", s2), ("03-raxml", s3), ("04-eval-g4tree", s4), ("05-mf-repeat", s5)):
    with open(os.path.join(out, "%s%s.task.json" % (name, "-canary" if canary else "")), "w") as f:
        json.dump(sp, f, indent=2)
        f.write("\n")
