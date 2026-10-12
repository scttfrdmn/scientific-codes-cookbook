#!/usr/bin/env bash
# Stage the checks only. Nothing is fetched: the arrays are synthesised and the one numeric
# reference -- the correctly rounded sum -- is computed here two independent ways.
#
# Validated below, with zarr and dask absent from the loop: that the test image is a FAIR
# test (structured, not flat, not noise), that math.fsum really is the exactly rounded sum
# (checked against exact rational arithmetic, which needs no floating point at all), that
# numpy's own sum differs from it so the premise of the run's check holds, that integer
# addition is grouping-invariant, and that the chunk shapes and tie structure the run
# depends on are what the run assumes. The instance is the verdict.
set -euo pipefail

BUCKET="s3://${1:?pass your bucket -- make stage RECIPE=zarr-dask does this}"
REGION="${AWS_REGION:-us-west-2}"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT
cd "$tmp"
cp "$SCRIPT_DIR/identities.py" .
python3 -c "import ast,io; ast.parse(io.open('identities.py',encoding='utf-8').read())" \
  || { echo "  identities.py does not parse" >&2; exit 1; }
printf '  %-16s %6s bytes\n' identities.py "$(wc -c < identities.py | tr -d '[:space:]')"
grep -q 'v = v if isinstance(v, str) else str(v)' identities.py \
  || { echo "  rec() does not str() its value -- a Quantity would format as a tuple" >&2; exit 1; }

echo "== the arrays are lifted from the run script, not restated here =="
python3 - <<'PY'
import ast, io, math, sys
from fractions import Fraction
import numpy as np

# Lift make_arrays() out of identities.py by AST, so the data validated here and the data
# used in the run are the same code and cannot drift apart.
tree = ast.parse(io.open("identities.py", encoding="utf-8").read())
# make_arrays' defaults are SEED/H/W, so the constants have to come across too -- lifting
# the function but retyping the literals here would reintroduce exactly the drift this
# avoids.
WANT = {"SEED", "H", "W"}
pieces = []
for n in tree.body:
    if isinstance(n, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id in WANT for t in n.targets):
        pieces.append(ast.unparse(n))
    elif isinstance(n, ast.FunctionDef) and n.name == "make_arrays":
        pieces.append(ast.unparse(n))
if not any(p.startswith("def make_arrays") for p in pieces):
    sys.exit("  identities.py has no make_arrays() to lift")
exec(compile("\n".join(pieces), "<make_arrays from identities.py>", "exec"))
print("  lifted: %s" % ", ".join(p.split("(")[0].replace("def ", "").splitlines()[0]
                                 for p in pieces))
IMG, FLT, INT, PLANT = make_arrays()                               # noqa: F821
print("  lifted make_arrays() -- IMG %s %s, FLT/PLANT %s %s, INT %s"
      % (IMG.shape, IMG.dtype, FLT.shape, FLT.dtype, INT.dtype))

# 1. IS THE IMAGE A FAIR TEST? A flat array round-trips through a broken codec; pure noise
#    sits at 16 bits/px and cannot be compressed, which would make a ratio unsatisfiable.
levels = len(np.unique(IMG))
counts = np.bincount(IMG.ravel(), minlength=65536).astype("float64")
p = counts[counts > 0] / IMG.size
ent = float(-(p * np.log2(p)).sum())
neigh = float(np.abs(np.diff(IMG.astype("int32"), axis=1)).mean())
print("  IMG: %d distinct levels, entropy %.3f bits/px (of 16), mean neighbour diff %.1f"
      % (levels, ent, neigh))
if levels < 1000:
    sys.exit("  only %d distinct levels; the image would compress trivially" % levels)
if not 4.0 < ent < 15.0:
    sys.exit("  entropy %.3f bits/px is flat or noise-like; neither is a fair test" % ent)

# 2. IS math.fsum REALLY THE ANSWER? Verify it with exact rational arithmetic, which uses
#    no floating point at all: a Fraction sum is exact, and float(Fraction) rounds
#    correctly. If these agree, the run's reference is the true value and not an opinion.
def ulps(x, y):
    return 0.0 if x == y else abs(x - y) / math.ulp(max(abs(x), abs(y)))

def blocked(a, bs):
    """Exactly the shape of what a chunked reduction does: sum each block, then sum the
    block results. Plain numpy -- this proves the premise without dask in the loop."""
    f = a.ravel()
    return float(np.add.reduce([f[i:i + bs].sum() for i in range(0, f.size, bs)]))

