#!/usr/bin/env bash
# Stage Apache's own expected-contents files for Parquet, from parquet-testing at the submodule
# commit arrow 25.0.0 pins. The version match is load-bearing: a reference from another commit is
# a different expected value, and these CSVs were produced by parquet-mr (an independent Java
# implementation), which is what makes the comparison cross-implementation rather than Arrow
# agreeing with itself.
set -euo pipefail

BUCKET="s3://${1:?pass your bucket -- make stage RECIPE=r-arrow does this}"
REGION="${AWS_REGION:-us-west-2}"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# apache/arrow @ apache-arrow-25.0.0 : cpp/submodules/parquet-testing
PT=e74785d85a4ecee829e1e405444d6a1b24b8bc9c
RAW="https://raw.githubusercontent.com/apache/parquet-testing/${PT}/data"

tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT
cd "$tmp"
cp "$SCRIPT_DIR/identities.R" .
test -s identities.R || { echo "  identities.R is empty" >&2; exit 1; }

echo "== fetch the reference files at the pinned commit =="
for f in \
  delta_binary_packed.parquet delta_binary_packed_expect.csv \
  delta_byte_array.parquet delta_byte_array_expect.csv \
  delta_encoding_required_column.parquet delta_encoding_required_column_expect.csv \
  delta_encoding_optional_column.parquet delta_encoding_optional_column_expect.csv \
  datapage_v1-uncompressed-checksum.parquet datapage_v1-snappy-compressed-checksum.parquet \
  plain-dict-uncompressed-checksum.parquet \
  datapage_v1-corrupt-checksum.parquet rle-dict-uncompressed-corrupt-checksum.parquet
do
  curl -fsSL -o "$f" "$RAW/$f" || { echo "  could not fetch $f" >&2; exit 1; }
done

cat > refs.sha256 <<'EOF'
d1c2173fe97255959e3d087b3fa5b7b5c27b2aac135337b2896772d7bbdc31b4  delta_binary_packed.parquet
9384cc177b54ca364ffdf1e4d0390acddc55f42a0e149300934c70b4946c444b  delta_binary_packed_expect.csv
a400b789aef5cde88551f25cdd9bba8f0ff0fe01c48ddc5303c26edf119ee279  delta_byte_array.parquet
2c53dd42a37deb70f23e8463e4293a05bbe06200f55d84b346bc9c0e4ad48b85  delta_byte_array_expect.csv
36ddcb79799d56d5098f4cdb42777873a5ddd9ae6f40d8cfa316abafde6c658a  delta_encoding_required_column.parquet
6ce505cbae2a70a76edc64328394f3d9f3393b67e55f3ff218b09447636fc7e5  delta_encoding_required_column_expect.csv
71f8f00b00ecc132a1cc5d534acca900d02d0fe4ba3525607b03b2bd06a56f1a  delta_encoding_optional_column.parquet
41574273fc0b120d364be6b8f934895237e47088bd40b5700e465ab9ca86b676  delta_encoding_optional_column_expect.csv
b1d664eaba82d89b4107a2dc2b953ec33566b3bb4f902b79ed6ced7b9fff5664  datapage_v1-uncompressed-checksum.parquet
f06df378ad412ace763d129f317c52236230b2fb24073c32d2c2d5fc1ef9d697  datapage_v1-snappy-compressed-checksum.parquet
4c8abc17ad0354dc540b0ad2c519d998ffb4a1a5997471b4313864852d82dccc  plain-dict-uncompressed-checksum.parquet
b337106431c826e3326ab8fecfa5560688aa57549fd46e0fa7cfcf99cd4e2c9e  datapage_v1-corrupt-checksum.parquet
b96f9198a18ec7a389f989c7d2a170ddad74a664ddd4e009a240f21c83cceddf  rle-dict-uncompressed-corrupt-checksum.parquet
EOF
sha256sum -c refs.sha256
printf '  %s files verified at parquet-testing %s\n' "$(wc -l < refs.sha256 | tr -d '[:space:]')" "${PT:0:12}"

echo "== the references must still contain the values the recipe reproduces =="
# A pin fixes bytes, not meaning. These two facts are what the strongest checks depend on, so
# they are confirmed before an instance is paid for: the expected CSVs have the documented shape,
# and the single INT64_MIN cell is still present (it is an asserted, characterised exception --
# if upstream regenerated the file without it, the assertion would be wrong rather than passing).
python3 - <<'PY'
import csv, sys
shapes = {"delta_binary_packed_expect.csv": (200, 66),
          "delta_byte_array_expect.csv": (1000, 9),
          "delta_encoding_required_column_expect.csv": (100, 17),
          "delta_encoding_optional_column_expect.csv": (100, 17)}
for f, (rows, cols) in shapes.items():
    r = list(csv.reader(open(f)))
    got = (len(r) - 1, len(r[0]))
    if got != (rows, cols):
        sys.exit("  %s is %s, expected %s" % (f, got, (rows, cols)))
    print("  %-46s %4d x %2d" % (f, got[0], got[1]))
r = list(csv.reader(open("delta_binary_packed_expect.csv")))
n = sum(row.count("-9223372036854775808") for row in r[1:])
if n != 1:
    sys.exit("  expected exactly 1 INT64_MIN cell, found %d -- re-derive the exception" % n)
print("  exactly 1 INT64_MIN cell, as the recipe asserts")
PY

sha256sum identities.R > pins.sha256
for f in ./*.parquet ./*.csv identities.R pins.sha256; do
  aws s3 cp --region "$REGION" "$f" "$BUCKET/inputs/r-arrow/$(basename "$f")" >/dev/null
done
cat pins.sha256
echo "done."
echo "  $BUCKET/inputs/r-arrow/"
