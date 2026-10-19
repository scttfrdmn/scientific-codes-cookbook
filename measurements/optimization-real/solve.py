# One solve: one solver on one instance, in its own process, writing one JSON record.
#
#   python3 -u solve.py <highs|scip|cbc> <lp|mip> <file.mps.gz> <time limit s> <out.json>
#   python3 -u solve.py solfile ref <file.mps.gz> <published.sol.gz> <out.json>
#
# The second form puts a PUBLISHED solution (MIPLIB's solution archive) through the same checker.
#
# The gzipped MPS is expanded to /tmp/work for the solve and removed afterwards, so all three
# solvers read the same plain bytes and none depends on its own zlib support.
#
# Settings copy the MIPLIB 2017 test engine (scripts/run.sh, run_scip.sh): MIP gap 0, wall-clock
# time limit, SCIP memory limit 8192 MB, one thread. Every reported solution is then checked
# against the ORIGINAL model with the engine's solution-checker rule (checker/src/model.cpp):
# integrality 1e-4 absolute; bounds 1e-4 * max(1,|bound|,|x|); each row 1e-4 * max(1, positive
# activity, |negative activity|, |side|); objective 1e-4 * max(1, obj+, |obj-|, |reported|).
# The checker is exact rational arithmetic; this is float64, whose error (~1e-16 of the same
# magnitudes) is twelve orders below the tolerance.
import gzip, json, math, os, re, shutil, subprocess, sys, time

solver, kind, gz, outp = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[5]
tl = float(sys.argv[4]) if solver != "solfile" else 0.0
inst = os.path.basename(gz)[:-len(".mps.gz")]
os.makedirs("/tmp/work", exist_ok=True)
# one work file per job, named after its record: jobs on the same instance run concurrently
path = "/tmp/work/%s.mps" % os.path.basename(outp)[:-len(".json")]
R = {"solver": solver, "kind": kind, "instance": inst}
INF = float("inf")


def dump():
    with open(outp, "w") as fh:
        json.dump(R, fh)


def fnum(v):
    v = float(v)
    return v if math.isfinite(v) and abs(v) < 1e20 else (INF if v > 0 else -INF)


def check(x_by_index):
    """The MIPLIB solution-checker rule, on HiGHS's parse of the same MPS bytes."""
    import highspy
    import numpy as np
    h = highspy.Highs()
    h.setOptionValue("output_flag", False)
    h.readModel(path)
    lp = h.getLp()
    n, m = lp.num_col_, lp.num_row_
    x = x_by_index(lp)
    if x is None:
        return {"check": "names-unmatched"}
    x = np.asarray(x, dtype=float)
    a = lp.a_matrix_
    start, index, value = np.asarray(a.start_), np.asarray(a.index_), np.asarray(a.value_)
    if int(a.format_) != int(highspy.MatrixFormat.kColwise):
        return {"check": "rowwise-matrix-unhandled"}
    cols = np.repeat(np.arange(n), np.diff(start))
    prod = value * x[cols]
    pos = np.bincount(index, weights=np.where(prod > 0, prod, 0.0), minlength=m)
    neg = np.bincount(index, weights=np.where(prod < 0, prod, 0.0), minlength=m)
    act = pos + neg
    rl, ru = np.asarray(lp.row_lower_), np.asarray(lp.row_upper_)
    scale_r = np.maximum.reduce([np.ones(m), pos, -neg])
    with np.errstate(invalid="ignore"):
        lo_v = np.where(np.isfinite(rl), (rl - act) / np.maximum(scale_r, np.where(np.isfinite(rl), abs(rl), 1)), 0.0)
        up_v = np.where(np.isfinite(ru), (act - ru) / np.maximum(scale_r, np.where(np.isfinite(ru), abs(ru), 1)), 0.0)
    row_viol = float(max(0.0, lo_v.max(initial=0.0), up_v.max(initial=0.0)))
    cl, cu = np.asarray(lp.col_lower_), np.asarray(lp.col_upper_)
    integ = list(lp.integrality_) if len(lp.integrality_) else []
    kinds = [str(t).split(".")[-1] for t in integ] if integ else ["kContinuous"] * n
    semi = np.array([k.startswith("kSemi") for k in kinds], dtype=bool)
    with np.errstate(invalid="ignore"):
        lb_v = np.where(np.isfinite(cl), (cl - x) / np.maximum.reduce([np.ones(n), np.where(np.isfinite(cl), abs(cl), 1), abs(x)]), 0.0)
        ub_v = np.where(np.isfinite(cu), (x - cu) / np.maximum.reduce([np.ones(n), np.where(np.isfinite(cu), abs(cu), 1), abs(x)]), 0.0)
    lb_v[semi & (abs(x) <= 1e-4)] = 0.0   # a semicontinuous variable may sit at zero
    bound_viol = float(max(0.0, lb_v.max(initial=0.0), ub_v.max(initial=0.0)))
    intmask = np.array([k in ("kInteger", "kSemiInteger") for k in kinds], dtype=bool)
    int_viol = float(abs(x[intmask] - np.round(x[intmask])).max(initial=0.0))
    c = np.asarray(lp.col_cost_) * x
    obj = float(c.sum() + lp.offset_)
    op, on = float(c[c > 0].sum()), float(-c[c < 0].sum())
    return {"check": "done", "n_col": n, "n_row": m, "n_int": int(intmask.sum()),
            "row_viol": row_viol, "bound_viol": bound_viol, "int_viol": int_viol,
            "obj_recomputed": obj, "obj_scale": max(1.0, op, on)}


