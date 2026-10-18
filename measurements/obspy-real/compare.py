"""Compare two chips' channels.tsv / taup_picks.tsv column by column.

    python3 compare.py results/arm64 results/x86_64
"""
import csv
import sys


def load(d, f):
    with open("%s/%s" % (d, f)) as fh:
        return list(csv.DictReader(fh, delimiter="\t"))


a_dir, b_dir = sys.argv[1], sys.argv[2]
A, B = load(a_dir, "channels.tsv"), load(b_dir, "channels.tsv")
assert [r["file"] for r in A] == [r["file"] for r in B], "different channel sets"
print("channels", len(A))
for col in A[0]:
    if col == "file":
        continue
    same = sum(a[col] == b[col] for a, b in zip(A, B))
    line = "%-24s identical %4d / %d" % (col, same, len(A))
    if same < len(A):
        worst = 0.0
        for a, b in zip(A, B):
            try:
                x, y = float(a[col]), float(b[col])
            except ValueError:
                continue
            if x != y:
                worst = max(worst, abs(x - y) / max(abs(x), abs(y), 1e-300))
        line += "   max rel diff %.3e" % worst
    print(line)
TA, TB = load(a_dir, "taup_picks.tsv"), load(b_dir, "taup_picks.tsv")
print("taup rows %d / %d, identical %d" % (len(TA), len(TB), sum(a == b for a, b in zip(TA, TB))))
