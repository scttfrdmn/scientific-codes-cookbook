#!/usr/bin/env bash
# Stage the pbmc3k 10x matrix: 2,700 peripheral blood mononuclear cells.
#
# WHY FROM 10x AND NOT FROM scanpy.datasets. `sc.datasets.pbmc3k()` downloads at run time, and
# a recipe here may not fetch during a run. It also fetches from a mirror that has moved at
# least once -- the figshare link for the processed variant is now a 404. 10x Genomics is the
# data's originator, so their CDN is the durable source, and the bytes get pinned regardless.
#
# WHY pbmc3k. Single-cell clustering has no closed-form answer, so the checks have to come
# from somewhere other than the clustering itself. This dataset supplies three:
#
#   * EXACT published dimensions. 32,738 genes x 2,700 cells is a property of the deposited
#     matrix, stated in its own Matrix Market header, so a truncated or wrong download fails
#     before any science runs.
#   * KNOWN BIOLOGY. PBMCs contain T cells, monocytes and B cells, and CD3D / LYZ / MS4A1 are
#     textbook lineage markers for exactly those three. That makes "these three genes peak in
#     three different clusters" a biological claim rather than a band on a number.
#   * TWO ALGORITHMS. The image carries both leidenalg and igraph, so Leiden and Louvain can
#     partition the same kNN graph independently and be compared (practices/cross-checks.md).
set -euo pipefail

BUCKET="s3://${1:?pass your bucket -- make stage RECIPE=scanpy does this}"
REGION="${AWS_REGION:-us-west-2}"
SRC="https://cf.10xgenomics.com/samples/cell/pbmc3k/pbmc3k_filtered_gene_bc_matrices.tar.gz"
PIN=847d6ebd9a1ec9a768f2be7e40ca42cbfe75ebeb6d76a4c24167041699dc28b5

tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT

if aws s3 ls "$BUCKET/inputs/scanpy/pbmc3k.tar.gz" --region "$REGION" >/dev/null 2>&1; then
  echo "pbmc3k already staged"
  exit 0
fi

echo "== fetch from 10x Genomics, the data's originator (7.6 MB) =="
curl -fsSL --retry 3 -o "$tmp/pbmc3k.tar.gz" "$SRC"
echo "$PIN  $tmp/pbmc3k.tar.gz" | shasum -a 256 -c - >/dev/null || {
  echo "pbmc3k.tar.gz does not match its pin -- upstream changed, do not proceed" >&2; exit 1; }
echo "  OK: matches $PIN"

echo "== the matrix states its own dimensions; check them here, not on the box =="
tar -xzf "$tmp/pbmc3k.tar.gz" -C "$tmp"
H="$tmp/filtered_gene_bc_matrices/hg19"
# Matrix Market header line 3 is "<rows> <cols> <nonzeros>" = genes, cells, entries.
read -r G C NZ <<<"$(awk 'NR==3{print $1, $2, $3}' "$H/matrix.mtx")"
NB=$(wc -l < "$H/barcodes.tsv" | tr -d ' ')
NG=$(wc -l < "$H/genes.tsv" | tr -d ' ')
printf '  matrix header: %s genes x %s cells, %s non-zero\n' "$G" "$C" "$NZ"
printf '  barcodes.tsv: %s   genes.tsv: %s\n' "$NB" "$NG"
test "$G" = "32738" -a "$C" = "2700" || { echo "unexpected dimensions" >&2; exit 1; }
# The header and the two index files are independent statements of the same two numbers.
test "$NB" = "$C" -a "$NG" = "$G" || { echo "header disagrees with the index files" >&2; exit 1; }
echo "  OK: header and index files agree"

echo "== the three lineage markers the biological check needs must be in this matrix =="
for g in CD3D LYZ MS4A1; do
  grep -qw "$g" "$H/genes.tsv" || { echo "marker $g absent from genes.tsv" >&2; exit 1; }
done
echo "  OK: CD3D, LYZ, MS4A1 all present"

aws s3 cp "$tmp/pbmc3k.tar.gz" "$BUCKET/inputs/scanpy/pbmc3k.tar.gz" --region "$REGION" --only-show-errors
printf '%s  pbmc3k.tar.gz\n' "$PIN" > "$tmp/pbmc3k.tar.gz.sha256"
aws s3 cp "$tmp/pbmc3k.tar.gz.sha256" "$BUCKET/inputs/scanpy/pbmc3k.tar.gz.sha256" --region "$REGION" --only-show-errors
echo "done."
echo "  $BUCKET/inputs/scanpy/pbmc3k.tar.gz  (7.6 MB, 2,700 cells x 32,738 genes)"
