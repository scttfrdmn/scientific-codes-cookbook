"""Compare two MEGAHIT contig sets, ignoring contig names, order and orientation.

    python3 -u compare.py <label_a> <a.fa> <label_b> <b.fa>

Prints one summary line, then a classification of the contigs found in only one of the two:
for each such contig in A, the closest contig of the SAME length in B's leftovers, by Hamming
distance in either orientation, or by rotation (a circular contig started at another base).
"""
import collections
import sys

COMP = bytes.maketrans(b"ACGTNacgtn", b"TGCANtgcan")


def rc(s):
    return s.translate(COMP)[::-1]


def read(p):
    seqs, cur = [], []
    with open(p, "rb") as fh:
        for line in fh:
            if line.startswith(b">"):
                if cur:
                    seqs.append(b"".join(cur))
                cur = []
            else:
                cur.append(line.strip())
    if cur:
        seqs.append(b"".join(cur))
    return collections.Counter(min(s, rc(s)) for s in seqs)


def ham(a, b):
    return sum(x != y for x, y in zip(a, b))


la, pa, lb, pb = sys.argv[1:5]
A, B = read(pa), read(pb)
onlyA = A - B
onlyB = B - A
shared = sum((A & B).values())
print("pair %s %s contigs %d %d shared %d only_%s %d (%d bp) only_%s %d (%d bp)" % (
    la, lb, sum(A.values()), sum(B.values()), shared,
    la, sum(onlyA.values()), sum(len(s) * n for s, n in onlyA.items()),
    lb, sum(onlyB.values()), sum(len(s) * n for s, n in onlyB.items())))
byLen = collections.defaultdict(list)
for s in onlyB.elements():
    byLen[len(s)].append(s)
kinds = collections.Counter()
hams = collections.Counter()
for s in sorted(onlyA.elements(), key=len):
    cands = byLen.get(len(s), [])
    best, how = None, None
    for t in cands:
        if s in t + t or rc(s) in t + t:
            best, how = t, "rotation"
            break
        d = min(ham(s, t), ham(s, rc(t)))
        if best is None or d < how:
            best, how = t, d
    if best is None:
        kinds["no_equal_length_partner"] += 1
        continue
    cands.remove(best)
    if how == "rotation":
        kinds["rotation"] += 1
    else:
        kinds["equal_length_hamming"] += 1
        hams[how if how <= 5 else ">5"] += 1
print("  only_%s classified: %s" % (la, " ".join("%s=%d" % kv for kv in sorted(kinds.items()))))
print("  hamming distance of equal-length partners: %s" % " ".join("%s:%d" % (k, v) for k, v in sorted(hams.items(), key=lambda kv: str(kv[0]))))
print("  only_%s left unpaired: %d" % (lb, sum(len(v) for v in byLen.values())))
