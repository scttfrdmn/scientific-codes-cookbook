#!/usr/bin/env python3
"""Z3 on Graviton4: every verdict checked by something other than Z3.

An SMT answer is a proof, so every check here is exact -- but "Z3 returned sat" proves
nothing on its own. So each `sat` model is evaluated back against the original
constraints in plain Python, and each `unsat` is on a problem whose unsatisfiability is a
theorem (pigeonhole, a graph's chromatic number) or is confirmed by exhaustive search:

 1. Pigeonhole: n+1 pigeons into n holes is unsat for n = 2..6; n into n is sat (model
    checked). With the "every pigeon sits somewhere" clauses tracked, the unsat core must
    be ALL n+1 of them -- any n pigeons fit, so no smaller core exists.
 2. Chromatic number: the Petersen graph is 3-chromatic (2 unsat, 3 sat) and the Groetzsch
    graph, built here by Mycielski's construction from C5, is 4-chromatic (3 unsat, 4 sat).
    Every colouring is checked edge by edge in Python.
 3. N-queens by blocking clauses: the solution counts for N = 1..8 must be exactly
    1, 0, 0, 2, 10, 4, 40, 92 (OEIS A000170), every solution valid and distinct, and the
    same counts reproduced by an independent Python backtracking search.
 4. Sudoku: Inkala's 2012 puzzle must solve to its published solution, and blocking that
    solution must be unsat -- the uniqueness the puzzle is published with.
 5. Bit-vectors: two carry identities proven for all 32- and 64-bit pairs (negation unsat)
    and exhaustively confirmed in Python at 8 bits; "x & (x-1) == 0 iff x is a power of two"
    is FALSE, its counterexample is checked in Python and must be the only one (x = 0); the
    binary-search midpoint (lo+hi)/2 overflows, and the counterexample is checked with
    32-bit two's-complement arithmetic in Python.
 6. Optimize: a 15-item, two-constraint 0-1 knapsack and a bounded integer program --
    Z3's optimum must equal brute force over every point, and equal HiGHS's MIP optimum on
    the same instance (cross-code, exact for an integer program).

Run with `python3 -u` so a killed run still has its output in command.log.
"""
import itertools
import sys
import time

ROWS = []


def rec(k, v):
    """Always str() the value -- `"%s" % obj` unpacks a tuple-like object as format args."""
    v = v if isinstance(v, str) else str(v)
    print("%s\t%s" % (k, v))
    ROWS.append((k, v))


def die(msg):
    print("FAIL: %s" % msg)
    sys.exit(1)


def ver(label, fn):
    """A descriptive line must never be able to fail the run."""
    try:
        rec(label, fn())
    except Exception as exc:
        rec(label, "unknown (%s)" % type(exc).__name__)


import z3

ver("z3", lambda: z3.get_version_string())
ver("python", lambda: sys.version.split()[0])
ver("highspy", lambda: __import__("importlib.metadata").metadata.version("highspy"))

T0 = time.time()


def check(s, want, what):
    r = s.check()
    if r != want:
        die("%s: Z3 said %s, expected %s" % (what, r, want))
    return r


# ======================================================== 1. pigeonhole
def php(p, h):
    x = [[z3.Bool("x_%d_%d" % (i, j)) for j in range(h)] for i in range(p)]
    s = z3.Solver()
    for j in range(h):  # at most one pigeon per hole -- hard
        for a in range(p):
            for b in range(a + 1, p):
                s.add(z3.Or(z3.Not(x[a][j]), z3.Not(x[b][j])))
    sits = []
    for i in range(p):  # every pigeon sits somewhere -- tracked
        t = z3.Bool("pigeon_%d" % i)
        s.assert_and_track(z3.Or(x[i]), t)
        sits.append(t)
    return s, x, sits


php_rows = []
for n in range(2, 7):
    s, x, sits = php(n + 1, n)
    check(s, z3.unsat, "pigeonhole %d into %d" % (n + 1, n))
    core = {str(c) for c in s.unsat_core()}
    if core != {str(t) for t in sits}:
        die("pigeonhole %d into %d: core is %d of %d pigeon clauses; any %d fit, so it "
            "must be all of them" % (n + 1, n, len(core), n + 1, n))
    s, x, _ = php(n, n)
    check(s, z3.sat, "pigeonhole %d into %d" % (n, n))
    m = s.model()
    place = [[z3.is_true(m.eval(x[i][j], model_completion=True)) for j in range(n)]
             for i in range(n)]
    if not all(any(r) for r in place):
        die("pigeonhole %d into %d: a pigeon has no hole in the model" % (n, n))
    if not all(sum(place[i][j] for i in range(n)) <= 1 for j in range(n)):
        die("pigeonhole %d into %d: two pigeons share a hole in the model" % (n, n))
    php_rows.append("%d->%d unsat core %d/%d, %d->%d sat" % (n + 1, n, len(core), n + 1, n, n))
