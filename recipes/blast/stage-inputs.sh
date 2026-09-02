#!/usr/bin/env bash
# Stage this recipe's two inputs: the Ensembl 116 human proteome (verbatim), which
# becomes the BLAST database, and the first 20 proteins from it as queries.
#
# Queries are drawn FROM the database on purpose. Every query then has a guaranteed
# exact self-hit, which gives the smoke check a deterministic assertion — "each
# query's best hit is itself at 100% identity" — rather than a threshold that has
# to be guessed. See README.md for why that matters here.
#
# Ensembl release-116/ is an immutable path. current_*/ is not, and would not
# qualify as a pinnable input.
set -euo pipefail

BUCKET="${1:-scicookbook-942542972736-us-east-1}"
PEP="https://ftp.ensembl.org/pub/release-116/fasta/homo_sapiens/pep/Homo_sapiens.GRCh38.pep.all.fa.gz"
QUERIES=20
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
cd "$WORK"

curl -sSf -o pep.fa.gz "$PEP"
PROT=$(gzip -dc pep.fa.gz | grep -c '^>')
[ "$PROT" -gt 50000 ] || { echo "proteome too small ($PROT)" >&2; exit 1; }

# First $QUERIES complete records: stop at the header of record $QUERIES+1.
{ gzip -dc pep.fa.gz || true; } \
  | awk -v n="$QUERIES" '/^>/ {c++; if (c>n) exit} {print}' > queries.fa
GOT=$(grep -c '^>' queries.fa)
[ "$GOT" -eq "$QUERIES" ] || { echo "got $GOT queries, wanted $QUERIES" >&2; exit 1; }
# No empty sequences, and no query is only a header.
[ "$(grep -vc '^>' queries.fa)" -ge "$QUERIES" ] || { echo "a query has no sequence" >&2; exit 1; }

aws s3 cp pep.fa.gz  "s3://$BUCKET/inputs/blast/ensembl116_pep.fa.gz" --only-show-errors
aws s3 cp queries.fa "s3://$BUCKET/inputs/blast/queries20.fa" --only-show-errors
echo "--- pins (record these in README.md):"
sha256sum pep.fa.gz queries.fa
echo "--- db proteins: $PROT   queries: $QUERIES"