def run_highs():
    import highspy
    R["version"] = str(getattr(highspy, "__version__", "unknown"))
    h = highspy.Highs()
    h.setOptionValue("threads", 1)
    h.setOptionValue("time_limit", tl)
    h.setOptionValue("mip_rel_gap", 0.0)
    h.readModel(path)
    t0 = time.time()
    h.run()
    R["solve_s"] = time.time() - t0
    R["status"] = str(h.modelStatusToString(h.getModelStatus()))
    info = h.getInfo()
    lp = h.getLp()
    R["sense"] = "max" if int(lp.sense_) == int(highspy.ObjSense.kMaximize) else "min"
    R["obj_offset"] = float(lp.offset_)   # an MPS objective constant (an RHS on the objective row)
    has = int(info.primal_solution_status) == 2
    R["n_sols"] = 1 if has else 0
    R["pb"] = fnum(info.objective_function_value) if has else None
    if kind == "mip":
        R["db"] = fnum(info.mip_dual_bound)
        R["nodes"] = int(info.mip_node_count)
    for k in ("max_primal_infeasibility", "max_dual_infeasibility", "max_complementarity_violation"):
        R[k] = float(getattr(info, k, float("nan")))
    if has:
        x = list(h.getSolution().col_value)
        R.update(check(lambda _lp: x))
        R["obj_reported"] = R["pb"]


def run_scip():
    from pyscipopt import Model
    import pyscipopt
    R["version"] = str(getattr(pyscipopt, "__version__", "unknown"))
    m = Model()
    for k, v in (("limits/time", tl), ("limits/memory", 8192.0), ("limits/gap", 0.0), ("timing/clocktype", 2),
                 ("display/freq", 10000), ("display/verblevel", 4)):
        m.setParam(k, v)
    m.readProblem(path)
    t0 = time.time()
    m.optimize()
    R["solve_s"] = time.time() - t0
    R["status"] = str(m.getStatus())
    R["sense"] = "max" if str(m.getObjectiveSense()).startswith("max") else "min"
    R["n_sols"] = int(m.getNSols())
    R["pb"] = fnum(m.getPrimalbound()) if R["n_sols"] > 0 else None
    R["db"] = fnum(m.getDualbound())
    R["nodes"] = int(m.getNNodes())
    if R["n_sols"] > 0:
        sol = m.getBestSol()
        vals = {v.name: m.getSolVal(sol, v) for v in m.getVars(transformed=False)}

        def by_name(lp):
            names = list(lp.col_names_)
            if len(names) != lp.num_col_ or any(nm not in vals for nm in names):
                return None
            return [vals[nm] for nm in names]
        R.update(check(by_name))
        R["obj_reported"] = R["pb"]


