#!/usr/bin/env python3
"""Build three planted-truth assemblies from the reference, and the numbers QUAST must report for them.

    python3 -I mkcontrols.py <reference.fna.gz> <outdir>

self     the reference itself: 100% genome fraction, nothing wrong, NGA50 == reference length.
planted  the reference with 50 substitutions and 20 short indels at fixed, widely spaced positions.
chimera  the reference cut into eight pieces at fixed positions, two of them reverse-complemented
         and two non-adjacent ones (P2 and P5) joined end to start: exactly one relocation.

Positions are arithmetic, not random, so the bytes are the same on every machine. Writes
<outdir>/{self,planted,chimera}.fasta and <outdir>/truth.json.
"""
import gzip
import json
import os
import sys

ref_path, out = sys.argv[1], sys.argv[2]
os.makedirs(out, exist_ok=True)
with gzip.open(ref_path, "rt") as fh:
    seq = "".join(line.strip() for line in fh if not line.startswith(">")).upper()
L = len(seq)
COMP = str.maketrans("ACGT", "TGCA")


def rc(s):
    return s.translate(COMP)[::-1]


def write(name, records):
    with open(os.path.join(out, name + ".fasta"), "w") as fh:
        for rid, s in records:
            fh.write(">%s\n" % rid)
            for i in range(0, len(s), 80):
                fh.write(s[i:i + 80] + "\n")


write("self", [("U00096.3_copy", seq)])

# planted: substitutions at 45,000 + 90,000 i (i < 50); indels at 67,500 + 225,000 j (j < 20), of
# length 1 + j % 5, insertions at even j and deletions at odd j. The two ladders never come within
# 22.5 kb of each other.
SUB = {"A": "C", "C": "G", "G": "T", "T": "A"}
edits = [(45000 + 90000 * i, "sub", 1) for i in range(50)]
edits += [(67500 + 225000 * j, "ins" if j % 2 == 0 else "del", 1 + j % 5) for j in range(20)]
s = list(seq)
for pos, kind, n in sorted(edits, reverse=True):          # right to left, so positions stay valid
    if kind == "sub":
        s[pos] = SUB[s[pos]]
    elif kind == "ins":
        s[pos:pos] = list("ACGTA"[:n])
    else:
        del s[pos:pos + n]
planted = "".join(s)
write("planted", [("U00096.3_planted", planted)])

cuts = [0, 600000, 1150000, 1800000, 2300000, 2950000, 3500000, 4100000, L]
P = [seq[a:b] for a, b in zip(cuts, cuts[1:])]
contigs = [("P0", P[0]), ("P1_rc", rc(P[1])), ("P2_P5", P[2] + P[5]), ("P3", P[3]),
           ("P4", P[4]), ("P6_rc", rc(P[6])), ("P7", P[7])]
write("chimera", contigs)


def nx(lengths, total, frac):
    c = 0
    for k, x in enumerate(sorted(lengths, reverse=True), 1):
        c += x
        if c >= total * frac:
            return x, k


clen = [len(x) for _, x in contigs]
truth = {
    "reference_length": L,
    "chimera_cuts": cuts[1:-1],
    "self": {"# contigs": 1, "# misassemblies": 0, "# mismatches": 0, "# indels": 0,
             "Genome fraction (%)": "100.000", "Duplication ratio": "1.000", "NGA50": L,
             "# unaligned contigs": "0 + 0 part"},
    "planted": {"# mismatches": 50, "# indels": 20, "# indels (<= 5 bp)": 20, "# indels (> 5 bp)": 0,
                "Indels length": sum(n for _, k, n in edits if k != "sub"),
                "# misassemblies": 0, "# contigs": 1,
                "length": len(planted)},
    "chimera": {"# contigs": len(contigs), "# misassemblies": 1, "# c. relocations": 1,
                "# c. inversions": 0, "# c. translocations": 0, "# misassembled contigs": 1,
                "Misassembled contigs length": len(P[2]) + len(P[5]),
                "# mismatches": 0, "# indels": 0, "Genome fraction (%)": "100.000",
                "Total length": L, "N50": nx(clen, L, 0.5)[0], "L50": nx(clen, L, 0.5)[1],
                # the chimera aligns as its eight source pieces
                "NA50": nx([len(p) for p in P], L, 0.5)[0], "LA50": nx([len(p) for p in P], L, 0.5)[1],
                "Largest alignment": max(len(p) for p in P)},
}
with open(os.path.join(out, "truth.json"), "w") as fh:
    json.dump(truth, fh, indent=1, sort_keys=True)
print(json.dumps(truth, sort_keys=True))
