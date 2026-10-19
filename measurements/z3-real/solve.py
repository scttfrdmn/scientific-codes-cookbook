# One benchmark: Z3 on one SMT-LIB file, in its own process, writing one JSON record.
#
#   python3 -u solve.py <file.smt2> <time limit s> <memory limit MiB> <out.json>
#
# 1. solve: Solver().from_file(), check() under Z3's own timeout -- the plain way to run a file.
# 2. sat:   read every declared constant from the model (model_completion=True) and evaluate the
#           file's assertions with smtcheck.py, which shares no code with Z3.
# 3. unsat: track each top-level assertion, ask for a minimised core, re-solve the core alone in a
#           fresh solver (it must be unsat), then drop each core member in turn: the remainder must
#           be sat, and its model is checked by smtcheck.py on exactly the core's other members.
#           That proves every member necessary, independently of Z3. Steps 3 share one time limit.
import json, os, sys, time
from fractions import Fraction

path, TL, MEM, outp = sys.argv[1], float(sys.argv[2]), int(sys.argv[3]), sys.argv[4]
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import smtcheck  # noqa: E402

R = {"file": path}


def dump():
    with open(outp + ".part", "w") as fh:
        json.dump(R, fh)
    os.replace(outp + ".part", outp)


head = open(path, "rb").read(65536).decode("utf-8", "replace")
for k in ("status",):
    i = head.find("(set-info :%s " % k)
    R[k] = head[i:].split(None, 2)[2].split(")")[0].strip() if i >= 0 else "absent"
dump()

import z3  # noqa: E402
z3.set_param("memory_max_size", MEM)
R["z3"] = z3.get_version_string()


def stat(s, key):
    st = s.statistics()
    for k in st.keys():
        if k == key:
            return st.get_key_value(k)
    return None


def chk(so):
    try:
        return so.check()
    except z3.Z3Exception:
        return z3.unknown


def model_values(m, decls):
    vals = {}
    for n, srt in decls.items():
        so = {"Int": z3.IntSort(), "Real": z3.RealSort(), "Bool": z3.BoolSort()}.get(srt)
        if so is None:
            raise smtcheck.Unsupported("sort %s" % srt)
        v = m.eval(z3.Const(n, so), model_completion=True)
        if z3.is_true(v):
            vals[n] = True
        elif z3.is_false(v):
            vals[n] = False
        elif z3.is_int_value(v):
            vals[n] = v.as_long()
        elif z3.is_rational_value(v):
            f = v.as_fraction()
            vals[n] = Fraction(f.numerator, f.denominator)
        else:
            raise smtcheck.Unsupported("model value %s for %s" % (v.sexpr()[:80], n))
    return vals


cmds = decls = None


def parsed():
    global cmds, decls
    if cmds is None:
        cmds = smtcheck.run_deep(smtcheck.commands, path)
        decls = smtcheck.declarations(path, cmds)
    return cmds, decls


def check_model(m, which=None):
    """smtcheck verdict on Z3's model: 'ok', 'VIOLATED', or 'not-checked: <why>'."""
    t0 = time.time()
    try:
        c, d = parsed()
        vals = model_values(m, d)
        res = smtcheck.run_deep(smtcheck.evaluate, path, vals, c)
    except (smtcheck.Unsupported, smtcheck.Undetermined) as e:
        return "not-checked: %s: %s" % (type(e).__name__, str(e)[:120]), time.time() - t0, None
    full = all(res)
    if which is not None:
        res = [res[i] for i in which]
    return ("ok" if all(res) else "VIOLATED"), time.time() - t0, (len(res) if which is None else full)


# 1. solve
t0 = time.time()
s = z3.Solver()
s.set("timeout", int(TL * 1000))
try:
    s.from_file(path)
except z3.Z3Exception as e:
    R["error"] = "z3 parse: %s" % str(e)[:300]
    dump()
    sys.exit(0)
R["parse_s"] = round(time.time() - t0, 3)
t1 = time.time()
try:
    r = s.check()
except z3.Z3Exception as e:   # memory_max_size surfaces as an exception, not as unknown
    r = z3.unknown
    R["exception"] = str(e)[:200]
R["solve_s"] = round(time.time() - t1, 3)
R["verdict"] = str(r)
R["reason"] = (R.get("exception") or s.reason_unknown()) if r == z3.unknown else ""
R["rlimit"] = stat(s, "rlimit count")
R["conflicts"] = stat(s, "conflicts")
dump()

# 2. sat: the model, checked outside Z3
if r == z3.sat:
    R["model_check"], dt, n = check_model(s.model())
    R["model_check_s"], R["assertions"] = round(dt, 3), n

# 3. unsat: a minimised core, re-solved alone, every member shown necessary by a checked model
if r == z3.unsat:
    tc = time.time()
    left = lambda: max(1, int((TL - (time.time() - tc)) * 1000))
    A = z3.parse_smt2_file(path)
    R["assertions"] = len(A)
    s2 = z3.Solver()
    s2.set("timeout", left())
    s2.set("core.minimize", True)
    lits = [z3.Bool("__core_%d" % i) for i in range(len(A))]
    for i in range(len(A)):
        s2.assert_and_track(A[i], lits[i])
    r2 = chk(s2)
    R["core_verdict"] = str(r2)
    if r2 == z3.unsat:
        idx = {str(l): i for i, l in enumerate(lits)}
        core = sorted(idx[str(c)] for c in s2.unsat_core())
        R["core"] = core
        R["core_size"] = len(core)
        s3 = z3.Solver()
        s3.set("timeout", left())
        for i in core:
            s3.add(A[i])
        R["core_alone"] = str(chk(s3))
        # necessity, only while time remains; the file's assertion order must match Z3's parse
        nec = {"necessary": 0, "redundant": 0, "unknown": 0, "violated": 0, "not-checked": 0,
               "control_rejected": 0, "control_ACCEPTED": 0}
        try:
            c, _ = parsed()
            n_file = sum(1 for x in c if isinstance(x, list) and x and x[0] == "assert")
        except (smtcheck.Unsupported, smtcheck.Undetermined) as e:
            n_file = -1
            R["necessity_skipped"] = "%s: %s" % (type(e).__name__, str(e)[:120])
        if n_file == len(A) and R["core_alone"] == "unsat":
            for j in core:
                if time.time() - tc > TL:
                    nec["unknown"] += 1
                    continue
                rest = [i for i in core if i != j]
                s4 = z3.Solver()
                s4.set("timeout", left())
                for i in rest:
                    s4.add(A[i])
                r4 = chk(s4)
                if r4 == z3.unsat:
                    nec["redundant"] += 1
                elif r4 == z3.sat:
                    v, _, full = check_model(s4.model(), rest)
                    nec["necessary" if v == "ok" else "violated" if v == "VIOLATED" else "not-checked"] += 1
                    # control: the whole file is unsat, so this model must break some assertion
                    if v == "ok":
                        nec["control_rejected" if not full else "control_ACCEPTED"] += 1
                else:
                    nec["unknown"] += 1
        elif n_file != len(A) and n_file >= 0:
            R["necessity_skipped"] = "file has %d asserts, Z3 parsed %d" % (n_file, len(A))
        R["necessity"] = nec
    R["core_s"] = round(time.time() - tc, 3)
dump()
