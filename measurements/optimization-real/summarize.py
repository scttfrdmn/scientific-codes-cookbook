# Check every record in results.jsonl against the published references and write score.tsv,
# lp.tsv and mip.tsv. Any failed assertion prints FAIL and the script exits 1.
#
#   python3 -u summarize.py <data dir> <out dir>
#
# LP (Netlib, three solvers): each solver's own optimality certificate; the objective against
# SoPlex's committed 32-digit optimum, with SoPlex's own harness rule (check/evaluation.py:
# |v-ref| / max(|v|,|ref|,1e-6) <= 1e-6); the three solvers against each other by the same rule.
# netlib's 1989 readme values are compared and reported, not asserted (its own notes say other
# double-precision solvers got different values for 23 of these problems).
#
# MIP (MIPLIB 2017 benchmark, two solvers): the MIPLIB test engine's rules (scripts/parse.awk):
# a solve that finished must match the .solu value (|ref| <= 100: 1e-4 absolute, else 1e-6
# relative); a solver may not call an instance with a known solution infeasible; every
# reported solution passes the solution-checker rule (solve.py). Added here: a run stopped
# by the time limit must still BRACKET the known optimum -- its dual bound may not cut it off
# and its incumbent may not beat it -- by the same tolerance. An incumbent that does beat a
# proven optimum is only possible by using the solver's feasibility tolerance (a solution with
# zero violation cannot), so it fails unless the checker measured a nonzero violation, and is
# reported either way. Where both solvers finish, they must agree by that tolerance.
import json, math, os, re, sys
from collections import defaultdict

DATA, OUT = sys.argv[1], sys.argv[2]
fails = []


def fail(msg):
    fails.append(msg)
    print("FAIL: " + msg, flush=True)


def key(name):
    return re.sub(r"[^a-z0-9]", "", name.lower())


recs = [json.loads(l) for l in open(os.path.join(OUT, "results.jsonl"))]
by = defaultdict(dict)
refsols = defaultdict(list)
for r in recs:
    if r["kind"] == "ref":
        refsols[r["instance"]].append(r)
        continue
    if r["solver"] in by[(r["kind"], r["instance"])]:
        fail("duplicate record %s %s %s" % (r["kind"], r["solver"], r["instance"]))
    by[(r["kind"], r["instance"])][r["solver"]] = r

# ------------------------------------------------------------------------------------------ LP
exact = {}
for l in open(os.path.join(DATA, "ref/soplex-quick.solu")):
    f = l.split()
    if len(f) == 3 and f[0] == "=opt=" and f[2] not in ("infeasible", "unbounded"):
        exact[key(f[1])] = float(f[2])
readme = {}
for l in open(os.path.join(DATA, "ref/netlib-readme")):
    m = re.match(r"^([A-Z0-9][A-Z0-9.\-]*)\s+\d+\s+\d+\s+\d+\s+(?:\d+|\(see NOTES\))\s+(?:[BR]+\s+)?(-?\d\.\d+E[-+]\d\d)", l)
    if m and key(m.group(1)) not in readme:
        readme[key(m.group(1))] = (float(m.group(2)), len(m.group(2).split("E")[0].replace("-", "").replace(".", "")))


def soplex_rel(v, ref):
    return abs(v - ref) / max(abs(v), abs(ref), 1e-6)


