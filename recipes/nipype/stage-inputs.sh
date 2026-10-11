#!/usr/bin/env bash
# Stage the checks. Nothing is fetched: the workflow computes a known composition over integers,
# so the reference is arithmetic. What this script verifies locally is that the composition and
# the MapNode fixture actually discriminate -- a chain whose nodes commute, or a fixture where
# the wrong order gives the right answer, would make the ordering check vacuous.
set -euo pipefail

BUCKET="s3://${1:?pass your bucket -- make stage RECIPE=nipype does this}"
REGION="${AWS_REGION:-us-west-2}"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT
cd "$tmp"
cp "$SCRIPT_DIR/identities.py" .
python3 -c "import ast,io; ast.parse(io.open('identities.py',encoding='utf-8').read())" \
  || { echo "  identities.py does not parse" >&2; exit 1; }
printf '  %-16s %6s bytes\n' identities.py "$(wc -c < identities.py | tr -d '[:space:]')"

echo "== the chain must be order-sensitive, or 'ran in order' proves nothing =="
python3 - <<'PY'
import itertools, sys

ops = {"double": lambda x: 2*x, "addone": lambda x: x+1,
       "square": lambda x: x*x, "minus3": lambda x: x-3}
order = ["double", "addone", "square", "minus3"]

def apply(seq, x):
    for name in seq:
        x = ops[name](x)
    return x

for seed in (5, 7):
    correct = apply(order, seed)
    print("  x=%d  correct order -> %d" % (seed, correct))
    # How many of the 24 permutations give the SAME answer? If any other order matches, the
    # composition check cannot distinguish a mis-ordered DAG from a correct one.
    same = [p for p in itertools.permutations(order) if list(p) != order and apply(p, seed) == correct]
    print("     permutations giving the same result: %d of 23" % len(same))
    if same:
        sys.exit("  the composition is order-insensitive for x=%d (%s) -- pick different ops"
                 % (seed, same[:3]))

# And the two seeds must give different answers, or 'propagation' could return a stale value
# and still look right.
if apply(order, 5) == apply(order, 7):
    sys.exit("  both seeds give the same result; a stale value would pass the propagation check")
print("  x=5 -> %d and x=7 -> %d differ, so a stale value cannot pass propagation"
      % (apply(order, 5), apply(order, 7)))

# MapNode fixture: doubling must be injective over the items, so N outputs can be matched to
# N inputs rather than coincidentally equal.
items = [1, 2, 3, 4, 5, 6, 7]
doubled = [2*v for v in items]
if len(set(doubled)) != len(items):
    sys.exit("  the MapNode fixture is not injective under doubling")
print("  MapNode fixture: %d items, %d distinct outputs %s" % (len(items), len(set(doubled)), doubled))
PY

sha256sum identities.py > pins.sha256
aws s3 cp --region "$REGION" identities.py "$BUCKET/inputs/nipype/identities.py" >/dev/null
aws s3 cp --region "$REGION" pins.sha256  "$BUCKET/inputs/nipype/pins.sha256"  >/dev/null
cat pins.sha256
echo "done."
echo "  $BUCKET/inputs/nipype/  (no data staged -- the reference is arithmetic)"
