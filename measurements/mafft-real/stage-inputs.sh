#!/usr/bin/env bash
# Stage the mafft-real inputs: Pfam 38.2 family PF01255.25 (Prenyltransf), its whole seed --
# 919 sequences across bacteria, archaea and eukaryotes -- as two files, plus the task script.
#
#   pf01255.25_unaligned.fa  the seed's residues with every gap removed: what mafft aligns
#   pf01255.25_ref.fa        Pfam's curated seed alignment, as released (all residues uppercase,
#                            gaps '.'): the reference the alignment is scored against
#
# Tier: a versioned immutable release. Pfam-A.seed.gz is the copy measurements/iqtree-real
# already staged (md5-checked against EBI there and again here), streamed from the bucket, never
# written to local disk. The reference, uppercased with '.' -> '-', must be byte-identical to
# iqtree-real's pf01255.25_seed.fa: this is the family iqtree-real built its tree from.
set -euo pipefail

BUCKET="s3://${1:?pass your bucket}"
REGION="${AWS_REGION:-us-west-2}"
HERE="$(cd "$(dirname "$0")" && pwd)"
PREFIX="$BUCKET/inputs/mafft-real"
SEED="$BUCKET/inputs/iqtree-real/Pfam-A.seed.gz"
REL=https://ftp.ebi.ac.uk/pub/databases/Pfam/releases/Pfam38.2
FAMILY=PF01255.25
WORK="$HOME/.xtmp/mafft-real-stage"
[ -d "$HOME/.xtmp" ] || WORK="$(mktemp -d)"
mkdir -p "$WORK"; trap 'rm -rf "$WORK"' EXIT
cd "$WORK"

md5of() { if command -v md5sum >/dev/null; then md5sum | cut -d' ' -f1; else md5 -q; fi; }
want=$(curl -sSf "$REL/md5_checksums" | awk '$2=="Pfam-A.seed.gz"{print $1}')
got=$(aws s3 cp --region "$REGION" --only-show-errors "$SEED" - | md5of)
[ "$got" = "$want" ] || { echo "FAIL: Pfam-A.seed.gz md5 $got, EBI says $want" >&2; exit 1; }
echo "Pfam-A.seed.gz md5 $got == EBI md5_checksums"

aws s3 cp --region "$REGION" --only-show-errors "$SEED" - | gzip -dc \
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
with open("ref.fa", "w") as ref, open("unaligned.fa", "w") as un, open("iq.fa", "w") as iq:
    for n in order:
        safe = re.sub(r"[^A-Za-z0-9_]", "_", n)       # the sanitising iqtree-real used
        assert safe not in seen, "name collision after sanitising: " + safe
        seen.add(safe)
        s = aln[n]
        assert re.fullmatch(r"[A-Za-z.\-]+", s), "unexpected character in " + n
        ref.write(">%s\n%s\n" % (safe, s))
        un.write(">%s\n%s\n" % (safe, re.sub(r"[.\-]", "", s).upper()))
        iq.write(">%s\n%s\n" % (safe, s.replace(".", "-").upper()))
print("%s: %d sequences x %d columns" % (fam, len(order), width))
PY

[ "$(grep -c '^>' unaligned.fa)" -eq 919 ] || { echo "FAIL: not 919 sequences (Pfam 38.2 num_seed)" >&2; exit 1; }
# the same family, the same bytes, iqtree-real aligned
aws s3 cp --region "$REGION" --only-show-errors "$BUCKET/inputs/iqtree-real/pf01255.25_seed.fa" - | cmp - iq.fa \
  || { echo "FAIL: reference differs from iqtree-real's pf01255.25_seed.fa" >&2; exit 1; }
echo "reference == iqtree-real/pf01255.25_seed.fa (uppercased, '.' -> '-')"
mv unaligned.fa pf01255.25_unaligned.fa
mv ref.fa pf01255.25_ref.fa
cp "$HERE/real.sh" .
shasum -a 256 pf01255.25_unaligned.fa pf01255.25_ref.fa real.sh
grep -E ' (pf01255\.25_unaligned\.fa|pf01255\.25_ref\.fa|real\.sh)$' "$HERE/pins.sha256" | shasum -a 256 -c
for f in pf01255.25_unaligned.fa pf01255.25_ref.fa real.sh; do
  aws s3 cp --region "$REGION" --only-show-errors "$f" "$PREFIX/$f"
done
aws s3 cp --region "$REGION" --only-show-errors "$HERE/pins.sha256" "$PREFIX/pins.sha256"
echo "staged to $PREFIX/"
