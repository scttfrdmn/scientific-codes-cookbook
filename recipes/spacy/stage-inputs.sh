#!/usr/bin/env bash
# Stage what the env lacks to run spaCy's OWN test suite, plus the checks.
#
# The conda package ships its tests (264 files) but none of the test requirements from spaCy's
# requirements.txt at release-v3.8.16. So those travel as pinned wheels -- all pure Python
# (py3-none-any / py2.py3-none-any), each within spaCy's own bounds -- and the task installs them
# OFFLINE from these files. Nothing else is fetched: every text in the identities is generated,
# and the model (en_core_web_sm 3.8.0) ships inside the pinned image.
set -euo pipefail

BUCKET="s3://${1:?pass your bucket -- make stage RECIPE=spacy does this}"
REGION="${AWS_REGION:-us-west-2}"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT
cd "$tmp"

# spaCy requirements.txt bounds: pytest>=5.2.0,!=7.1.0  pytest-timeout>=1.3.0,<2.0.0
# mock>=2.0.0,<3.0.0  hypothesis>=3.27.0,<6.156.0  ml_datasets>=0.2.1,<0.3.0
# plus their dependencies the env lacks (pluggy, iniconfig; pbr, six; sortedcontainers).
# ml_datasets is not a runtime dependency, but it registers three spaCy readers by entry point
# and test_registry_population asserts they exist -- without it that test fails.
cat > wheels.sha256 <<'PINS'
37a86b45efb9a47a61a36449063e8e18d0cab3161329fc099eb21783169c4f0c  pytest-9.1.1-py3-none-any.whl
e920276dd6813095e9377c0bc5566d94c932c33b27a3e3945d8389c374dd4746  pluggy-1.6.0-py3-none-any.whl
9121e2c1fdb355232495be3194c8dfe87ccc2d5dee45947b78e68f499790d7a7  iniconfig-2.3.1-py3-none-any.whl
541d7aa19b9a6b4e475c759fd6073ef43d7cdc9a92d95644c260076eb257a063  pytest_timeout-1.4.2-py2.py3-none-any.whl
5ce3c71c5545b472da17b72268978914d0252980348636840bd34a00b5cc96c1  mock-2.0.0-py2.py3-none-any.whl
6583e878a1d97cb135fdc509811f31b9235905cde8d4dacd3dbadf9efc45d745  pbr-7.1.3-py2.py3-none-any.whl
4721f391ed90541fddacab5acf947aa0d3dc7d27b2e1e8eda2be8970586c3274  six-1.17.0-py2.py3-none-any.whl
9f634bdb1f9e9b8ab6ba09431cf2deedb750c96978125a6fb3c5a0f6c6db4131  hypothesis-6.155.7-py3-none-any.whl
a163dcaede0f1c021485e957a39245190e74249897e2ae4b2aa38595db237ee0  sortedcontainers-2.4.0-py2.py3-none-any.whl
867309f08a97bd97adad0edc9872eb01bdde0fd974d4567df06a8b08b1b4d4aa  ml_datasets-0.2.1-py3-none-any.whl
PINS

echo "== ten pure-Python wheels, by exact version, checked against their pins =="
python3 -m pip download -q --no-deps --only-binary=:all: --python-version 3.14 \
  --implementation py --abi none --platform any -d . \
  pytest==9.1.1 pluggy==1.6.0 iniconfig==2.3.1 pytest-timeout==1.4.2 mock==2.0.0 pbr==7.1.3 \
  six==1.17.0 hypothesis==6.155.7 sortedcontainers==2.4.0 ml_datasets==0.2.1
shasum -a 256 -c wheels.sha256 || { echo "  a wheel does not match its pin" >&2; exit 1; }

echo "== the checks =="
cp "$SCRIPT_DIR/identities.py" "$SCRIPT_DIR/account.py" "$SCRIPT_DIR/suite_check.py" "$SCRIPT_DIR/pytest.ini" .
for f in identities.py account.py suite_check.py; do
  python3 -c "import ast,io,sys; ast.parse(io.open(sys.argv[1],encoding='utf-8').read())" "$f" \
    || { echo "  $f does not parse" >&2; exit 1; }
done
grep -q 'v = v if isinstance(v, str) else str(v)' identities.py \
  || { echo "  rec() does not str() its value" >&2; exit 1; }
grep -qx '    error' pytest.ini || { echo "  pytest.ini must keep spaCy's filterwarnings = error" >&2; exit 1; }
echo "  checks parse; rec() is str()-safe; warnings are errors"

sha256sum identities.py account.py suite_check.py pytest.ini > pins.sha256 2>/dev/null \
  || shasum -a 256 identities.py account.py suite_check.py pytest.ini > pins.sha256
for w in *.whl; do aws s3 cp --region "$REGION" --only-show-errors "$w" "$BUCKET/inputs/spacy/wheels/$w"; done
for f in wheels.sha256 identities.py account.py suite_check.py pytest.ini pins.sha256; do
  aws s3 cp --region "$REGION" --only-show-errors "$f" "$BUCKET/inputs/spacy/$f"
done
cat pins.sha256
echo "done."
echo "  $BUCKET/inputs/spacy/  (10 wheels + the checks; the suite ships in the image, no data staged)"