lp_inst = sorted(i for (k, i) in by if k == "lp")
lprows, lp_noref, readme_off, lp_spread_max, cbc_cleanups, offsets = [], [], [], 0.0, [], []
for inst in lp_inst:
    rs = by[("lp", inst)]
    ref = exact.get(key(inst))
    if ref is None:
        lp_noref.append(inst)
        continue
    for s in ("highs", "scip", "cbc"):
        if s not in rs:
            fail("lp %s: no %s record" % (inst, s))
    rs = {s: r for s, r in rs.items() if "error" not in r or fail("lp %s %s: %s" % (inst, s, r["error"]))}
    h, sc, cb = rs.get("highs", {}), rs.get("scip", {}), rs.get("cbc", {})
    if h.get("status") != "Optimal":
        fail("lp %s highs status %s" % (inst, h.get("status")))
    if sc.get("status") != "optimal":
        fail("lp %s scip status %s" % (inst, sc.get("status")))
    if cb.get("status") != "optimal":
        fail("lp %s cbc status %s" % (inst, cb.get("status")))
    # each solver's own certificate
    for k in ("max_primal_infeasibility", "max_dual_infeasibility"):
        v = h.get(k, float("nan"))
        if not (v <= 1e-7):   # HiGHS's own feasibility tolerances; NaN fails too
            fail("lp %s highs %s %.3e" % (inst, k, v))
    if sc.get("pb") is not None and sc.get("db") is not None and soplex_rel(sc["pb"], sc["db"]) > 1e-9:
        fail("lp %s scip primal %.12g != dual %.12g" % (inst, sc["pb"], sc["db"]))
    fs = cb.get("cbc_final_state") or ""
    if cb.get("cbc_cleanup"):
        cbc_cleanups.append(inst)
    if fs.startswith("After Postsolve"):
        cdi, cpi = cb.get("cbc_dual_infeas"), cb.get("cbc_primal_infeas")
        if not (cdi <= 1e-7 and cpi <= 1e-7):
            fail("lp %s cbc postsolve infeasibility dual %.3e primal %.3e" % (inst, cdi, cpi))
    elif re.match(r"^\d+\s+Obj\s", fs):
        if "inf" in fs.lower():
            fail("lp %s cbc declared optimal from an infeasible iterate: %r" % (inst, fs))
    elif "needs cleaning up" not in fs:
        fail("lp %s cbc: no certificate before its final Optimal (%r)" % (inst, fs))
    # netlib's and SoPlex's values are the objective WITHOUT an MPS objective constant; the solvers
    # report it included. Compare like with like: subtract the constant HiGHS read from the file.
    off = h.get("obj_offset", 0.0) or 0.0
    if off:
        offsets.append((inst, off))
    vals = {}
    for s, r in (("highs", h), ("scip", sc), ("cbc", cb)):
        if r.get("pb") is None:
            fail("lp %s %s: no objective" % (inst, s))
            continue
        vals[s] = r["pb"] - off
        e = soplex_rel(vals[s], ref)
        if e > 1e-6:
            fail("lp %s %s objective %.12g (constant %g removed) vs exact %.12g, rel %.2e > 1e-6" % (inst, s, vals[s], off, ref, e))
    if len(vals) == 3:
        sp = max(soplex_rel(a, b) for a in vals.values() for b in vals.values())
        lp_spread_max = max(lp_spread_max, sp)
        if sp > 1e-6:
            fail("lp %s solvers disagree, rel %.2e" % (inst, sp))
    rd = readme.get(key(inst))
    rd_rel = soplex_rel(rd[0], ref) if rd else float("nan")
    if rd and rd_rel > 0.5 * 10 ** (1 - rd[1]):   # beyond the rounding of the digits it printed
        readme_off.append((inst, rd[0], ref, rd_rel))
    lprows.append([inst, "%.16g" % ref] + ["%.12g" % vals.get(s, float("nan")) for s in ("highs", "scip", "cbc")]
                  + ["%.2e" % max(soplex_rel(v, ref) for v in vals.values()) if vals else "nan",
                     "%.11g" % rd[0] if rd else "", "%.2e" % rd_rel,
                     "%.3f" % sum(rs[s].get("solve_s", 0) for s in rs)])
with open(os.path.join(OUT, "lp.tsv"), "w") as fh:
    fh.write("instance\texact\thighs\tscip\tcbc\tmax_rel_vs_exact\tnetlib_readme\treadme_rel\tsolve_s_3solvers\n")
    for row in lprows:
        fh.write("\t".join(row) + "\n")

# ----------------------------------------------------------------------------------------- MIP
solu = {}
for l in open(os.path.join(DATA, "miplib/miplib2017-v37.solu")):
    f = l.split()
    if len(f) >= 2:
        solu[f[1]] = (f[0].strip("="), float(f[2]) if len(f) > 2 else None)


