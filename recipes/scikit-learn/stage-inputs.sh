#!/usr/bin/env bash
# Stage what the env lacks to run scikit-learn's OWN test suite, plus the checks.
#
# The conda package ships its tests (259 files) but the geo-ml env has no pytest, so pytest
# and pytest-xdist travel as pinned wheels -- pure Python (py3-none-any), so the same bytes
# serve any architecture -- and the task installs them OFFLINE from these staged files. That
# is staging a pinned file, not fetching at run time. Each wheel is pulled by exact version
# and checked against the sha256 below, so a republished wheel fails here, not in the run.
set -euo pipefail

BUCKET="s3://${1:?pass your bucket -- make stage RECIPE=scikit-learn does this}"
REGION="${AWS_REGION:-us-west-2}"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT
cd "$tmp"

cat > wheels.sha256 <<'PINS'
37a86b45efb9a47a61a36449063e8e18d0cab3161329fc099eb21783169c4f0c  pytest-9.1.1-py3-none-any.whl
e920276dd6813095e9377c0bc5566d94c932c33b27a3e3945d8389c374dd4746  pluggy-1.6.0-py3-none-any.whl
9121e2c1fdb355232495be3194c8dfe87ccc2d5dee45947b78e68f499790d7a7  iniconfig-2.3.1-py3-none-any.whl
202ca578cfeb7370784a8c33d6d05bc6e13b4f25b5053c30a152269fd10f0b88  pytest_xdist-3.8.0-py3-none-any.whl
67fba928dd5a544b783f6056f449e5e3931a5c378b128bc18501f7ea79e296ec  execnet-2.1.2-py3-none-any.whl
PINS

echo "== five pure-Python wheels, by exact version, checked against their pins =="
python3 -m pip download -q --no-deps --only-binary=:all: --python-version 3.14 \
  --implementation py --abi none --platform any -d . \
  pytest==9.1.1 pluggy==1.6.0 iniconfig==2.3.1 pytest-xdist==3.8.0 execnet==2.1.2
shasum -a 256 -c wheels.sha256 || { echo "  a wheel does not match its pin" >&2; exit 1; }
# pytest 9.1.1 clears scikit-learn 1.9.1's own floor (_min_dependencies.py: PYTEST_MIN_VERSION 7.1.2)

echo "== the checks =="
cp "$SCRIPT_DIR/identities.py" "$SCRIPT_DIR/account.py" .
for f in identities.py account.py; do
  python3 -c "import ast,io,sys; ast.parse(io.open(sys.argv[1],encoding='utf-8').read())" "$f" \
    || { echo "  $f does not parse" >&2; exit 1; }
done
grep -q 'v = v if isinstance(v, str) else str(v)' identities.py \
  || { echo "  rec() does not str() its value" >&2; exit 1; }
sha256sum identities.py account.py > pins.sha256 2>/dev/null || shasum -a 256 identities.py account.py > pins.sha256

for w in *.whl; do aws s3 cp --region "$REGION" --only-show-errors "$w" "$BUCKET/inputs/scikit-learn/wheels/$w"; done
aws s3 cp --region "$REGION" --only-show-errors wheels.sha256 "$BUCKET/inputs/scikit-learn/wheels.sha256"
aws s3 cp --region "$REGION" --only-show-errors identities.py "$BUCKET/inputs/scikit-learn/identities.py"
aws s3 cp --region "$REGION" --only-show-errors account.py    "$BUCKET/inputs/scikit-learn/account.py"
aws s3 cp --region "$REGION" --only-show-errors pins.sha256   "$BUCKET/inputs/scikit-learn/pins.sha256"
cat pins.sha256
echo "done."
echo "  $BUCKET/inputs/scikit-learn/  (5 wheels + the checks; the suite itself ships in the image)"
