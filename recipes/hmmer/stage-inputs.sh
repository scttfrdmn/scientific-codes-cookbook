#!/usr/bin/env bash
# Stage this recipe's two inputs: the first 200 Pfam 38.2 profile HMMs, and the
# Ensembl 116 human proteome (verbatim) as the search target.
#
# Both upstream paths are versioned and therefore immutable: Pfam
# releases/Pfam38.2/ and Ensembl release-116/. The mutable sibling paths
# (Pfam current_release/, Ensembl current_*/) are deliberately NOT used — they
# cannot be pinned, so by the project's own rule they do not qualify as inputs.
set -euo pipefail

BUCKET="${1:-scicookbook-942542972736-us-east-1}"
PFAM="https://ftp.ebi.ac.uk/pub/databases/Pfam/releases/Pfam38.2/Pfam-A.hmm.gz"
PEP="https://ftp.ensembl.org/pub/release-116/fasta/homo_sapiens/pep/Homo_sapiens.GRCh38.pep.all.fa.gz"
MODELS=200
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
cd "$WORK"

# Models: take the first $MODELS complete records from the front of the release.
# An HMMER3 model ends with a line that is exactly "//", so cutting after the
# $MODELS-th such line yields a valid, complete file. 12 MiB of the gzip stream
# holds far more than 200 models; `head`/awk closing the pipe is expected.
curl -sSf -r 0-12582911 -o pfam_part.gz "$PFAM"
{ gzip -dc pfam_part.gz 2>/dev/null || true; } \
  | awk -v n="$MODELS" '{print} /^\/\/$/ {c++; if (c==n) exit}' > pfam200.hmm
GOT=$(grep -c '^//$' pfam200.hmm)
[ "$GOT" -eq "$MODELS" ] || { echo "got $GOT models, wanted $MODELS" >&2; exit 1; }
# Every model must have a NAME and an ACC, and the counts must agree.
[ "$(grep -c '^NAME ' pfam200.hmm)" -eq "$MODELS" ] || { echo "NAME count wrong" >&2; exit 1; }
[ "$(grep -c '^ACC ' pfam200.hmm)" -eq "$MODELS" ] || { echo "ACC count wrong" >&2; exit 1; }
grep -q '^HMMER3/' <(head -n 1 pfam200.hmm) || { echo "not an HMMER3 file" >&2; exit 1; }

# Targets: whole proteome object, unmodified.
curl -sSf -o pep.fa.gz "$PEP"
PROT=$(gzip -dc pep.fa.gz | grep -c '^>')
[ "$PROT" -gt 50000 ] || { echo "proteome too small ($PROT)" >&2; exit 1; }

aws s3 cp pfam200.hmm "s3://$BUCKET/inputs/hmmer/pfam38.2_first200.hmm" --only-show-errors
aws s3 cp pep.fa.gz "s3://$BUCKET/inputs/hmmer/ensembl116_pep.fa.gz" --only-show-errors
echo "--- pins (record these in README.md):"
sha256sum pfam200.hmm pep.fa.gz
echo "--- models: $MODELS   proteins: $PROT"
