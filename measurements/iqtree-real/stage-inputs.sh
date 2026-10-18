#!/usr/bin/env bash
# Stage the iqtree-real input: Pfam 38.2 family PF01255.25 (Prenyltransf: undecaprenyl
# diphosphate synthase / cis-prenyltransferase), its whole seed alignment -- 919 sequences x 533
# columns across bacteria, archaea and eukaryotes -- plus the three task scripts.
#
# Tier: a versioned immutable release. Pfam-A.seed.gz of Pfam 38.2 is streamed from EBI straight
# into the bucket (no local disk), checked against EBI's own md5_checksums, and kept there so the
# derivation can be redone from the bucket. The family is picked by accession AND version, so the
# selection does not depend on when this runs. The Stockholm-to-FASTA conversion is the recipe's
# own (recipes/iqtree/stage-inputs.sh), so the 114-taxon fixture and this are the same kind of
# input at two scales.
set -euo pipefail

BUCKET="s3://${1:?pass your bucket}"
REGION="${AWS_REGION:-us-west-2}"
HERE="$(cd "$(dirname "$0")" && pwd)"
PREFIX="$BUCKET/inputs/iqtree-real"
REL=https://ftp.ebi.ac.uk/pub/databases/Pfam/releases/Pfam38.2
FAMILY=PF01255.25
WORK="$HOME/.xtmp/iqtree-real-stage"
[ -d "$HOME/.xtmp" ] || WORK="$(mktemp -d)"
mkdir -p "$WORK"; trap 'rm -rf "$WORK"' EXIT
cd "$WORK"

md5of() { if command -v md5sum >/dev/null; then md5sum | cut -d' ' -f1; else md5 -q; fi; }

if ! aws s3 ls --region "$REGION" "$PREFIX/Pfam-A.seed.gz" >/dev/null 2>&1; then
  curl -sSf "$REL/Pfam-A.seed.gz" | aws s3 cp --region "$REGION" --only-show-errors - "$PREFIX/Pfam-A.seed.gz"
fi
want=$(curl -sSf "$REL/md5_checksums" | awk '$2=="Pfam-A.seed.gz"{print $1}')
got=$(aws s3 cp --region "$REGION" --only-show-errors "$PREFIX/Pfam-A.seed.gz" - | md5of)
[ "$got" = "$want" ] || { echo "FAIL: Pfam-A.seed.gz md5 $got, EBI says $want" >&2; exit 1; }
echo "Pfam-A.seed.gz md5 $got == EBI md5_checksums"

# one Stockholm record, by accession.version
aws s3 cp --region "$REGION" --only-show-errors "$PREFIX/Pfam-A.seed.gz" - | gzip -dc \
  | LC_ALL=C awk -v fam="$FAMILY" '
      /^# STOCKHOLM/ {n=0; keep=0}
      {line[++n]=$0}
      /^#=GF AC/ && $3==fam {keep=1}
      /^\/\// {if (keep) {for (i=1;i<=n;i++) print line[i]; found=1}; n=0; keep=0}
      END {exit !found}' > family.sto

python3 -I - "$FAMILY" <<'PY'
import re, sys
fam = sys.argv[1]
order, aln = [], {}
for line in open("family.sto", encoding="latin-1"):
    if line.startswith("#") or line.startswith("//") or not line.strip():
        continue
    name, seq = line.split(None, 1)
    if name not in aln:
        aln[name] = ""
        order.append(name)
    aln[name] += seq.strip()
width = len(aln[order[0]])
assert all(len(aln[n]) == width for n in order), "ragged alignment"
seen = set()
with open("alignment.fa", "w") as out:
    for n in order:
        safe = re.sub(r"[^A-Za-z0-9_]", "_", n)
        assert safe not in seen, "name collision after sanitising: " + safe
        seen.add(safe)
        out.write(">%s\n%s\n" % (safe, aln[n].replace(".", "-").upper()))
print("%s: %d sequences x %d columns" % (fam, len(order), width))
PY

NTAX=$(grep -c '^>' alignment.fa)
[ "$NTAX" -eq 919 ] || { echo "FAIL: $NTAX sequences, Pfam 38.2 num_seed is 919" >&2; exit 1; }
[ "$(grep -v '^>' alignment.fa | awk '{print length}' | sort -u)" = 533 ] || { echo "FAIL: not 533 columns throughout" >&2; exit 1; }
mv alignment.fa pf01255.25_seed.fa

cp "$HERE/real.sh" "$HERE/eval.sh" "$HERE/rax.sh" .
shasum -a 256 pf01255.25_seed.fa
for f in pf01255.25_seed.fa real.sh eval.sh rax.sh; do
  grep -q " $f\$" "$HERE/pins.sha256" || { echo "FAIL: $f has no pin in pins.sha256" >&2; exit 1; }
done
grep -E ' (pf01255\.25_seed\.fa|real\.sh|eval\.sh|rax\.sh)$' "$HERE/pins.sha256" | shasum -a 256 -c
for f in pf01255.25_seed.fa real.sh eval.sh rax.sh; do
  aws s3 cp --region "$REGION" --only-show-errors "$f" "$PREFIX/$f"
done
aws s3 cp --region "$REGION" --only-show-errors "$HERE/pins.sha256" "$PREFIX/pins.sha256"
cat "$HERE/pins.sha256"
echo "staged to $PREFIX/"
