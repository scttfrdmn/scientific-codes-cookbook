"""Compare two or more runs' jobs.jsonl field by field: which per-chromosome values are identical
across chips, and how far apart the rest are.
    python3 crosschip.py <ref jobs.jsonl> <other jobs.jsonl> ..."""
import json
import sys

SKIP = {"wall_s", "maxrss_gib", "finished_s", "ancestry_s", "mutations_s", "structure_s",
        "tskit_s", "allel_s"}


def load(p):
    return {(r["job"], r["chrom"]): r for r in map(json.loads, open(p))}


def flat(d, pre=""):
    for k, v in d.items():
        if k in SKIP:
            continue
        if isinstance(v, dict):
            yield from flat(v, pre + k + ".")
        elif isinstance(v, list):
            for i, x in enumerate(v):
                yield "%s%s[%d]" % (pre, k, i), x
        else:
            yield pre + k, v


ref = load(sys.argv[1])
for other in sys.argv[2:]:
    o = load(other)
    print("== %s vs %s" % (sys.argv[1], other))
    for kind in ("vcf", "sim"):
        same = diff = 0
        worst = {}
        for key in sorted(k for k in ref if k[0] == kind):
            a, b = dict(flat(ref[key])), dict(flat(o[key]))
            for f in a:
                if a[f] == b.get(f):
                    same += 1
                    continue
                diff += 1
                base = f.split("[")[0]
                if isinstance(a[f], float) and isinstance(b.get(f), float) and a[f] != 0:
                    rel = abs(a[f] - b[f]) / abs(a[f])
                    worst[base] = max(worst.get(base, 0), rel)
                else:
                    worst[base] = "differs (%s)" % type(a[f]).__name__
        print("%s: %d values identical, %d differ" % (kind, same, diff))
        for f, w in sorted(worst.items()):
            print("   %-28s %s" % (f, "max rel %.2e" % w if isinstance(w, float) else w))
