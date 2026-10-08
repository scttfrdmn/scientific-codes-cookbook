#!/usr/bin/env bash
# Stage ViennaRNA's own test input and its committed MFE gold files.
#
# WHY THESE HAVE TO BE STAGED. The bioconda package ships the RNAfold binary but NOT the test
# suite, so the reference outputs are not in the image. They are fetched from the git tag that
# matches the image's version -- v2.7.2 -- because a gold file from another release is a
# different expected output. Staging a pinned file is allowed where a package's build
# constraints exclude the data.
#
# WHY SEVEN GOLDS AND NOT ONE. Each corresponds to a different RNAfold mode: four dangling-end
# treatments, --noLP, and two temperatures. Their sha256s are all DIFFERENT, which is the
# property that makes the comparison discriminating -- an implementation that ignored -d or -T
# would still match one gold while failing the rest. Asserted below rather than assumed.
set -euo pipefail

BUCKET="s3://${1:?pass your bucket -- make stage RECIPE=viennarna does this}"
REGION="${AWS_REGION:-us-west-2}"
TAG=v2.7.2
RAW="https://raw.githubusercontent.com/ViennaRNA/ViennaRNA/$TAG"

tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT
cd "$tmp"

if aws s3 ls "$BUCKET/inputs/viennarna/rnafold.small.seq" --region "$REGION" >/dev/null 2>&1; then
  echo "ViennaRNA test data already staged"; exit 0
fi

cat > pins.sha256 <<'EOF'
d6bb9afb4064d39ce42667a2af4ca5666a6d2fd98b5e01a5ab8cdfa2c3f2a762  rnafold.small.seq
08a0e4ecbf81f74ced478b4734156d76989285e15fa541dfa93aaca2534488e8  rnafold.small.d0.mfe.gold
a3341eae3cda19a46870edd0861bd32bd4a54a6a6de2444f8f86b8b887804dd2  rnafold.small.d1.mfe.gold
15b269b93003e71f3d1063e2e91ef3e1304041ddd87d13f8a691477a3d043167  rnafold.small.d2.mfe.gold
ae85a8e8fdc86bcdeff1f28d59bafb6630a5a2fa93c3197912d2f8c2a1a9899e  rnafold.small.d3.mfe.gold
75c4b5c971d3fb64746ad628498b01bff59a63c5e39e28cf708de890f93a8e31  rnafold.small.noLP.mfe.gold
ae065f4fdc0abc864d3a86cc6eb0e75937b8f9db62dfd0309cf363618050c724  rnafold.small.T25.mfe.gold
47437efb66e8dc3952aa88f1b3b4726319a89000775e852cc0a6b72cb4302c69  rnafold.small.T40.mfe.gold
EOF

echo "== fetch the input and seven golds from tag $TAG =="
curl -fsSL -o rnafold.small.seq "$RAW/tests/data/rnafold.small.seq"
for g in d0 d1 d2 d3 noLP T25 T40; do
  curl -fsSL -o "rnafold.small.$g.mfe.gold" "$RAW/tests/RNAfold/results/rnafold.small.$g.mfe.gold"
done
shasum -a 256 -c pins.sha256 >/dev/null || { echo "a file does not match its pin -- upstream changed" >&2; exit 1; }
echo "  OK: all 8 files match their pins"

echo "== the golds must be mutually distinct, or the comparison proves nothing =="
n_gold=$(ls rnafold.small.*.mfe.gold | wc -l | tr -d ' ')
n_uniq=$(shasum -a 256 rnafold.small.*.mfe.gold | awk '{print $1}' | sort -u | wc -l | tr -d ' ')
echo "  $n_gold golds, $n_uniq distinct"
test "$n_gold" = "$n_uniq" || { echo "two golds are byte-identical; the modes are not being discriminated" >&2; exit 1; }

echo "== how many sequences, and how long =="
nseq=$(grep -cvE '^[>;]|^$' rnafold.small.seq)
echo "  sequences: $nseq"
test "$nseq" -gt 20 || { echo "expected more than 20 sequences" >&2; exit 1; }

for f in rnafold.small.seq rnafold.small.*.mfe.gold pins.sha256; do
  aws s3 cp "$f" "$BUCKET/inputs/viennarna/$f" --region "$REGION" --only-show-errors
done
echo "done."
echo "  $BUCKET/inputs/viennarna/  ($nseq sequences, 7 mode-specific golds, all pinned)"
