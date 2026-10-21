#!/usr/bin/env python3
"""Render recipes/nf-spawn/main.nf's process scripts for the direct run, as Nextflow renders them.

    python3 -I render.py <outdir>

Each `shell: '''...'''` block has its `!{...}` placeholders filled with what the engine run passes
(--expect_seqs 919 --expect_residues 207712, the inputs' stage names) and is dedented the way
nf-spawn writes .command.sh. The output is compared, byte for byte, against the .command.sh the
engine actually ran, so a rendering mistake here fails the comparison rather than hiding.
"""
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
MAIN = os.path.join(HERE, "..", "..", "recipes", "nf-spawn", "main.nf")
PARAMS = {"params.expect_seqs": "919", "params.expect_residues": "207712"}
# step -> (process, the process's own variables as the engine binds them)
STEPS = {
    "mafft": ("MAFFT", {"seqs": "pf01255.25_unaligned.fa"}),
    "muscle": ("MUSCLE", {"seqs": "pf01255.25_unaligned.fa"}),
    "tree-mafft": ("TREE", {"aligner": "mafft", "aln": "mafft_aln.fa"}),
    "tree-muscle": ("TREE", {"aligner": "muscle", "aln": "muscle_aln.fa"}),
    "rf": ("OBSERVE_RF", {}),
}


def dedent(s):
    lines = s.split("\n")
    ind = [re.match(r"[ \t]*", l).group(0) for l in lines if l.strip()]
    common = ind[0] if ind else ""
    for i in ind[1:]:
        n = 0
        while n < min(len(common), len(i)) and common[n] == i[n]:
            n += 1
        common = common[:n]
    return "\n".join(l[len(common):] if l.startswith(common) else l for l in lines)


def main():
    out = sys.argv[1]
    src = open(MAIN, encoding="utf-8").read()
    os.makedirs(out, exist_ok=True)
    for step, (proc, bind) in STEPS.items():
        m = re.search(r"^process %s \{.*?^    shell:\n    '''\n(.*?)\n    '''\n" % proc, src, re.S | re.M)
        if not m:
            sys.exit("no shell block for " + proc)
        body = m.group(1)
        vals = dict(PARAMS, **bind)

        def sub(x):
            k = x.group(1)
            if k not in vals:
                sys.exit("%s: unbound placeholder !{%s}" % (proc, k))
            return vals[k]
        body = re.sub(r"!\{([^}]+)\}", sub, body)
        with open(os.path.join(out, step + ".sh"), "w", encoding="utf-8") as f:
            f.write(dedent(body))
        print(step, proc)


main()