for label, arr in (("sampled", FLT), ("planted", PLANT)):   # noqa: F821
    vals = arr.ravel().tolist()
    exact = float(sum(map(Fraction, vals)))
    fs = math.fsum(vals)
    nps = float(arr.sum())
    at = float(np.abs(arr).sum())
    kappa = at / abs(exact)
    print("  %s: kappa %.4g (sum|x| %.6g -> %.6g)" % (label, kappa, at, abs(exact)))
    print("    exact rational %.17g | math.fsum %.17g | identical %s"
          % (exact, fs, fs == exact))
    if fs != exact:
        sys.exit("    math.fsum is not the correctly rounded sum here, so the run cannot "
                 "use it as the true value")
    print("    numpy.sum() %.17g -- %.2f ULP from exact, backward error %.2e"
          % (nps, ulps(nps, exact), abs(nps - exact) / at))
    # The bound the run asserts is DERIVED: O(log2(n)*eps) for a pairwise or blocked
    # summation, against O(n*eps) for a left-to-right pass.
    eps = float(np.finfo("float64").eps)
    bound = math.log2(arr.size) * eps
    if abs(nps - exact) / at > bound:
        sys.exit("    numpy's own sum is %.3e of the accumulated magnitude out, past the "
                 "log2(%d)*eps = %.3e bound the run asserts; the bound is wrong, not the "
                 "libraries" % (abs(nps - exact) / at, arr.size, bound))
    print("    within the derived bound log2(%d)*eps = %.3e (naive would be ~%.3e)"
          % (arr.size, bound, arr.size * eps))

    # 3. DOES THE PREMISE HOLD? The run claims a BLOCKED reduction reorders the additions
    #    and therefore changes the last bits. That has to be true of this data, or the run
    #    asserts something it cannot observe -- and it is checkable in plain numpy.
    diffs = [(bs, blocked(arr, bs)) for bs in (arr.size, 16384, 4096, 256, 64)]
    moved = [bs for bs, v in diffs if v != nps]
    print("    blocked sums differing from numpy's sequential sum: %s of %d block sizes %s"
          % (len(moved), len(diffs), moved))
    if not moved:
        sys.exit("    no block size changed the sum on this array, so the run's claim that "
                 "chunking reorders the additions is untestable here")
    whole = dict(diffs)[arr.size]
    if whole != nps:
        sys.exit("    a single whole-array 'block' already differs from numpy's sum, so a "
                 "multi-block difference could not be attributed to the blocking")
    print("    and the single whole-array block reproduces numpy exactly, so the "
          "difference is attributable")

# 3b. THE PLANTED CASE MUST ACTUALLY BITE: 1e16 + 1.0 == 1e16, so a left-to-right pass
#     has to lose all 262,143 ones. Verified here in plain python before the run leans on it.
naive = 0.0
for v in PLANT.ravel().tolist():                                 # noqa: F821
    naive += v
true_p = math.fsum(PLANT.ravel().tolist())                       # noqa: F821
ones = float(PLANT.size - 1)                                     # noqa: F821
print("  planted: naive left-to-right %.17g vs true %.17g -- loses %.6g of %.6g"
      % (naive, true_p, abs(true_p - naive), ones))
if abs(true_p - naive) < ones * 0.5:
    sys.exit("  the planted array does not defeat a naive sum, so it is not demonstrating "
             "that ordering can cost everything")
print("    and ulp(1e16) = %.1f, which is why a single 1.0 vanishes into it" % math.ulp(1e16))
naive_be = abs(true_p - naive) / float(np.abs(PLANT).sum())      # noqa: F821
bound_p = math.log2(PLANT.size) * float(np.finfo("float64").eps)  # noqa: F821
print("    naive backward error %.3e vs the bound %.3e -- %.0fx outside, so the bound "
      "discriminates" % (naive_be, bound_p, naive_be / bound_p))
if naive_be <= bound_p:
    sys.exit("    the naive sum meets the bound, so the bound cannot separate a blocked "
             "summation from an order-naive one")

# 4. INTEGER ADDITION IS GROUPING-INVARIANT -- the reason the integer assertions are exact.
rng = np.random.default_rng(1)
flat = INT.ravel()
total = int(flat.sum())
for trial in range(3):
    cuts = sorted(rng.choice(np.arange(1, flat.size), size=9, replace=False).tolist())
    parts = np.split(flat, cuts)
    if sum(int(q.sum()) for q in parts) != total:
        sys.exit("  an integer sum changed under regrouping, which cannot happen")
print("  integer sum %d is invariant under 3 random regroupings into 10 blocks" % total)

# 5. THE CHUNK SHAPES AND THE TIE STRUCTURE THE RUN ASSUMES.
H, W = IMG.shape
if W % 13 == 0 or H % 17 == 0:
    sys.exit("  (17, 13) divides %dx%d, so there is no ragged edge chunk to test" % (H, W))
print("  (17, 13) over %dx%d -> %d x %d chunks with partial edges in both axes"
      % (H, W, -(-H // 17), -(-W // 13)))
ties = int(np.count_nonzero(INT == INT.max()))
print("  INT's maximum %d occurs %d times -- argmax is genuinely ambiguous" % (INT.max(), ties))
if ties < 2:
    sys.exit("  INT has a unique maximum, so the run's tie demonstration cannot happen")

# 6. THE PYRAMID IS EXACTLY CONSERVATIVE IN INTEGER ARITHMETIC, in plain numpy.
lv = IMG.astype("uint32")
tots = [int(lv.astype("uint64").sum())]
for _ in range(2):
    lv = lv.reshape(lv.shape[0] // 2, 2, lv.shape[1] // 2, 2).sum(axis=(1, 3))
    tots.append(int(lv.astype("uint64").sum()))
print("  2x2 block-sum pyramid totals: %s" % " == ".join(str(t) for t in tots))
if len(set(tots)) != 1:
    sys.exit("  the pyramid is not conservative in plain numpy: %s" % tots)
print("  references are sound: a verified exact sum, a fair image, a real tie, a ragged grid")
PY

sha256sum identities.py > pins.sha256
aws s3 cp --region "$REGION" identities.py "$BUCKET/inputs/zarr-dask/identities.py" >/dev/null
aws s3 cp --region "$REGION" pins.sha256   "$BUCKET/inputs/zarr-dask/pins.sha256"   >/dev/null
cat pins.sha256
echo "done."
echo "  $BUCKET/inputs/zarr-dask/  (no data staged -- the arrays are synthesised)"
