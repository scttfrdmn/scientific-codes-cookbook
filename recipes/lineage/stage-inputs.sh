#!/usr/bin/env bash
# Stage one Nextclade dataset at an IMMUTABLE tag, plus the 165 example sequences it ships.
#
# WHY A TAG AND NOT `nextclade dataset get`. A recipe here may not fetch at run time, and the
# dataset server keeps historical versions under explicit timestamps -- 25 of them, back to
# 2024-01-16 -- so a tag is a real pin rather than a moving target. `pathogen.json` also
# self-reports its own `version.tag`, which the task asserts, so the fixture carries its
# provenance and a swapped dataset cannot pass unnoticed.
#
# WHY THE EXAMPLE SEQUENCES ARE THE QUERY. They are declared in the dataset's own
# `files.examples`, so they are pinned by the same tag as the reference and the tree -- no
# second source to keep true.
#
# WHAT THIS RECIPE DELIBERATELY DOES NOT DO. The appealing check would be "reproduce the
# depositors' Pango lineages", and it is not available in pinnable form: GenBank records carry
# no Pango lineage, NCBI Virus's lineage column is NCBI-COMPUTED rather than deposited and comes
# from a non-pinnable UI query, and `pango-designation`'s authoritative lineages.csv keys on
# GISAID virus names whose sequences cannot be redistributed. You can pin the labels and never
# legally stage the bytes they label. So the check is a CROSS-CODE comparison instead, which is
# the shape this project prefers anyway.
set -euo pipefail

BUCKET="s3://${1:?pass your bucket -- make stage RECIPE=lineage does this}"
REGION="${AWS_REGION:-us-west-2}"
TAG="2026-09-07--17-10-15Z"
BASE="https://data.clades.nextstrain.org/v3/nextstrain/sars-cov-2/wuhan-hu-1/orfs/$TAG"

tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT
cd "$tmp"

if aws s3 ls "$BUCKET/inputs/lineage/dataset.tar" --region "$REGION" >/dev/null 2>&1; then
  echo "Nextclade dataset already staged"; exit 0
fi

echo "== fetch the dataset at tag $TAG =="
mkdir -p dataset
for f in pathogen.json reference.fasta genome_annotation.gff3 sequences.fasta tree.json; do
  curl -fsSL -o "dataset/$f" "$BASE/$f"
  printf '  %-26s %9s bytes\n' "$f" "$(wc -c < "dataset/$f")"
done

echo "== the dataset must self-report the tag we asked for =="
GOT=$(python3 -c "import json;print((json.load(open('dataset/pathogen.json')).get('version') or {}).get('tag',''))")
echo "  pathogen.json version.tag: $GOT"
test "$GOT" = "$TAG" || { echo "  dataset reports a different tag than requested" >&2; exit 1; }

echo "== the example set must be the one the dataset declares =="
EX=$(python3 -c "import json;print((json.load(open('dataset/pathogen.json')).get('files') or {}).get('examples',''))")
echo "  files.examples: $EX"
test "$EX" = "sequences.fasta" || { echo "  unexpected examples file" >&2; exit 1; }
NSEQ=$(grep -c '^>' dataset/sequences.fasta)
echo "  sequences: $NSEQ"
test "$NSEQ" -gt 100 || { echo "  expected >100 example sequences" >&2; exit 1; }

# The reference is the query for the self-identity check, extracted so both tools see the same
# single record rather than each slicing it differently.
head -c 200 dataset/reference.fasta | head -1 | sed 's/^/  reference header: /'

echo "== the Pango alias table, so lineage names can be compared in one namespace =="
# Pango renames a lineage with a new letter prefix once its dotted name grows too long, so
# C.* IS B.1.1.1.*, BA.* is B.1.1.529.*, and so on. Comparing the printed names without
# resolving that treats an alias as a disagreement. This file is the authoritative mapping,
# it is pinned by release tag, and -- unlike pango-designation's lineages.csv -- it keys on
# lineage prefixes rather than GISAID virus names, so it is freely redistributable.
curl -fsSL -o alias_key.json \
  "https://raw.githubusercontent.com/cov-lineages/pango-designation/v1.41/pango_designation/alias_key.json"
NAL=$(python3 -c "import json;print(len(json.load(open('alias_key.json'))))")
echo "  alias_key.json: $(wc -c < alias_key.json) bytes, $NAL entries"
test "$NAL" -gt 500 || { echo "  alias table looks truncated" >&2; exit 1; }
python3 -c "
import json,sys
d=json.load(open('alias_key.json'))
for k,v in (('C','B.1.1.1'),('AY','B.1.617.2'),('BA','B.1.1.529')):
    assert d.get(k)==v, '%s maps to %r, expected %s' % (k,d.get(k),v)
print('  spot-checked C, AY, BA against known Pango aliases')
" || exit 1

tar --no-xattrs -cf dataset.tar dataset 2>/dev/null || tar -cf dataset.tar dataset
shasum -a 256 dataset.tar alias_key.json > pins.sha256
printf 'dataset_tag\t%s\nn_sequences\t%s\n' "$TAG" "$NSEQ" > expected.tsv
cat pins.sha256 expected.tsv | sed 's/^/  /'

for f in dataset.tar alias_key.json pins.sha256 expected.tsv; do
  aws s3 cp "$f" "$BUCKET/inputs/lineage/$f" --region "$REGION" --only-show-errors
done
echo "done."
echo "  $BUCKET/inputs/lineage/dataset.tar  ($NSEQ sequences, tag $TAG)"