def run_cbc():
    # CBC has no python binding in this env: it runs as the CLI and its log is parsed. It prints
    # the objective at several precisions; the 10-figure "Optimal objective ... iterations" line
    # is the one used (see recipes/optimization).
    log = outp[:-len(".json")] + ".cbc.log"
    t0 = time.time()
    p = subprocess.run(["cbc", path, "sec", str(int(tl)), "solve"], stdout=open(log, "w"), stderr=subprocess.STDOUT)
    R["solve_s"] = time.time() - t0
    txt = open(log).read()
    sys.stdout.write(txt)
    R["cbc_rc"] = p.returncode
    mt = re.search(r"Optimal objective\s+(-?[0-9.]+(?:[eE][-+]?\d+)?)\s+-\s+\d+\s+iterations", txt)
    R["status"] = "optimal" if (mt and "Optimal - objective value" in txt) else "not-optimal"
    R["pb"] = float(mt.group(1)) if mt else None
    # CBC's certificate is the state it was in when it last printed "Optimal - objective value":
    # the nearest line before that is an iteration line (it must carry no "Primal inf"/"Dual inf"),
    # an "After Postsolve ... infeasibilities" line (they must vanish), or "full model needs cleaning
    # up" (a clean-up that took no iterations). A postsolve line FOLLOWED by a clean-up is the
    # pre-clean-up state, not the certificate: Clp re-optimises the full model after it.
    mp = list(re.finditer(r"infeasibilities\s*-\s*dual\s+([0-9.eE+-]+)\s*\(\d+\),\s*primal\s+([0-9.eE+-]+)", txt))
    if mp:
        R["cbc_dual_infeas"], R["cbc_primal_infeas"] = float(mp[-1].group(1)), float(mp[-1].group(2))
    R["cbc_cleanup"] = "full model needs cleaning up" in txt
    lines = txt.splitlines()
    last = max((i for i, l in enumerate(lines) if l.startswith("Optimal - objective value")), default=None)
    R["cbc_final_state"] = None
    if last is not None:
        for l in reversed(lines[:last]):
            if re.match(r"^\d+\s+Obj\s", l) or l.startswith("After Postsolve") or "needs cleaning up" in l:
                R["cbc_final_state"] = l.strip()
                break


def run_solfile():
    # MIPLIB .sol: "=obj= <value>" then "<name> <value>" per nonzero; absent names are zero.
    sol = sys.argv[4]
    R["solution_file"] = os.path.basename(os.path.dirname(sol)) + "/" + os.path.basename(sol)
    vals = {}
    for l in gzip.open(sol, "rt"):
        f = l.split()
        if len(f) >= 2:
            if f[0] == "=obj=":
                R["obj_reported"] = R["pb"] = float(f[1])
            else:
                vals[f[0]] = float(f[1])
    R["n_sols"] = 1
    def by_name(lp):
        names = list(lp.col_names_)
        return [vals.get(nm, 0.0) for nm in names] if set(vals) <= set(names) else None
    R.update(check(by_name))


try:
    with gzip.open(gz, "rb") as fi, open(path, "wb") as fo:
        shutil.copyfileobj(fi, fo, 1 << 20)
    {"highs": run_highs, "scip": run_scip, "cbc": run_cbc, "solfile": run_solfile}[solver]()
except Exception as e:  # recorded, never swallowed: the summary counts these
    R["error"] = "%s: %s" % (type(e).__name__, e)
finally:
    if os.path.exists(path):
        os.remove(path)   # this process wrote it; the staged .mps.gz is never touched
dump()
