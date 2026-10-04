#!/usr/bin/env bash
# Stage PROJ's own geodetic reference vectors, version-matched to the image's PROJ.
#
# The conda `proj` package ships the `gie` runner but not the test files, so the recipe
# stages them from the PROJ source at the tag matching the image (9.8.1). That turns
# "reproject a coordinate and get a number" into "reproduce the upstream project's own
# committed expected coordinates" -- the same sourcing move as SIESTA's pseudopotentials
# (see practices/reference-from-tests.md). The version match is load-bearing: expected
# values from another release are a different reference.
#
# The twelve files below are the grid-free ones. `more_builtins.gie` is deliberately NOT
# staged: 9 of its 174 tests need proj-data grid files that the conda package strips, so
# including it would ship a recipe with 9 expected failures and teach readers to ignore a
# red line. Knowing which upstream tests need data the package drops is part of the
# sourcing, not an afterthought.
#
# The raster half of this recipe reads the Sentinel-2 band already staged by
# recipes/earth-observation -- deliberately not a second copy. A second copy of a derived
# input is a second thing to keep true, and here the two recipes genuinely want the same
# bytes.
set -euo pipefail

BUCKET="s3://${1:?pass your bucket -- make stage RECIPE=NAME does this}"
PROJ_TAG="9.8.1"
BASE="https://raw.githubusercontent.com/OSGeo/PROJ/${PROJ_TAG}/test/gie"

tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT
cd "$tmp"

echo "== fetch PROJ ${PROJ_TAG} gie reference vectors =="
for f in adams_hemi adams_ws1 adams_ws2 axisswap builtins ellipsoid epsg_no_grid \
         GDA guyou peirce_q spilhaus unitconvert; do
  curl -fsSL "$BASE/$f.gie" -o "$f.gie"
done

echo "== verify against pinned sha256s =="
cat > pins.sha256 <<'EOF'
7aa0a221bce6c9c9c59efa76ba970ecc3b1871c043c595d5d7793c9b878ce1c7  adams_hemi.gie
f9239e34461efc5aff3c61ee2b66be58e7ec8b5b071f7bc5b735f1dccc7bbccb  adams_ws1.gie
c27a04e904c170f7c28ee788baae1cf296ae5bb5549df77bc80a8145d86e4ae3  adams_ws2.gie
548e2840680743f50c33f7bed1c2e7fac021d323e9da37afcb8cab321299cae8  axisswap.gie
42dc4e6c7350fad4319bb1d05759b63e880f98488a79664d434325d20bc5f6c0  builtins.gie
872ba0642e60e768747b4f05c698aebe9dfd2592373e1819569939d0a20bbcc7  ellipsoid.gie
acec30144b59cdda117572957deb9b3118b764edf429570c305af2e94bc0571b  epsg_no_grid.gie
f668775e81337cd9edb1ef12e8ee1dae1ddd523cc97488529f9ad30d01e12554  GDA.gie
369632eb538a99d441416ccd7e67aa2fbb2a3495ba289768546f5f2ff65a7a5b  guyou.gie
6f2042ebde75b99aadcc7e855f7df352de4f716f224ca4c8981c0dac6cd3956d  peirce_q.gie
97fba8885c2ecf0cdee8a0be7ef7d6cbb89398e06e29a271fc2753364160e441  spilhaus.gie
9bfd7234dcbcf89fda5e1d14f9dc2868d4b0bafa5b57949a5563db8ee0c8edb6  unitconvert.gie
EOF
shasum -a 256 -c pins.sha256

echo "== tar to ONE flat file =="
# A directory cannot be a spawn output on the container path and a directory of inputs
# costs twelve manifest entries, so this travels as one file and is untarred in the task
# (practices/container-path.md).
tar -cf gie.tar *.gie
echo "  gie.tar: $(ls -l gie.tar | awk '{print $5}') B, $(tar -tf gie.tar | wc -l | tr -d ' ') files"

echo "== upload =="
aws s3 cp gie.tar "$BUCKET/inputs/geospatial/gie.tar" --only-show-errors
echo "  -> $BUCKET/inputs/geospatial/gie.tar"

echo "== confirm the raster this recipe borrows is already staged =="
# Fail loudly here rather than on the box: the raster half reads earth-observation's B04.
if ! aws s3 ls "$BUCKET/inputs/earth-observation/B04.tif" >/dev/null 2>&1; then
  echo "B04.tif is not staged -- run 'make stage RECIPE=earth-observation' first" >&2
  exit 1
fi
echo "  OK: $BUCKET/inputs/earth-observation/B04.tif"
echo "done."
