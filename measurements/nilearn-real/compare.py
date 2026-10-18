"""Compare two chips' nilearn-real outputs: region signals by sha256, the tangent mean, predictions,
and every scored value side by side.

    python3 compare.py <dir A> <dir B>
"""
import csv
import struct
import sys

a, b = sys.argv[1], sys.argv[2]


def tsv(p):
    return list(csv.DictReader(open(p, newline=""), delimiter="\t"))


sa = {r["id"]: r for r in tsv(f"{a}/subjects.tsv")}
sb = {r["id"]: r for r in tsv(f"{b}/subjects.tsv")}
same = sum(sa[k]["sha_signals"] == sb[k]["sha_signals"] for k in sa)
print(f"region signals bit-identical: {same} of {len(sa)} subjects")


def npy(p):
    """float64 C-order .npy without numpy, so this runs anywhere."""
    raw = open(p, "rb").read()
    hl = struct.unpack("<H", raw[8:10])[0]
    body = raw[10 + hl:]
    return struct.unpack("<%dd" % (len(body) // 8), body)


ga, gb = npy(f"{a}/tangent-mean.npy"), npy(f"{b}/tangent-mean.npy")
d = max(abs(x - y) for x, y in zip(ga, gb))
m = max(abs(x) for x in ga)
nid = sum(x == y for x, y in zip(ga, gb))
print(f"tangent mean: max |diff| {d:.3e}, rel {d / m:.3e}, bit-identical entries {nid} of {len(ga)}")
pa, pb = tsv(f"{a}/predictions.tsv"), tsv(f"{b}/predictions.tsv")
for col in ("pred_10fold", "pred_loso"):
    print(f"{col}: identical for {sum(x[col] == y[col] for x, y in zip(pa, pb))} of {len(pa)} subjects")
print("\nscored values that differ:")
ka = {r["claim"]: r["value"] for r in tsv(f"{a}/score.tsv")}
kb = {r["claim"]: r["value"] for r in tsv(f"{b}/score.tsv")}
for k in ka:
    if ka[k] != kb.get(k):
        print(f"  {k}: {ka[k]}  |  {kb.get(k)}")