rec("pigeonhole", "; ".join(php_rows))


# ======================================================== 2. chromatic number
def petersen():
    outer = [(i, (i + 1) % 5) for i in range(5)]
    spokes = [(i, i + 5) for i in range(5)]
    inner = [(5 + i, 5 + (i + 2) % 5) for i in range(5)]
    return 10, outer + spokes + inner


def mycielski(nv, edges):
    """Mycielski's construction: raises the chromatic number by one, keeps it triangle-free."""
    out = list(edges)
    for u, v in edges:
        out += [(u, nv + v), (v, nv + u)]
    w = 2 * nv
    out += [(nv + i, w) for i in range(nv)]
    return 2 * nv + 1, out


def colour(nv, edges, k):
    c = [z3.Int("c_%d" % i) for i in range(nv)]
    s = z3.Solver()
    s.add([z3.And(ci >= 0, ci < k) for ci in c])
    s.add([c[u] != c[v] for u, v in edges])
    return s, c


def chromatic(name, nv, edges, chi):
    s, _ = colour(nv, edges, chi - 1)
    check(s, z3.unsat, "%s with %d colours" % (name, chi - 1))
    s, c = colour(nv, edges, chi)
    check(s, z3.sat, "%s with %d colours" % (name, chi))
    m = s.model()
    col = [m.eval(ci, model_completion=True).as_long() for ci in c]
    if not all(0 <= v < chi for v in col):
        die("%s: a colour is out of range in the model" % name)
    bad = [(u, v) for u, v in edges if col[u] == col[v]]
    if bad:
        die("%s: the %d-colouring has %d monochromatic edges" % (name, chi, len(bad)))
    rec("chromatic_%s" % name, "%d vertices, %d edges: %d colours unsat, %d sat, "
        "colouring checked on every edge" % (nv, len(edges), chi - 1, chi))


nv, e = petersen()
if (nv, len(e), len(set(map(frozenset, e)))) != (10, 15, 15):
    die("Petersen graph is not 10 vertices / 15 edges")
chromatic("petersen", nv, e, 3)
nv, e = mycielski(5, [(i, (i + 1) % 5) for i in range(5)])
if (nv, len(e), len(set(map(frozenset, e)))) != (11, 20, 20):
    die("Groetzsch graph is not 11 vertices / 20 edges")
adj = {i: set() for i in range(nv)}
for u, v in e:
    adj[u].add(v)
    adj[v].add(u)
if any(adj[u] & adj[v] for u, v in e):
    die("Groetzsch graph has a triangle")
chromatic("groetzsch", nv, e, 4)


# ======================================================== 3. N-queens, counted
def queens_ok(cols):
    n = len(cols)
    return (sorted(cols) == list(range(n)) and
            all(abs(cols[i] - cols[j]) != j - i for i in range(n) for j in range(i + 1, n)))


def queens_py(n):
    """Independent count: plain backtracking, no solver."""
    count = 0

    def go(row, cols, d1, d2):
        nonlocal count
        if row == n:
            count += 1
            return
        for c in range(n):
            if c not in cols and row - c not in d1 and row + c not in d2:
                go(row + 1, cols | {c}, d1 | {row - c}, d2 | {row + c})
    go(0, frozenset(), frozenset(), frozenset())
    return count


A000170 = [1, 0, 0, 2, 10, 4, 40, 92]
got = []
for n in range(1, 9):
    q = [z3.Int("q_%d" % i) for i in range(n)]
    s = z3.Solver()
    s.add([z3.And(qi >= 0, qi < n) for qi in q])
    s.add(z3.Distinct(q))
    s.add([z3.And(q[i] - q[j] != j - i, q[j] - q[i] != j - i)
           for i in range(n) for j in range(i + 1, n)])
    sols = set()
    while s.check() == z3.sat:
        m = s.model()
        cols = tuple(m.eval(qi, model_completion=True).as_long() for qi in q)
        if not queens_ok(cols):
            die("%d-queens: Z3 returned an invalid placement %s" % (n, cols))
        if cols in sols:
            die("%d-queens: Z3 returned %s twice despite the blocking clause" % (n, cols))
        sols.add(cols)
        s.add(z3.Or([qi != v for qi, v in zip(q, cols)]))
        if len(sols) > 1000:
            die("%d-queens: runaway enumeration" % n)
    got.append(len(sols))
