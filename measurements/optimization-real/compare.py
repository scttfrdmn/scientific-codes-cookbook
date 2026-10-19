# Compare two chips' results.jsonl: what is a fixed point (an optimum both reached) and what is a
# path (node counts, which instances finish inside the time limit, the incumbent at the limit).
#
#   python3 -I compare.py <chip A dir> <chip B dir>
import json, math, os, sys


def load(d):
    return {(r["kind"], r["solver"], r["instance"]): r for r in map(json.loads, open(os.path.join(d, "results.jsonl")))}


A, B = load(sys.argv[1]), load(sys.argv[2])
FIN = {"Optimal", "Infeasible", "optimal", "infeasible"}


def rel(a, b):
    return abs(a - b) / max(abs(a), abs(b), 1e-9)


for kind, solvers in (("lp", ("highs", "scip", "cbc")), ("mip", ("highs", "scip"))):
    for s in solvers:
        ks = sorted(k for k in A if k[0] == kind and k[1] == s and k in B)
        if not ks:
            continue
        both = [k for k in ks if A[k].get("status") in FIN and B[k].get("status") in FIN]
        fa = sum(A[k].get("status") in FIN for k in ks)
        fb = sum(B[k].get("status") in FIN for k in ks)
        only_a = [k[2] for k in ks if A[k].get("status") in FIN and B[k].get("status") not in FIN]
        only_b = [k[2] for k in ks if B[k].get("status") in FIN and A[k].get("status") not in FIN]
        same_status = sum(A[k].get("status") == B[k].get("status") for k in both)
        pv = [(k, A[k].get("pb"), B[k].get("pb")) for k in both if A[k].get("pb") is not None and B[k].get("pb") is not None]
        ident = sum(a == b for _, a, b in pv)
        worst = max(pv, key=lambda t: rel(t[1], t[2]), default=None)
        nodes = [k for k in both if "nodes" in A[k] and "nodes" in B[k]]
        same_nodes = sum(A[k]["nodes"] == B[k]["nodes"] for k in nodes)
        ta = sum(A[k]["wall_s"] for k in both)
        tb = sum(B[k]["wall_s"] for k in both)
        print("%s %-5s finished %d / %d (A) and %d / %d (B); both %d" % (kind, s, fa, len(ks), fb, len(ks), len(both)))
        print("    same status on both-finished %d / %d" % (same_status, len(both)))
        print("    objective bit-identical %d / %d; worst rel diff %s" % (
            ident, len(pv), "%.2e (%s: %.15g vs %.15g)" % (rel(worst[1], worst[2]), worst[0][2], worst[1], worst[2]) if worst else "-"))
        if nodes:
            print("    node count identical %d / %d" % (same_nodes, len(nodes)))
        print("    summed wall on both-finished: A %.0f s, B %.0f s, B/A %.3f" % (ta, tb, tb / ta if ta else float("nan")))
        if only_a:
            print("    finished on A only: %s" % " ".join(only_a))
        if only_b:
            print("    finished on B only: %s" % " ".join(only_b))
