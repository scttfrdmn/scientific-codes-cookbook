#!/usr/bin/env bash
# Stage what the env lacks to run Nilearn's OWN test suite, plus the checks.
#
# The conda package ships its tests (135 modules) and the env now carries their non-pytest
# dependencies (polars, pyarrow, numpydoc -- playgroundlogic/aarchsci#43), but no pytest. So
# pytest travels as pinned wheels -- pure Python (py3-none-any): the five scikit-learn's recipe
# stages, plus pytest-timeout and pytest-rerunfailures from Nilearn's own test group (tox.ini's
# --timeout=60, and the `flaky` marker) -- and the task installs them OFFLINE from these files. Nothing
# else is fetched: every image, design and time series in the identities is synthesised.
set -euo pipefail

BUCKET="s3://${1:?pass your bucket -- make stage RECIPE=nilearn does this}"
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
c42667e5cdadb151aeb5b26d114aff6bdf5a907f176a007a30b940d3d865b5c2  pytest_timeout-2.4.0-py3-none-any.whl
edf1886209c2b7dafe35b5bf1708d6ec40ccf6c6b357f0f02807efcec0204c99  pytest_rerunfailures-16.7-py3-none-any.whl
PINS

echo "== seven pure-Python wheels, by exact version, checked against their pins =="
python3 -m pip download -q --no-deps --only-binary=:all: --python-version 3.14 \
  --implementation py --abi none --platform any -d . \
  pytest==9.1.1 pluggy==1.6.0 iniconfig==2.3.1 pytest-xdist==3.8.0 execnet==2.1.2 \
  pytest-timeout==2.4.0 pytest-rerunfailures==16.7
shasum -a 256 -c wheels.sha256 || { echo "  a wheel does not match its pin" >&2; exit 1; }
# pytest 9.1.1 clears Nilearn 0.14.1's own floor (pyproject.toml test_common: pytest>=6.0.0)

echo "== the checks =="
cp "$SCRIPT_DIR/identities.py" "$SCRIPT_DIR/account.py" "$SCRIPT_DIR/order.py" "$SCRIPT_DIR/suite_check.py" "$SCRIPT_DIR/pytest.ini" .
for f in identities.py account.py order.py suite_check.py; do
  python3 -c "import ast,io,sys; ast.parse(io.open(sys.argv[1],encoding='utf-8').read())" "$f" \
    || { echo "  $f does not parse" >&2; exit 1; }
done
grep -q 'v = v if isinstance(v, str) else str(v)' identities.py \
  || { echo "  rec() does not str() its value" >&2; exit 1; }
grep -q 'signal_scaling=False' identities.py \
  || { echo "  the GLM must set signal_scaling=False, or effect sizes are in percent signal change" >&2; exit 1; }
grep -q 'cov_estimator=EmpiricalCovariance()' identities.py \
  || { echo "  the Pearson comparison must use EmpiricalCovariance; the default is Ledoit-Wolf" >&2; exit 1; }
grep -q '^xfail_strict = true' pytest.ini || { echo "  pytest.ini must keep Nilearn's xfail_strict" >&2; exit 1; }
echo "  checks parse; GLM unscaled, correlation empirical, xfail strict"

sha256sum identities.py account.py order.py suite_check.py pytest.ini > pins.sha256 2>/dev/null \
  || shasum -a 256 identities.py account.py order.py suite_check.py pytest.ini > pins.sha256
for w in *.whl; do aws s3 cp --region "$REGION" --only-show-errors "$w" "$BUCKET/inputs/nilearn/wheels/$w"; done
for f in wheels.sha256 identities.py account.py order.py suite_check.py pytest.ini pins.sha256; do
  aws s3 cp --region "$REGION" --only-show-errors "$f" "$BUCKET/inputs/nilearn/$f"
done
cat pins.sha256
echo "done."
echo "  $BUCKET/inputs/nilearn/  (7 wheels + the checks; the suite ships in the image, no data staged)"
