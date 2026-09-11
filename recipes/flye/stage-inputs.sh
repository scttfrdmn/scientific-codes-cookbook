#!/usr/bin/env bash
# Stage Flye's toy E. coli dataset (reads + reference) for recipes/flye.
#
# FETCHED, not derived: Flye's conda package ships no test data, so the toy set is
# pulled from Flye's own repo at the tag matching the image (2.9.6). This reproduces
# only as long as github.com/mikolmogorov/Flye keeps that tag's blobs — a different
# guarantee than a derived input (see practices/what-this-does-not-cover.md). The
# sha256 verify below is what makes "fetched" trustworthy: wrong bytes fail loudly.
set -euo pipefail

BUCKET="${1:?pass your bucket -- make stage RECIPE=NAME does this}"
BASE="https://raw.githubusercontent.com/mikolmogorov/Flye/2.9.6/flye/tests/data"
WORK="$(mktemp -d)"; trap 'rm -rf "$WORK"' EXIT; cd "$WORK"

echo "== fetch the toy reads + reference from the Flye 2.9.6 tag =="
curl -fsSL "$BASE/ecoli_500kb_reads.fastq.gz" -o ecoli_500kb_reads.fastq.gz
curl -fsSL "$BASE/ecoli_500kb.fasta"          -o ecoli_500kb.fasta

echo "== verify against pinned sha256 =="
cat > pins.sha256 <<'EOF'
65b7cbd9fb7ce90958d821d85c96ea078ee3ed4d446e02a07a9c1d29767dcbe2  ecoli_500kb_reads.fastq.gz
de2efb0bdf2e880b769b53777fd6d300cf8b65a82dbb42ca6c8a0d279a97aa76  ecoli_500kb.fasta
EOF
shasum -a 256 -c pins.sha256 2>/dev/null || sha256sum -c pins.sha256

aws s3 cp ecoli_500kb_reads.fastq.gz "s3://$BUCKET/inputs/flye/ecoli_500kb_reads.fastq.gz" --only-show-errors
aws s3 cp ecoli_500kb.fasta          "s3://$BUCKET/inputs/flye/ecoli_500kb.fasta"          --only-show-errors
echo "staged inputs/flye/ (fetched from the Flye 2.9.6 tag, sha256-verified)"
