#!/usr/bin/env bash
# Stage what the env lacks to run NumPy's, SciPy's and pandas' OWN test suites, plus the checks.
#
# numpy and pandas ship their tests inside the conda package; scipy does not -- conda-forge splits
# them into a separate `scipy-tests` output, which the geo-ml env does not carry. So that package
# travels too, the exact build that pins the image's scipy (`scipy 1.18.1 py314hb3ba6a9_1`), by the
# sha256 conda-forge publishes for it. The env also has no pytest, so the runner travels as pinned
# pure-Python wheels (py3-none-any), each inside every project's own bounds:
#   numpy 2.5.4   requirements/test_requirements.txt: hypothesis==6.152.1, pytest-xdist, pytest-timeout, tzdata,
#                 meson (its f2py tests call `meson` at import to probe for compilers, and error without it)
#   scipy 1.18.1  [tool.cibuildwheel] test-requires: pytest, pytest-xdist, threadpoolctl, pooch, hypothesis
#   pandas 3.0.6  [project.optional-dependencies] test: hypothesis>=6.116.0, pytest>=8.3.4,<9.1, pytest-xdist>=3.6.1
# pandas' `<9.1` is why pytest is 9.0.3, not numpy's CI pin of 9.1.0. threadpoolctl, pooch, mpmath,
# gmpy2, sortedcontainers, packaging and pygments are already in the env.
# The task installs all of it OFFLINE from these files -- staging a pinned file, not fetching.
set -euo pipefail

BUCKET="s3://${1:?pass your bucket -- make stage RECIPE=scipy-stack does this}"
REGION="${AWS_REGION:-us-west-2}"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SCIPY_TESTS=scipy-tests-1.18.1-py314hb3ba6a9_1.conda

tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT
cd "$tmp"

cat > packages.sha256 <<'PINS'
2c5efc453d45394fdd706ade797c0a81091eccd1d6e4bccfcd476e2b8e0ab5d9  pytest-9.0.3-py3-none-any.whl
e920276dd6813095e9377c0bc5566d94c932c33b27a3e3945d8389c374dd4746  pluggy-1.6.0-py3-none-any.whl
9121e2c1fdb355232495be3194c8dfe87ccc2d5dee45947b78e68f499790d7a7  iniconfig-2.3.1-py3-none-any.whl
40a3619d9e0cb97b018857c7986f75cf5de2e5ec0fa8a0b172d00747758f749e  hypothesis-6.152.1-py3-none-any.whl
202ca578cfeb7370784a8c33d6d05bc6e13b4f25b5053c30a152269fd10f0b88  pytest_xdist-3.8.0-py3-none-any.whl
67fba928dd5a544b783f6056f449e5e3931a5c378b128bc18501f7ea79e296ec  execnet-2.1.2-py3-none-any.whl
c42667e5cdadb151aeb5b26d114aff6bdf5a907f176a007a30b940d3d865b5c2  pytest_timeout-2.4.0-py3-none-any.whl
b683bd1b6659ddcd810ff02ad09ba821d4bf1065072805063eb35c49617905ac  tzdata-2026.5-py2.py3-none-any.whl
930bc7542cbd9f57009e182fd014eba48cf1a0180a7b9006d2d32d8f168d8b02  meson-1.12.1-py3-none-any.whl
046508ea4ff3b8866eef0a7cd67bac63e96783c5279473202cca818596761134  scipy-tests-1.18.1-py314hb3ba6a9_1.conda
PINS

echo "== nine pure-Python wheels and scipy's companion test package, checked against their pins =="
python3 -m pip download -q --no-deps --only-binary=:all: --python-version 3.14 \
  --implementation py --abi none --platform any -d . \
  pytest==9.0.3 pluggy==1.6.0 iniconfig==2.3.1 hypothesis==6.152.1 pytest-xdist==3.8.0 \
  execnet==2.1.2 pytest-timeout==2.4.0 tzdata==2026.5 meson==1.12.1
curl -sfL -o "$SCIPY_TESTS" "https://conda.anaconda.org/conda-forge/linux-aarch64/$SCIPY_TESTS"
shasum -a 256 -c packages.sha256 || { echo "  a package does not match its pin" >&2; exit 1; }

echo "== the checks =="
cp "$SCRIPT_DIR/account.py" "$SCRIPT_DIR/suite_check.py" .
for f in account.py suite_check.py; do
  python3 -c "import ast,io,sys; ast.parse(io.open(sys.argv[1],encoding='utf-8').read())" "$f" \
    || { echo "  $f does not parse" >&2; exit 1; }
done
sha256sum account.py suite_check.py > pins.sha256 2>/dev/null || shasum -a 256 account.py suite_check.py > pins.sha256

for f in *.whl "$SCIPY_TESTS"; do aws s3 cp --region "$REGION" --only-show-errors "$f" "$BUCKET/inputs/scipy-stack/pkgs/$f"; done
for f in packages.sha256 account.py suite_check.py pins.sha256; do
  aws s3 cp --region "$REGION" --only-show-errors "$f" "$BUCKET/inputs/scipy-stack/$f"
done
cat pins.sha256
echo "done."
echo "  $BUCKET/inputs/scipy-stack/  (9 wheels + scipy-tests + the checks; numpy's and pandas' suites ship in the image)"
