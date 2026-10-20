#!/usr/bin/env bash
# Stage kallisto-real. No new data: the reads (ERR188026) are the bytes salmon-real staged, the index
# is the one recipes/kallisto built from salmon-real's Ensembl 116 cDNA, and the cross-check reads
# salmon-real's and rsem-real's own outputs in place.
#
# DATA TIER: durable public ids, plus one built intermediate.
#   ERR188026 (ENA, Geuvadis, 15,800,127 pairs), pinned by ENA's fastq_md5 (reads.md5, checked on
#   every box). Ensembl release-116 Homo_sapiens.GRCh38.cdna.all.fa.gz, pinned by Ensembl's CHECKSUMS
#   when salmon-real staged it. The kallisto index (build tier, recipes/kallisto task 01) is hashed on
#   every box and task 3 asserts all three chips read one file. The sibling outputs are pinned in
#   siblings.sha256 and checked before task 3 reads them.
#
# Only the scripts are staged here (EC2 user data is 16 KiB, spawn#1063); each spec pins them by sha256.
#   stage-inputs.sh <bucket>
set -euo pipefail
BUCKET="s3://${1:?pass your bucket}"
HERE="$(cd "$(dirname "$0")" && pwd)"
aws s3 ls "$BUCKET/inputs/kallisto/kallisto.idx" >/dev/null || { echo "run 'make stage RECIPE=kallisto' first" >&2; exit 1; }
for f in quant.sh reads.md5 check.R siblings.sha256; do
  aws s3 cp --only-show-errors "$HERE/$f" "$BUCKET/inputs/kallisto-real/$f"
  shasum -a 256 "$HERE/$f"
done
