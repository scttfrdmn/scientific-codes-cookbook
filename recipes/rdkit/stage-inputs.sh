#!/usr/bin/env bash
# Stage 100,000 ChEMBL compounds WITH ChEMBL's own published InChI and InChIKey.
#
# WHY THIS FILE. chembl_37_chemreps.txt.gz carries four columns -- chembl_id,
# canonical_smiles, standard_inchi, standard_inchi_key -- so it ships the answer alongside
# the question. A toolkit can be asked to regenerate the InChIKey from the SMILES and checked
# against ChEMBL's, which turns "compute a descriptor" into "reproduce a published
# identifier". Both RDKit and Open Babel read this same subset, so they are also checked
# against each other on identical bytes.
#
# An InChIKey comparison needs no tolerance: InChI is canonical by construction, so the
# answer is a string that either matches or does not.
#
# PINNING. The release directory (releases/chembl_37/) is immutable, so the release is the
# durable id. The subset is the FIRST 100,000 data rows -- deterministic, and reproducible by
# anyone from the same release. ChEMBL ids are roughly registration-ordered, so this is an
# older slice of the database rather than a random sample; that is a property of the subset,
# not a problem with it.
set -euo pipefail

BUCKET="${1:?pass your bucket -- make stage RECIPE=NAME does this}"
REL="chembl_37"
SRC="https://ftp.ebi.ac.uk/pub/databases/chembl/ChEMBLdb/releases/${REL}/${REL}_chemreps.txt.gz"
N=100000
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
cd "$WORK"

curl -sSf -o chemreps.txt.gz "$SRC"

# head would SIGPIPE gzip under pipefail; awk with a line limit reads to EOF.
gzip -dc chemreps.txt.gz | awk -v n="$N" 'NR<=n+1' | gzip -n -6 > chembl37_first100k.tsv.gz

ROWS=$(gzip -dc chembl37_first100k.tsv.gz | awk 'END{print NR-1}')
HDR=$(gzip -dc chembl37_first100k.tsv.gz | awk -F'\t' 'NR==1{print NF}')
[ "$ROWS" -eq "$N" ] || { echo "got $ROWS data rows, wanted $N" >&2; exit 1; }
[ "$HDR" -eq 4 ] || { echo "expected 4 columns, got $HDR" >&2; exit 1; }
# Every row must carry a SMILES and a 27-character InChIKey, or the comparison has nothing
# to compare against on that row.
BAD=$(gzip -dc chembl37_first100k.tsv.gz | awk -F'\t' 'NR>1 && (length($2)==0 || length($4)!=27)' | wc -l | tr -d ' ')
[ "$BAD" -eq 0 ] || { echo "$BAD rows lack a SMILES or a 27-char InChIKey" >&2; exit 1; }

aws s3 cp chembl37_first100k.tsv.gz "s3://$BUCKET/inputs/chembl/chembl37_first100k.tsv.gz" --only-show-errors
echo "--- pin (record in README.md):"
sha256sum chembl37_first100k.tsv.gz
echo "--- $ROWS compounds, $HDR columns, every row with a SMILES and a 27-char InChIKey"
