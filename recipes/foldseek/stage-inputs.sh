#!/usr/bin/env bash
# Stage 16 small, structurally diverse PDB entries as one flat tar.
#
# WHY NOT foldseek's PREBUILT DATABASE. The real `pdb100` is 2.17 GiB served from an
# UNVERSIONED Cloudflare-worker URL, so it cannot be pinned at all -- and the widely cited
# `search.foldseek.com/data/pdb100.tar.gz` mirror silently returns an HTML page with HTTP 200.
# Building a small database from individually pinned mmCIF files is both pinnable and
# hand-checkable, which is what this recipe wants.
#
# PDB ACCESSIONS ARE IMMUTABLE; PDB FILE BYTES ARE NOT. Entries get revised -- 1CRN is at
# revision 1.5, 4HHB at 4.3 -- and files.rcsb.org serves *current*. So the pin is
# accession + sha256 + the revision number from the REST API, which is cryptographic and also
# human-legible. A future revision changes the sha256 and the recipe stops, which is correct:
# re-derive the numbers rather than assume they survived.
set -euo pipefail

BUCKET="s3://${1:?pass your bucket -- make stage RECIPE=foldseek does this}"
REGION="${AWS_REGION:-us-west-2}"
IDS="1crn 1ubq 4hhb 1iep 1mbn 1pga 3chy 1aki 1bpi 2trx 1shg 1ten 1fkb 1hhp 2gb1 1rop"

tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT
cd "$tmp"

if aws s3 ls "$BUCKET/inputs/foldseek/structures.tar" --region "$REGION" >/dev/null 2>&1; then
  echo "structures already staged"; exit 0
fi

echo "== fetch 16 entries from RCSB, recording each one's revision =="
: > revisions.tsv
for id in $IDS; do
  curl -fsSL -o "$id.cif" "https://files.rcsb.org/download/$id.cif"
  U=$(echo "$id" | tr 'a-z' 'A-Z')
  rev=$(curl -fsSL "https://data.rcsb.org/rest/v1/core/entry/$U" | python3 -c "
import json,sys
h=(json.load(sys.stdin).get('pdbx_audit_revision_history') or [])
print('%s.%s' % (h[-1]['major_revision'], h[-1]['minor_revision']) if h else 'none')")
  printf '%s\t%s\n' "$id" "$rev" >> revisions.tsv
  printf '  %-6s %8s bytes  revision %s\n' "$id" "$(wc -c < "$id.cif")" "$rev"
done

echo "== every structure must parse as mmCIF and contain coordinates =="
for id in $IDS; do
  grep -q "^data_" "$id.cif" || { echo "  $id.cif has no data_ block" >&2; exit 1; }
  n=$(grep -c "^ATOM" "$id.cif" || true)
  test "$n" -gt 100 || { echo "  $id.cif has only $n ATOM records" >&2; exit 1; }
done
echo "  OK: 16 structures, all with a data_ block and >100 ATOM records"

# --no-xattrs: macOS tar otherwise stamps com.apple.provenance attributes that Linux tar
# warns about once per member on extraction. Harmless, but 16 lines of noise in every log.
tar --no-xattrs -cf structures.tar *.cif 2>/dev/null || tar -cf structures.tar *.cif
shasum -a 256 structures.tar revisions.tsv > pins.sha256
cat pins.sha256 | sed 's/^/  /'

# The expected query count travels with the data, so the task asserts against a staged number
# rather than one typed into a spec.
printf 'n_structures\t%s\n' "$(ls *.cif | wc -l | tr -d ' ')" > expected.tsv

for f in structures.tar revisions.tsv pins.sha256 expected.tsv; do
  aws s3 cp "$f" "$BUCKET/inputs/foldseek/$f" --region "$REGION" --only-show-errors
done
echo "done."
echo "  $BUCKET/inputs/foldseek/structures.tar  (16 entries, $(wc -c < structures.tar) bytes)"