py = [queens_py(n) for n in range(1, 9)]
rec("nqueens_counts", "N=1..8: z3 %s, python %s, OEIS A000170 %s" % (got, py, A000170))
if got != A000170:
    die("N-queens counts %s differ from OEIS A000170 %s" % (got, A000170))
if py != A000170:
    die("the independent Python count %s differs from A000170" % py)
# control: the Python verifier must reject a placement with two queens on a diagonal
if queens_ok((0, 1, 2, 3, 4, 5, 6, 7)) or not queens_ok((0, 4, 7, 5, 2, 6, 1, 3)):
    die("the Python queens verifier does not discriminate")


# ======================================================== 4. Sudoku, unique
PUZZLE = ("8........" "..36....." ".7..9.2.." ".5...7..." "....457.."
          "...1...3." "..1....68" "..85...1." ".9....4..")
SOLUTION = ("812753649" "943682175" "675491283" "154237896" "369845721"
            "287169534" "521974368" "438526917" "796318452")


def sudoku_ok(g):
    rows = [g[9 * r:9 * r + 9] for r in range(9)]
    cols = [g[c::9] for c in range(9)]
    boxes = [[g[9 * (3 * br + i) + 3 * bc + j] for i in range(3) for j in range(3)]
             for br in range(3) for bc in range(3)]
    return all(sorted(u) == list(range(1, 10)) for u in rows + cols + boxes)


cells = [z3.Int("s_%d" % i) for i in range(81)]
s = z3.Solver()
s.add([z3.And(v >= 1, v <= 9) for v in cells])
for r in range(9):
    s.add(z3.Distinct(cells[9 * r:9 * r + 9]))
for c in range(9):
    s.add(z3.Distinct(cells[c::9]))
for br in range(3):
    for bc in range(3):
        s.add(z3.Distinct([cells[9 * (3 * br + i) + 3 * bc + j]
                           for i in range(3) for j in range(3)]))
clues = [(i, int(ch)) for i, ch in enumerate(PUZZLE) if ch != "."]
s.add([cells[i] == v for i, v in clues])
check(s, z3.sat, "Sudoku")
m = s.model()
g = [m.eval(v, model_completion=True).as_long() for v in cells]
if not sudoku_ok(g):
    die("Sudoku: Z3's grid breaks a row, column or box")
if any(g[i] != v for i, v in clues):
    die("Sudoku: Z3's grid does not keep the clues")
if "".join(map(str, g)) != SOLUTION:
    die("Sudoku: Z3's grid differs from the published solution")
# control: the Python verifier must reject the solution with two cells of a row swapped
bad = list(g)
bad[0], bad[1] = bad[1], bad[0]
if sudoku_ok(bad):
    die("the Python Sudoku verifier accepted a grid with a swapped pair")
s.add(z3.Or([v != x for v, x in zip(cells, g)]))
check(s, z3.unsat, "Sudoku with its solution blocked")
rec("sudoku", "Inkala 2012, %d clues: valid, equals the published solution, "
    "blocking it is unsat (unique)" % len(clues))


# ======================================================== 5. bit-vectors
def prove(claim, what):
    s = z3.Solver()
    s.add(z3.Not(claim))
    check(s, z3.unsat, "negation of %s" % what)


ids = {
    "x+y == (x^y) + 2(x&y)": (lambda x, y: x + y == (x ^ y) + 2 * (x & y),
                              lambda x, y, M: (x + y) & M == ((x ^ y) + 2 * (x & y)) & M),
    "x^y == (x|y) - (x&y)": (lambda x, y: x ^ y == (x | y) - (x & y),
                             lambda x, y, M: (x ^ y) & M == ((x | y) - (x & y)) & M),
}
for name, (zf, pf) in ids.items():
    for w in (32, 64):
        x, y = z3.BitVecs("x y", w)
        prove(zf(x, y), "%s at %d bits" % (name, w))
    M = 0xFF
    if not all(pf(a, b, M) for a in range(256) for b in range(256)):
        die("%s fails somewhere in Python's exhaustive 8-bit check" % name)
