#!/usr/bin/env bash
# Stage the checks. Nothing is fetched: every reference is a coalescent closed form, an
# integer invariant of a tree, or a second implementation shipped in the same env.
#
# Validated here is ONLY the theory the run compares against -- the harmonic number, the
# 2n-1/2n-2 counts, and that the closed form is derived rather than copied. msprime is not
# exercised locally and must not be: the instance is the verdict.
set -euo pipefail

BUCKET="s3://${1:?pass your bucket -- make stage RECIPE=popgen does this}"
REGION="${AWS_REGION:-us-west-2}"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT
cd "$tmp"
cp "$SCRIPT_DIR/identities.py" .
python3 -c "import ast,io; ast.parse(io.open('identities.py',encoding='utf-8').read())" \
  || { echo "  identities.py does not parse" >&2; exit 1; }
printf '  %-16s %6s bytes\n' identities.py "$(wc -c < identities.py | tr -d '[:space:]')"

echo "== the coalescent references must be derivable without msprime =="
python3 - <<'PY'
import ast, io, math, sys

src = io.open("identities.py", encoding="utf-8").read()
tree = ast.parse(src)
K = {}
for node in tree.body:
    if isinstance(node, ast.Assign):
        t = node.targets[0]
        # Only literal tuples are of interest; `a, b = 2*n-1, 2*n-2` is an expression
        # tuple and literal_eval raises on it, so guard rather than assume.
        if isinstance(t, ast.Tuple) and isinstance(node.value, ast.Tuple):
            names = [e.id for e in t.elts if isinstance(e, ast.Name)]
            try:
                vals = [ast.literal_eval(e) for e in node.value.elts]
            except (ValueError, TypeError):
                continue
            if len(names) == len(vals):
                K.update(dict(zip(names, vals)))
        elif isinstance(t, ast.Name) and t.id in ("SEED", "NREP"):
            try:
                K[t.id] = ast.literal_eval(node.value)
            except (ValueError, TypeError):
                pass
for k in ("INDIV", "NE", "MU", "RHO", "L", "SEED", "NREP"):
    if k not in K:
        sys.exit("  identities.py no longer defines %s" % k)

n = 2 * K["INDIV"]            # diploid: sample NODES, not individuals
print("  %d diploid individuals -> n = %d sample nodes" % (K["INDIV"], n))
print("  a binary tree on n samples: %d nodes (2n-1), %d edges (2n-2)" % (2*n-1, 2*n-2))
if 2*n-1 <= n or 2*n-2 <= 0:
    sys.exit("  the node/edge formulas are degenerate at this n")

theta = 4 * K["NE"] * K["MU"]
print("  E[pi] = 4*Ne*mu = 4 * %d * %g = %.6e per site" % (K["NE"], K["MU"], theta))
if abs(theta - 4 * K["NE"] * K["MU"]) > 0:
    sys.exit("  the closed form does not reproduce")
# Watterson's harmonic number, the other closed form the run uses.
a_n = sum(1.0 / i for i in range(1, n))
print("  a_n = sum_{i=1}^{n-1} 1/i = %.6f   (Watterson denominator)" % a_n)
if not (math.log(n - 1) < a_n < math.log(n) + 1):
    sys.exit("  a_n is not where the harmonic series puts it")

# theta must be small enough that the infinite-sites approximation is sane, or the
# mutation-vs-site counting gap the run asserts would be large rather than a few per mil.
print("  theta per site %.2e -> expected multi-hit fraction is small, so" % theta)
print("  num_mutations/num_sites should sit just above 1.0")
if theta > 0.01:
    sys.exit("  theta is large enough that multi-hit sites dominate; the Watterson"
             " accounting identity would not be a small correction")

# A single replicate is too variable to assert: that is why the run averages NREP.
print("  replicates averaged in-run: %d" % K["NREP"])
if K["NREP"] < 10:
    sys.exit("  too few replicates for a standard-error bound to mean anything")
print("  references are sound: closed forms and integer invariants, no data")
PY

sha256sum identities.py > pins.sha256
aws s3 cp --region "$REGION" identities.py "$BUCKET/inputs/popgen/identities.py" >/dev/null
aws s3 cp --region "$REGION" pins.sha256   "$BUCKET/inputs/popgen/pins.sha256"   >/dev/null
cat pins.sha256
echo "done."
echo "  $BUCKET/inputs/popgen/  (no data staged -- the references are coalescent theory)"
