#!/usr/bin/env bash
# Stage CalculiX's own regression suite: 629 committed .dat.ref reference outputs.
#
# WHY THIS AND NOT A MESH WE BUILD. The suite ships its own meshes and its own expected
# results, so the recipe reproduces published numbers rather than asserting a band on one it
# invented. It also means gmsh is not needed -- which matters, because gmsh never landed in the
# fem-cfd env (requested as aarchsci#24, calculix arrived and gmsh did not).
#
# THE VERSION MATCH IS LOAD-BEARING AND DOUBLY CHECKED: the tarball path encodes ccx_2.23, and
# the task asserts the running binary reports 2.23 before trusting a single reference. A
# reference from another version is a different number.
set -euo pipefail

BUCKET="s3://${1:?pass your bucket -- make stage RECIPE=calculix does this}"
REGION="${AWS_REGION:-us-west-2}"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
TARBALL=ccx_2.23.test.tar.bz2
SHA=be2259fd9a7b990d0453b30708e1b05f2cd4b6df4a90fa96f0e4abd1ae7beaa0

tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT
cd "$tmp"

if aws s3 ls "$BUCKET/inputs/calculix/run-suite.sh" --region "$REGION" >/dev/null 2>&1; then
  echo "calculix inputs already staged"; exit 0
fi

echo "== fetch the suite from its author's site =="
curl -fsSL --max-time 300 -o "$TARBALL" "https://www.dhondt.de/$TARBALL"
printf '  %-26s %10s bytes\n' "$TARBALL" "$(wc -c < "$TARBALL")"

echo "== the publisher issues no checksum file, so the sha256 is recorded here and asserted =="
# Tier 2 in this project's terms: a versioned artifact at a stable URL. The publisher could
# replace it, but cannot silently change it without this assertion failing.
GOT=$(shasum -a 256 "$TARBALL" | cut -d' ' -f1)
echo "  got  $GOT"
echo "  want $SHA"
test "$GOT" = "$SHA" || { echo "  sha256 mismatch -- the suite was republished; re-derive before trusting it" >&2; exit 1; }

echo "== it must contain the version it claims, and the references it claims =="
tar tjf "$TARBALL" | grep -q '^\./\?CalculiX/ccx_2\.23/test/' \
  || { echo "  the archive path does not encode ccx_2.23" >&2; exit 1; }
NREF=$(tar tjf "$TARBALL" | grep -c '\.dat\.ref$')
NINP=$(tar tjf "$TARBALL" | grep -c '\.inp$')
echo "  .dat.ref references: $NREF    .inp cases: $NINP"
test "$NREF" -eq 629 || { echo "  expected 629 references, found $NREF -- re-derive the count" >&2; exit 1; }
test "$NINP" -ge "$NREF" || { echo "  fewer inputs than references" >&2; exit 1; }

echo "== and one reference value is pinned by content, as a canary =="
# If the archive is ever repacked with different numerics, this line moves and staging fails
# before an instance is paid for.
# the member name carries the ./ prefix the listing showed
tar xjf "$TARBALL" ./CalculiX/ccx_2.23/test/achtel2.dat.ref 2>/dev/null \
  || tar xjf "$TARBALL" CalculiX/ccx_2.23/test/achtel2.dat.ref
grep -qE '^ *3 +2\.463875E-04 +-1\.723861E-04 +1\.138229E-03' \
  CalculiX/ccx_2.23/test/achtel2.dat.ref \
  || { echo "  achtel2 node 3 is not the expected displacement triple" >&2; exit 1; }
echo "  achtel2 node 3: 2.463875E-04 -1.723861E-04 1.138229E-03"

cp "$SCRIPT_DIR/run-suite.sh" .
bash -n run-suite.sh || { echo "  run-suite.sh does not parse" >&2; exit 1; }

shasum -a 256 "$TARBALL" run-suite.sh > pins.sha256
printf 'dat_refs\t%s\ninp_cases\t%s\n' "$NREF" "$NINP" > expected.tsv
sed 's/^/  /' expected.tsv

for f in "$TARBALL" run-suite.sh pins.sha256 expected.tsv; do
  aws s3 cp "$f" "$BUCKET/inputs/calculix/$f" --region "$REGION" --only-show-errors
done
echo "done."
echo "  $BUCKET/inputs/calculix/  ($NREF committed references, ccx 2.23)"
