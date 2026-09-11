#!/usr/bin/env bash
# Stage kraken2's inputs: a prebuilt viral DB + the SARS-CoV-2 reference genome.
#
# FETCHED, not derived (see practices/what-this-does-not-cover.md) — and the DB is the
# clearest case of why that distinction matters: a kraken2-build REBUILD would NOT
# reproduce these bytes (the source library moves, the build isn't byte-deterministic).
# So the viral DB is the *published* dated artifact from genome-idx (Ben Langmead's
# prebuilt indexes), reproducible only while genome-idx keeps k2_viral_20240605. It is
# served .tar.gz; the recipe uses the flat .tar (a directory DB travels as one tar), so
# gunzip it. NC_045512.2 is NCBI efetch, which reproduces its pin exactly. Both verified.
set -euo pipefail

BUCKET="${1:?pass your bucket -- make stage RECIPE=NAME does this}"
DB_URL="https://genome-idx.s3.amazonaws.com/kraken/k2_viral_20240605.tar.gz"
NC_URL="https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi?db=nuccore&id=NC_045512.2&rettype=fasta&retmode=text"
WORK="$(mktemp -d)"; trap 'rm -rf "$WORK"' EXIT; cd "$WORK"

echo "== fetch NC_045512.2 (NCBI efetch) =="
curl -fsSL "$NC_URL" -o NC_045512.2.fasta

echo "== fetch the prebuilt viral DB (~0.5 GB) and gunzip to the flat .tar the recipe uses =="
curl -fsSL "$DB_URL" -o k2_viral.tar.gz
gunzip -c k2_viral.tar.gz > viral_20240605.tar

echo "== verify pins =="
# The DB .tar pin is TIER 3 (a moving artifact): genome-idx repacks its prebuilt DBs, so a
# future fetch may give different bytes with the same taxonomy. This pin records the version
# we ran and reconfirmed (SARS-CoV-2 -> taxid 2697049); if it stops matching, re-fetch and
# repin after re-running the recipe's taxon assertion. NC_045512.2 (efetch) is byte-stable.
cat > pins.sha256 <<'EOF'
9cbf9ddc5b88c33f3103b56defb66b966c89f47c0988757077c651bfa6d92f6c  viral_20240605.tar
0891c00cc2d503f58e0c600ce5ee64e9c64b939525c70d5486bc46cbc7cb073e  NC_045512.2.fasta
EOF
shasum -a 256 -c pins.sha256 2>/dev/null || sha256sum -c pins.sha256

aws s3 cp viral_20240605.tar "s3://$BUCKET/inputs/kraken2/viral_20240605.tar" --only-show-errors
aws s3 cp NC_045512.2.fasta  "s3://$BUCKET/inputs/kraken2/NC_045512.2.fasta"  --only-show-errors
echo "staged inputs/kraken2/ (fetched + sha256-verified)"
