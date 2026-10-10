#!/usr/bin/env bash
# Stage the checks only. CP2K's regtests -- inputs, reference values AND the tolerances they
# must be met within -- ship inside the conda package, at
# /opt/conda/etc/conda/test-files/cp2k/1/tests/*/*/TEST_FILES.toml. So there is nothing to
# fetch and nothing to pin but this script: the reference travels with the binary it describes,
# which is the version match this recipe would otherwise have to assert by hand.
set -euo pipefail

BUCKET="s3://${1:?pass your bucket -- make stage RECIPE=cp2k does this}"
REGION="${AWS_REGION:-us-west-2}"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT
cd "$tmp"
cp "$SCRIPT_DIR/identities.py" .

echo "== the checks must parse, and must not carry a hardcoded reference =="
python3 -c "import ast,io; ast.parse(io.open('identities.py',encoding='utf-8').read())" \
  || { echo "  identities.py does not parse" >&2; exit 1; }
# The point of this recipe is that the expected values come from the package at run time, so a
# literal energy in the script would be a second copy that drifts when upstream revises theirs.
#
# Checked on the AST, not with grep: a first version grepped the text and flagged its own
# DOCSTRING, which quotes `ref=-21.04944231395054` to show the manifest format. Text cannot
# tell a value used in an assertion from one quoted in a comment -- the same mistake an
# assertion in this catalog once made against its own comment.
python3 - <<'GUARD' || exit 1
import ast, io, sys
tree = ast.parse(io.open("identities.py", encoding="utf-8").read())
docstrings = set()
for node in ast.walk(tree):
    if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        d = ast.get_docstring(node, clean=False)
        if d is not None and node.body and isinstance(node.body[0], ast.Expr):
            docstrings.add(id(node.body[0].value))
bad = [n.value for n in ast.walk(tree)
       if isinstance(n, ast.Constant) and isinstance(n.value, float)
       and id(n) not in docstrings
       and len(repr(abs(n.value)).split(".")[-1]) >= 6]
if bad:
    print("  identities.py hardcodes %s -- read references from the TOML instead" % bad[:3],
          file=sys.stderr)
    sys.exit(1)
print("  no hardcoded reference in executable code (AST-checked, docstrings excluded)")
GUARD
printf '  %-16s %6s bytes, no hardcoded reference\n' identities.py "$(wc -c < identities.py)"

echo "== stage CP2K's own matchers module, from the tag matching the image =="
# The 165-entry matcher registry is how CP2K turns a manifest entry into a number. It is
# preferred from the installed package at run time; this staged copy is the fallback, pinned,
# and taken from v2026.2 to match cp2k 2026.2 in the image. A matcher registry from another
# version would extract a different quantity.
curl -fsSL -o matchers.py \
  "https://raw.githubusercontent.com/cp2k/cp2k/v2026.2/tests/matchers.py"
python3 -c "import ast,io; ast.parse(io.open('matchers.py',encoding='utf-8').read())" \
  || { echo "  matchers.py does not parse" >&2; exit 1; }
N=$(grep -c 'registry\["' matchers.py)
printf '  %-16s %6s bytes, %s matchers\n' matchers.py "$(wc -c < matchers.py)" "$N"
test "$N" -ge 100 || { echo "  only $N matchers -- expected ~165; re-derive" >&2; exit 1; }
grep -q 'registry\["E_total"\]' matchers.py \
  || { echo "  no E_total matcher, which is the one this recipe selects on" >&2; exit 1; }

echo "== stage TEST_DIRS, which decides what this build may run =="
curl -fsSL -o TEST_DIRS "https://raw.githubusercontent.com/cp2k/cp2k/v2026.2/tests/TEST_DIRS"
NQ=$(awk '$1 ~ /^QS\// {n++} END{print n+0}' TEST_DIRS)
printf '  %-16s %6s bytes, %s QS directories listed\n' TEST_DIRS "$(wc -c < TEST_DIRS)" "$NQ"
test "$NQ" -ge 100 || { echo "  only $NQ QS dirs listed -- re-derive" >&2; exit 1; }

shasum -a 256 identities.py matchers.py TEST_DIRS > pins.sha256
sed 's/^/  /' pins.sha256
for f in identities.py matchers.py TEST_DIRS pins.sha256; do
  aws s3 cp "$f" "$BUCKET/inputs/cp2k/$f" --region "$REGION" --only-show-errors
done
echo "done."
echo "  $BUCKET/inputs/cp2k/  (references ship inside the image, not here)"