# Where MIPLIB's published solutions are staged (binkar10_1), they went through the same checker.
# A .solu value that only a tolerance-feasible solution reaches is not the instance's exact optimum:
# the reference becomes the best published solution with NO violation, and the .solu value is
# reported. The rule is checked on every run, so it lapses by itself if the archive changes.
EXACT = 1e-12
ref_note = {}
for inst, sols in sorted(refsols.items()):
    for r in sols:
        if r.get("check") != "done":
            fail("ref %s %s: not checkable (%s)" % (inst, r.get("solution_file"), r.get("error") or r.get("check")))
    ok = [r for r in sols if r.get("check") == "done"]
    exact_ok = [r for r in ok if max(r["int_viol"], r["row_viol"], r["bound_viol"]) <= EXACT]
    for r in ok:
        print("REPORT: ref %s %s objective %.12g (file says %.12g), violations int %.2e row %.2e bound %.2e" % (
            inst, r["solution_file"], r["obj_recomputed"], r["pb"], r["int_viol"], r["row_viol"], r["bound_viol"]), flush=True)
    ref_note[inst] = (ok, exact_ok)


def miplib_ok(v, ref):
    return abs(v - ref) <= 1e-4 if abs(ref) <= 100 else abs(v - ref) / abs(ref) <= 1e-6


def slack(ref):
    return 1e-4 if abs(ref) <= 100 else 1e-6 * abs(ref)


FINISHED = {"highs": {"Optimal", "Infeasible"}, "scip": {"optimal", "infeasible"}}
INFEAS = {"highs": {"Infeasible"}, "scip": {"infeasible"}}
mip_inst = sorted(i for (k, i) in by if k == "mip")
counts = defaultdict(int)
miprows = []
for inst in mip_inst:
    rs = by[("mip", inst)]
    st, ref = solu.get(inst, ("unknown", None))
    if inst in ref_note and ref is not None:
        ok, exact_ok = ref_note[inst]
        reaches = [r for r in ok if miplib_ok(r["obj_recomputed"], ref)]
        if reaches and not any(r in exact_ok for r in reaches) and exact_ok:
            best = min(exact_ok, key=lambda r: (1 if r.get("sense", "min") == "min" else -1) * r["obj_recomputed"])
            print("REPORT: mip %s: .solu %.12g is reached only by a tolerance-feasible solution (%s); "
                  "the reference is the best exactly feasible published solution, %.12g (%s)" % (
                      inst, ref, " ".join(r["solution_file"] for r in reaches), best["obj_recomputed"], best["solution_file"]), flush=True)
            counts["reference_corrected"] += 1
            ref = best["obj_recomputed"]
    row = [inst, st, "%.12g" % ref if ref is not None else ""]
    pbs = {}
    for s in ("highs", "scip"):
        r = rs.get(s)
        if r is None:
            fail("mip %s: no %s record" % (inst, s))
            row += ["missing"] * 6
            continue
        if "error" in r:
            counts[s + "_error"] += 1
            print("REPORT: mip %s %s error: %s (rss %s MiB, %.0f s)" % (inst, s, r["error"], r.get("maxrss_mib"), r["wall_s"]), flush=True)
            row += ["error", "", "", "", "%.1f" % r["wall_s"], str(r.get("maxrss_mib"))]
            continue
        status, pb, db, sense = r.get("status"), r.get("pb"), r.get("db"), r.get("sense", "min")
        fin = status in FINISHED[s]
        counts[s + ("_finished" if fin else "_stopped")] += 1
        sign = 1.0 if sense == "min" else -1.0
        if r.get("n_sols", 0) > 0:
            counts[s + "_with_solution"] += 1
            if r.get("check") != "done":
                fail("mip %s %s: solution not checkable (%s)" % (inst, s, r.get("check")))
            else:
                for k in ("int_viol", "row_viol", "bound_viol"):
                    if not (r[k] <= 1e-4):
                        fail("mip %s %s: solution checker %s %.3e > 1e-4" % (inst, s, k, r[k]))
                if not (abs(r["obj_recomputed"] - pb) <= 1e-4 * max(r["obj_scale"], abs(pb))):
                    fail("mip %s %s: reported objective %.12g, solution evaluates to %.12g" % (inst, s, pb, r["obj_recomputed"]))
        if st in ("opt", "best") and status in INFEAS[s]:
            fail("mip %s %s: claims infeasible, MIPLIB has a solution %.12g" % (inst, s, ref))
        if st == "inf" and r.get("n_sols", 0) > 0:
            print("REPORT: mip %s %s: a checker-feasible solution on an =inf= instance" % (inst, s), flush=True)
        if st in ("opt", "best") and ref is not None:
            if fin and st == "opt" and pb is not None and not miplib_ok(pb, ref):
                fail("mip %s %s: finished at %.12g, MIPLIB optimum %.12g" % (inst, s, pb, ref))
            if db is not None and math.isfinite(db) and sign * (db - ref) > slack(ref):
                fail("mip %s %s: dual bound %.12g cuts off the known solution %.12g" % (inst, s, db, ref))
            if st == "opt" and pb is not None and sign * (ref - pb) > slack(ref):
                viol = max(r.get("int_viol", 0.0), r.get("row_viol", 0.0), r.get("bound_viol", 0.0))
                msg = "mip %s %s (%s): incumbent %.12g beats the proven optimum %.12g by %.2e relative, " \
                      "with checker violation %.2e" % (inst, s, status, pb, ref, abs(ref - pb) / abs(ref), viol)
                if viol > EXACT:
                    counts[s + "_tolerance_incumbent"] += 1
                    print("REPORT: " + msg, flush=True)
                else:
                    fail(msg + " -- an exactly feasible solution better than a proven optimum")
            if st == "best" and pb is not None and sign * (ref - pb) > slack(ref):
                print("REPORT: mip %s %s: incumbent %.12g improves on MIPLIB's best known %.12g" % (inst, s, pb, ref), flush=True)
        if fin and status not in INFEAS[s] and st == "opt":
            counts[s + "_solved_opt"] += 1
        if fin and status in INFEAS[s] and st == "inf":
            counts[s + "_proved_inf"] += 1
        if fin and pb is not None:
            pbs[s] = pb
        gap = (abs(pb - db) / max(abs(pb), abs(db), 1e-9)) if (pb is not None and db is not None and math.isfinite(db)) else float("nan")
        row += [str(status), "%.12g" % pb if pb is not None else "", "%.12g" % db if db is not None else "",
                "%.3e" % gap, "%.1f" % r["wall_s"], str(r.get("maxrss_mib"))]
    if len(pbs) == 2 and st == "opt":
        counts["both_finished"] += 1
        if not miplib_ok(pbs["highs"], pbs["scip"]):
            fail("mip %s: HiGHS %.12g and SCIP %.12g both finished and disagree" % (inst, pbs["highs"], pbs["scip"]))
    miprows.append(row)