rec("bv_identities", "%d identities: negation unsat at 32 and 64 bits; "
    "exhaustive 8-bit Python check passes (65536 pairs each)" % len(ids))

x = z3.BitVec("x", 32)
pow2 = z3.Or([x == (1 << k) for k in range(32)])
s = z3.Solver()
s.add(((x & (x - 1)) == 0) != pow2)
check(s, z3.sat, "the false power-of-two claim")
cx = s.model()[x].as_long()
py_trick = (cx & ((cx - 1) & 0xFFFFFFFF)) == 0
py_pow2 = cx != 0 and (cx & (cx - 1)) == 0
if py_trick == py_pow2:
    die("Z3's counterexample x=%d does not break the claim in Python" % cx)
s.add(x != cx)
check(s, z3.unsat, "the power-of-two claim with x=%d excluded" % cx)
if cx != 0:
    die("the only counterexample should be x = 0, Z3 gave %d" % cx)
rec("bv_false_claim", "x&(x-1)==0 <=> pow2: counterexample x=%d, verified in Python, "
    "the only one (excluding it is unsat)" % cx)

lo, hi = z3.BitVecs("lo hi", 32)
s = z3.Solver()
s.add(lo >= 0, hi >= lo)  # signed comparisons
s.add((lo + hi) / 2 != lo + (hi - lo) / 2)  # signed division
check(s, z3.sat, "the midpoint overflow")
m = s.model()


def s32(v):
    v &= 0xFFFFFFFF
    return v - (1 << 32) if v >> 31 else v


def sdiv2(v):
    return int(v / 2)  # C truncation toward zero


L, H = s32(m[lo].as_long()), s32(m[hi].as_long())
naive, safe = sdiv2(s32(L + H)), sdiv2(s32(H - L)) + L
if not (0 <= L <= H) or naive == safe:
    die("midpoint counterexample lo=%d hi=%d does not reproduce in Python" % (L, H))
if not (L <= safe <= H) or L <= naive <= H:
    die("midpoint: expected the safe form in range and the naive one out of it")
rec("bv_midpoint", "lo=%d hi=%d: (lo+hi)/2 = %d (overflowed), lo+(hi-lo)/2 = %d; "
    "checked in 32-bit Python" % (L, H, naive, safe))


# ======================================================== 6. Optimize vs brute force vs HiGHS
VAL = [23, 31, 29, 44, 53, 38, 63, 85, 89, 82, 17, 41, 57, 36, 72]
WT = [12, 18, 16, 22, 27, 19, 31, 40, 43, 39, 9, 21, 28, 17, 35]
VOL = [7, 11, 13, 5, 17, 9, 14, 22, 19, 8, 4, 12, 16, 10, 20]
CAPW, CAPV = 165, 85
n = len(VAL)

best = -1
for bits in range(1 << n):
    w = v = val = 0
    for i in range(n):
        if bits >> i & 1:
            w += WT[i]
            v += VOL[i]
            val += VAL[i]
    if w <= CAPW and v <= CAPV and val > best:
        best = val

xs = [z3.Int("k_%d" % i) for i in range(n)]
o = z3.Optimize()
o.add([z3.And(xi >= 0, xi <= 1) for xi in xs])
o.add(z3.Sum([WT[i] * xs[i] for i in range(n)]) <= CAPW)
o.add(z3.Sum([VOL[i] * xs[i] for i in range(n)]) <= CAPV)
h = o.maximize(z3.Sum([VAL[i] * xs[i] for i in range(n)]))
check(o, z3.sat, "knapsack")
m = o.model()
pick = [m.eval(xi, model_completion=True).as_long() for xi in xs]
zval = sum(VAL[i] * pick[i] for i in range(n))
if not all(p in (0, 1) for p in pick):
    die("knapsack: a non-binary pick in Z3's model")
if sum(WT[i] * pick[i] for i in range(n)) > CAPW or sum(VOL[i] * pick[i] for i in range(n)) > CAPV:
    die("knapsack: Z3's model breaks a capacity")
if zval != h.value().as_long():
    die("knapsack: the model's value %d differs from Z3's reported optimum %s" % (zval, h.value()))


