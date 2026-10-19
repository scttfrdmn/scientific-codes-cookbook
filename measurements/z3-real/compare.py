# Compare two chips' results.jsonl. The verdict is a fixed point: where both chips decide a
# benchmark, they must agree (exit 1 otherwise). Everything else is a path and is reported:
# Z3's deterministic work counter (rlimit), conflicts, the core it returned, which benchmarks
# finish inside the limit, and the summed solve time on the benchmarks both finish.
#
#   python3 -I compare.py <dir A> <dir B>
import collections, json, sys

A, B = ({json.loads(l)["bench"]: json.loads(l) for l in open(d + "/results.jsonl")} for d in sys.argv[1:3])
bad = 0
for lg in ("QF_IDL", "QF_RDL", "QF_LRA"):
    ks = sorted(k for k in A if A[k]["logic"] == lg and k in B)
    if not ks:
        continue
    dec = lambda r: r.get("verdict") in ("sat", "unsat")
    both = [k for k in ks if dec(A[k]) and dec(B[k])]
    dis = [k for k in both if A[k]["verdict"] != B[k]["verdict"]]
    bad += len(dis)
    c = collections.Counter()
    for k in both:
        c["rlimit_identical"] += A[k].get("rlimit") == B[k].get("rlimit")
        c["conflicts_identical"] += A[k].get("conflicts") == B[k].get("conflicts")
        if A[k]["verdict"] == "unsat" and "core" in A[k] and "core" in B[k]:
            c["cores_compared"] += 1
            c["core_identical"] += A[k]["core"] == B[k]["core"]
    ta = sum(A[k]["solve_s"] for k in both)
    tb = sum(B[k]["solve_s"] for k in both)
    onlyA = [k for k in ks if dec(A[k]) and not dec(B[k])]
    onlyB = [k for k in ks if dec(B[k]) and not dec(A[k])]
    print("%s: %d in both; decided A %d, B %d, both %d; verdicts disagree %d" % (
        lg, len(ks), sum(dec(A[k]) for k in ks), sum(dec(B[k]) for k in ks), len(both), len(dis)))
    print("  rlimit identical %d/%d, conflicts identical %d/%d, cores identical %d/%d" % (
        c["rlimit_identical"], len(both), c["conflicts_identical"], len(both), c["core_identical"], c["cores_compared"]))
    print("  summed solve time on both-decided: A %.1f s, B %.1f s, B/A %.3f" % (ta, tb, tb / ta if ta else 0))
    print("  decided only by A (%d): %s" % (len(onlyA), " ".join(onlyA[:12]) + (" ..." if len(onlyA) > 12 else "")))
    print("  decided only by B (%d): %s" % (len(onlyB), " ".join(onlyB[:12]) + (" ..." if len(onlyB) > 12 else "")))
    for k in dis:
        print("  DISAGREE %s: A %s, B %s" % (k, A[k]["verdict"], B[k]["verdict"]))
if bad:
    print("FAIL: %d verdict disagreement(s)" % bad)
    sys.exit(1)
print("verdicts agree wherever both chips decide")