with open(os.path.join(OUT, "mip.tsv"), "w") as fh:
    hdr = ["instance", "miplib_status", "miplib_value"]
    for s in ("highs", "scip"):
        hdr += [s + "_" + c for c in ("status", "primal", "dual", "gap", "wall_s", "rss_mib")]
    fh.write("\t".join(hdr) + "\n")
    for row in miprows:
        fh.write("\t".join(row) + "\n")

with open(os.path.join(OUT, "score.tsv"), "w") as fh:
    def w(k, v):
        fh.write("%s\t%s\n" % (k, v))
        print("%s\t%s" % (k, v), flush=True)
    w("lp_instances", len(lp_inst))
    w("lp_with_exact_reference", len(lprows))
    w("lp_without_reference", ",".join(lp_noref) or "-")
    w("lp_max_three_solver_spread", "%.2e" % lp_spread_max)
    w("lp_objective_constant_removed", " ".join("%s:%g" % o for o in offsets) or "-")
    w("lp_cbc_postsolve_cleanups", "%d %s" % (len(cbc_cleanups), " ".join(cbc_cleanups)))
    w("lp_max_rel_vs_exact", max((float(r[5]) for r in lprows), default=float("nan")))
    w("lp_readme_beyond_its_printed_digits", len(readme_off))
    for inst, rv, ref, e in readme_off:
        w("  readme_off", "%s readme %.11g exact %.16g rel %.2e" % (inst, rv, ref, e))
    w("mip_instances", len(mip_inst))
    for k in sorted(counts):
        w("mip_" + k, counts[k])
    w("fails", len(fails))

if fails:
    print("%d FAILURES" % len(fails), flush=True)
    sys.exit(1)
print("OPTIMIZATION REAL OK", flush=True)
