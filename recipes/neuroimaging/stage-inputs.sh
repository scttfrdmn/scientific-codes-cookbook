#!/usr/bin/env bash
# Stage the checks. There is no scientific DATA to stage, and that is deliberate.
#
# DIPY ships fetchers (dipy.data.fetch_*) that download at run time, which this project does not
# do. The alternative is better than a workaround: every signal here is SYNTHESISED from a
# diffusion tensor whose fractional anisotropy and mean diffusivity have closed forms, so the
# expected answer is arithmetic rather than a file. Nothing to pin, nothing to fetch, and the
# truth is exact rather than a reference someone else measured.
set -euo pipefail

BUCKET="s3://${1:?pass your bucket -- make stage RECIPE=neuroimaging does this}"
REGION="${AWS_REGION:-us-west-2}"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT
cd "$tmp"
cp "$SCRIPT_DIR/identities.py" .

echo "== the checks must parse before they cost an instance =="
python3 -c "import ast,io; ast.parse(io.open('identities.py',encoding='utf-8').read())" \
  || { echo "  identities.py does not parse" >&2; exit 1; }
printf '  %-16s %6s bytes\n' identities.py "$(wc -c < identities.py)"

echo "== and the closed form it asserts is checkable here, with no tools at all =="
# If this arithmetic is wrong the recipe would chase a wrong target on a paid box. FA is a
# function of the eigenvalues only, so it can be verified in four lines of python.
python3 - <<'PY'
import math, sys
evals = (1.5e-3, 0.4e-3, 0.4e-3)
md = sum(evals) / 3.0
fa = math.sqrt(1.5 * sum((l - md) ** 2 for l in evals) / sum(l * l for l in evals))
print("  evals        %s" % ", ".join("%.4e" % v for v in evals))
print("  analytic FA  %.12f" % fa)
print("  analytic MD  %.12e" % md)
if not (0.0 < fa < 1.0):
    sys.exit("  FA must lie in (0,1)")
if abs(fa - 0.686161147707) > 1e-11:
    sys.exit("  FA closed form moved: expected 0.686161147707, got %.12f" % fa)
iso = (0.7e-3,) * 3
mdi = sum(iso) / 3.0
fai = math.sqrt(1.5 * sum((l - mdi) ** 2 for l in iso) / sum(l * l for l in iso))
print("  isotropic FA %.3e  (must be exactly 0 by construction)" % fai)
if fai != 0.0:
    sys.exit("  an isotropic tensor must give FA exactly 0")
PY

shasum -a 256 identities.py > pins.sha256
sed 's/^/  /' pins.sha256
for f in identities.py pins.sha256; do
  aws s3 cp "$f" "$BUCKET/inputs/neuroimaging/$f" --region "$REGION" --only-show-errors
done
echo "done."
echo "  $BUCKET/inputs/neuroimaging/  (no data: the truth is a closed form)"