def highs_mip(c, A, ub, lo, hi, maximize=True):
    import highspy
    import numpy as np
    hs = highspy.Highs()
    hs.setOptionValue("output_flag", False)
    lp = highspy.HighsLp()
    nc = len(c)
    lp.num_col_, lp.num_row_ = nc, len(A)
    lp.col_cost_ = np.array(c, dtype=float)
    lp.col_lower_ = np.array(lo, dtype=float)
    lp.col_upper_ = np.array(hi, dtype=float)
    lp.row_lower_ = np.full(len(A), -highspy.kHighsInf)
    lp.row_upper_ = np.array(ub, dtype=float)
    lp.sense_ = highspy.ObjSense.kMaximize if maximize else highspy.ObjSense.kMinimize
    lp.integrality_ = [highspy.HighsVarType.kInteger] * nc
    starts, idx, vals = [0], [], []
    for j in range(nc):
        for r in range(len(A)):
            if A[r][j]:
                idx.append(r)
                vals.append(A[r][j])
        starts.append(len(idx))
    lp.a_matrix_.format_ = highspy.MatrixFormat.kColwise
    lp.a_matrix_.start_ = np.array(starts)
    lp.a_matrix_.index_ = np.array(idx)
    lp.a_matrix_.value_ = np.array(vals, dtype=float)
    hs.passModel(lp)
    hs.run()
    st = hs.modelStatusToString(hs.getModelStatus())
    return st, hs.getInfo().objective_function_value


try:
    hst, hval = highs_mip(VAL, [WT, VOL], [CAPW, CAPV], [0] * n, [1] * n)
except Exception as exc:
    die("HiGHS could not solve the knapsack: %s: %s" % (type(exc).__name__, exc))
rec("knapsack", "15 items, 2 capacities, 32768 subsets: z3 %d, brute force %d, HiGHS %s %r"
    % (zval, best, hst, hval))
if zval != best:
    die("knapsack: Z3's optimum %d differs from brute force %d" % (zval, best))
if hst != "Optimal" or round(hval) != best or abs(hval - best) > 1e-6:
    die("knapsack: HiGHS (%s, %r) differs from brute force %d" % (hst, hval, best))

# a general bounded integer program: minimise, negative coefficients, equality-free
C = [3, -2, 5, -4]
A = [[2, 1, -1, 3], [-1, 4, 2, 1], [1, -3, 1, 2]]
B = [14, 12, 6]
LO, HI = [-3, 0, -2, -1], [6, 5, 4, 7]
grid = itertools.product(*[range(a, b + 1) for a, b in zip(LO, HI)])
feas = [p for p in grid if all(sum(r[j] * p[j] for j in range(4)) <= b for r, b in zip(A, B))]
bf = min(sum(C[j] * p[j] for j in range(4)) for p in feas)
ys = [z3.Int("y_%d" % j) for j in range(4)]
o = z3.Optimize()
o.add([z3.And(ys[j] >= LO[j], ys[j] <= HI[j]) for j in range(4)])
o.add([z3.Sum([r[j] * ys[j] for j in range(4)]) <= b for r, b in zip(A, B)])
h = o.minimize(z3.Sum([C[j] * ys[j] for j in range(4)]))
check(o, z3.sat, "integer program")
m = o.model()
y = [m.eval(v, model_completion=True).as_long() for v in ys]
if not all(LO[j] <= y[j] <= HI[j] for j in range(4)) or \
        not all(sum(r[j] * y[j] for j in range(4)) <= b for r, b in zip(A, B)):
    die("integer program: Z3's model is infeasible")
zip_val = sum(C[j] * y[j] for j in range(4))
if zip_val != h.value().as_long():
    die("integer program: the model's value differs from Z3's reported optimum")
try:
    hst, hval = highs_mip(C, A, B, LO, HI, maximize=False)
except Exception as exc:
    die("HiGHS could not solve the integer program: %s: %s" % (type(exc).__name__, exc))
rec("integer_program", "4 vars, 3 rows, %d feasible of %d points: z3 %d, brute force %d, "
    "HiGHS %s %r" % (len(feas), 10 * 6 * 7 * 9, zip_val, bf, hst, hval))
if zip_val != bf:
    die("integer program: Z3's optimum %d differs from brute force %d" % (zip_val, bf))
if hst != "Optimal" or abs(hval - bf) > 1e-6:
    die("integer program: HiGHS (%s, %r) differs from brute force %d" % (hst, hval, bf))

rec("wall_s", "%.1f" % (time.time() - T0))
with open("score.tsv", "w") as fh:
    fh.write("observable\tvalue\n")
    for k_, v in ROWS:
        fh.write("%s\t%s\n" % (k_, v))

print("Z3 IDENTITIES OK")
