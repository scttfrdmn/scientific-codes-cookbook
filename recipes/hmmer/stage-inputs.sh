#!/usr/bin/env bash
# Stage this recipe's two inputs: all of Pfam-A 38.2, and one protein per human
# gene from Ensembl 116 as the search target.
#
# Both upstream paths are versioned and therefore immutable: Pfam
# releases/Pfam38.2/ and Ensembl release-116/. The mutable sibling paths
# (Pfam current_release/, Ensembl current_*/) are deliberately NOT used — they
# cannot be pinned, so by the project's own rule they do not qualify as inputs.
set -euo pipefail

BUCKET="${1:?pass your bucket -- make stage RECIPE=NAME does this}"
PFAM="https://ftp.ebi.ac.uk/pub/databases/Pfam/releases/Pfam38.2/Pfam-A.hmm.gz"
PEP="https://ftp.ensembl.org/pub/release-116/fasta/homo_sapiens/pep/Homo_sapiens.GRCh38.pep.all.fa.gz"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
cd "$WORK"

# Models: the whole release, verbatim. An HMMER3 model ends with a line that is
# exactly "//", so counting those counts families.
curl -sSf -o Pfam-A.hmm.gz "$PFAM"
MODELS=$(gzip -dc Pfam-A.hmm.gz | grep -c '^//$')
[ "$MODELS" -gt 20000 ] || { echo "only $MODELS models — truncated download?" >&2; exit 1; }
NAMES=$(gzip -dc Pfam-A.hmm.gz | grep -c '^NAME ')
GA=$(gzip -dc Pfam-A.hmm.gz | grep -c '^GA ')
[ "$NAMES" -eq "$MODELS" ] || { echo "NAME count $NAMES != $MODELS" >&2; exit 1; }
# --cut_ga in the task needs every model to carry a curated gathering threshold.
[ "$GA" -eq "$MODELS" ] || { echo "GA count $GA != $MODELS — --cut_ga would fail" >&2; exit 1; }

# Targets: ONE protein per gene, the longest, which is what Pfam annotation
# actually runs on. The whole file is 382,428 sequences because it carries every
# isoform; annotating all of them answers a question nobody asks and costs ~19x
# more. Ensembl pep headers carry `gene:ENSG…`, so the rule is deterministic:
# longest sequence per gene, ties broken by the lexicographically smallest
# protein id, so the output does not depend on input order.
curl -sSf -o pep_all.fa.gz "$PEP"
gzip -dc pep_all.fa.gz | awk '
  /^>/ { if (id != "") print gene"\t"len"\t"id"\t"seq
         id = substr($1, 2); gene = ""; len = 0; seq = ""
         for (i = 2; i <= NF; i++) if ($i ~ /^gene:/) gene = substr($i, 6)
         next }
  { seq = seq $0; len += length($0) }
  END { if (id != "") print gene"\t"len"\t"id"\t"seq }
' | sort -t"$(printf '\t')" -k1,1 -k2,2nr -k3,3 \
  | awk -F'\t' '$1 != prev { print ">"$3" gene:"$1"\n"$4; prev = $1 }' \
  | gzip -n -6 > pep_one_per_gene.fa.gz

GENES=$(gzip -dc pep_one_per_gene.fa.gz | grep -c '^>')
ALL=$(gzip -dc pep_all.fa.gz | grep -c '^>')
[ "$GENES" -gt 15000 ] || { echo "only $GENES genes — selection failed" >&2; exit 1; }
[ "$GENES" -lt "$ALL" ] || { echo "selection kept everything — gene: tag missing?" >&2; exit 1; }

aws s3 cp Pfam-A.hmm.gz "s3://$BUCKET/inputs/hmmer/pfam38.2_A.hmm.gz" --only-show-errors
aws s3 cp pep_one_per_gene.fa.gz "s3://$BUCKET/inputs/hmmer/ensembl116_pep_one_per_gene.fa.gz" --only-show-errors
echo "--- pins (record these in README.md):"
sha256sum Pfam-A.hmm.gz pep_one_per_gene.fa.gz
echo "--- models: $MODELS   genes: $GENES   (of $ALL isoforms)"
