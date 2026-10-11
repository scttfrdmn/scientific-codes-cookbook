#!/usr/bin/env bash
# Stage the checks only. Nothing is fetched: 5000 values from a pinned seed, so every
# reference is either an exact count or numpy's independent binning of the same array.
#
# Validated here is ONLY what does not need yoda -- that the fixture straddles the axis
# (so the in-range/total distinction is actually exercised) and that numpy's binning
# convention matches the one the run compares against. The instance is the verdict.
set -euo pipefail

BUCKET="s3://${1:?pass your bucket -- make stage RECIPE=yoda does this}"
REGION="${AWS_REGION:-us-west-2}"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT
cd "$tmp"
cp "$SCRIPT_DIR/identities.py" .
python3 -c "import ast,io; ast.parse(io.open('identities.py',encoding='utf-8').read())" \
  || { echo "  identities.py does not parse" >&2; exit 1; }
printf '  %-16s %6s bytes\n' identities.py "$(wc -c < identities.py | tr -d '[:space:]')"
grep -q 'clone()' identities.py \
  || { echo "  identities.py no longer uses clone(); deepcopy raises on yoda objects" >&2; exit 1; }
echo "  uses clone() rather than copy.deepcopy (which raises on yoda's Cython objects)"

echo "== the fixture must straddle the axis, and numpy's convention must be the compared one =="
python3 - <<'PY'
import ast, io, sys
import numpy as np

src = io.open("identities.py", encoding="utf-8").read()
K = {}
for node in ast.parse(src).body:
    if isinstance(node, ast.Assign):
        t = node.targets[0]
        if isinstance(t, ast.Tuple):
            names = [e.id for e in t.elts if isinstance(e, ast.Name)]
            try:
                vals = [ast.literal_eval(e) for e in node.value.elts]
            except Exception:
                continue
            if len(names) == len(vals):
                K.update(dict(zip(names, vals)))
for k in ("NBINS", "LO", "HI", "SEED", "NFILL"):
    if k not in K:
        sys.exit("  identities.py no longer defines %s" % k)
NBINS, LO, HI, SEED, NFILL = K["NBINS"], K["LO"], K["HI"], K["SEED"], K["NFILL"]
print("  %d bins over [%g, %g); %d fills from seed %d" % (NBINS, LO, HI, NFILL, SEED))

rng = np.random.default_rng(SEED)
DATA = rng.uniform(-2.0, 12.0, NFILL)
inside = int(((DATA >= LO) & (DATA < HI)).sum())
print("  in range %d, outside %d (%.1f%% outside)"
      % (inside, NFILL - inside, 100.0 * (NFILL - inside) / NFILL))
if not (0.1 * NFILL < NFILL - inside < 0.9 * NFILL):
    sys.exit("  the fixture does not straddle the axis meaningfully; the in-range vs"
             " total distinction would be untested")

# numpy's range= bins are half-open [lo, hi) except the LAST, which includes hi. The
# fixture's probability of landing exactly on hi is zero for a continuous draw, so the
# two conventions cannot diverge here -- stated because it is the kind of edge that
# silently breaks a count comparison.
on_edge = int((DATA == HI).sum())
print("  values exactly on the upper edge: %d (numpy includes hi in the last bin)" % on_edge)
if on_edge:
    sys.exit("  a value sits exactly on the upper edge; yoda and numpy may assign it"
             " to different bins and the count comparison would be convention-dependent")

counts, edges = np.histogram(DATA, bins=NBINS, range=(LO, HI))
print("  numpy counts sum to %d, which must equal the in-range fills %d"
      % (counts.sum(), inside))
if counts.sum() != inside:
    sys.exit("  numpy's own binning does not account for every in-range fill")
print("  bin width %.6f, edges %g .. %g" % (edges[1] - edges[0], edges[0], edges[-1]))

# The moments the run asserts are over ALL fills, so record both here to make the
# distinction explicit before anyone compares against the wrong one.
ins = DATA[(DATA >= LO) & (DATA < HI)]
print("  mean of all fills %.9f ; mean of in-range only %.9f  (differ by %.1e)"
      % (DATA.mean(), ins.mean(), abs(DATA.mean() - ins.mean()) / abs(ins.mean())))
print("  sd   of all fills %.9f ; sd   of in-range only %.9f" % (DATA.std(ddof=0), ins.std(ddof=0)))
print("  references are sound: exact counts, an independent binning, and the right subset")
PY

sha256sum identities.py > pins.sha256
aws s3 cp --region "$REGION" identities.py "$BUCKET/inputs/yoda/identities.py" >/dev/null
aws s3 cp --region "$REGION" pins.sha256   "$BUCKET/inputs/yoda/pins.sha256"   >/dev/null
cat pins.sha256
echo "done."
echo "  $BUCKET/inputs/yoda/  (no data staged -- the fills come from a pinned seed)"
