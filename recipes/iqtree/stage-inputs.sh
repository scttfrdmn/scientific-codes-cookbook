#!/usr/bin/env bash
# Stage this recipe's one input: a curated protein alignment from Pfam 38.2's seed
# alignments, converted from Stockholm to aligned FASTA.
#
# Why a Pfam seed rather than a bundled example: iqtree's conda package ships only
# a 20 KB cmaple test file, and depending on a package's internal test directory is
# a fragile input. Pfam seeds are hand-curated alignments in an immutable versioned
# release, which is a real input with a durable id — and it is the same pinned
# release the hmmer recipe uses.
#
# The selection rule is deterministic: the FIRST alignment in Pfam-A.seed with
# between 40 and 150 sequences. That is a property of Pfam 38.2, so re-running this
# picks the same family every time.
set -euo pipefail

BUCKET="${1:-scicookbook-942542972736-us-east-1}"
SEED="https://ftp.ebi.ac.uk/pub/databases/Pfam/releases/Pfam38.2/Pfam-A.seed.gz"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
cd "$WORK"

# 4 MiB of the gzip stream holds many complete alignments. Pfam-A.seed is
# latin-1, not utf-8, so decode explicitly rather than letting python guess.
curl -sSf -r 0-4194303 -o seed_part.gz "$SEED"
gzip -dc seed_part.gz 2>/dev/null > seed_part.sto || true

python3 - <<'PY'
import re, sys

MIN_TAX, MAX_TAX = 40, 150
text = open("seed_part.sto", encoding="latin-1", errors="replace").read()

def alignments(text):
    """Yield (accession, id, {name: seq}) for each complete Stockholm record."""
    cur, order, acc, ident = {}, [], None, None
    for line in text.splitlines():
        if line.startswith("# STOCKHOLM"):
            cur, order, acc, ident = {}, [], None, None
        elif line.startswith("#=GF AC"):
            acc = line.split(None, 2)[2].strip()
        elif line.startswith("#=GF ID"):
            ident = line.split(None, 2)[2].strip()
        elif line.startswith("//"):
            if cur:
                yield acc, ident, order, cur
            cur, order, acc, ident = {}, [], None, None
        elif line.startswith("#") or not line.strip():
            continue
        else:
            parts = line.split(None, 1)
            if len(parts) != 2:
                continue
            name, seq = parts[0], parts[1].strip()
            if name not in cur:
                cur[name] = ""
                order.append(name)
            cur[name] += seq

pick = None
for acc, ident, order, aln in alignments(text):
    if MIN_TAX <= len(order) <= MAX_TAX:
        pick = (acc, ident, order, aln)
        break
if pick is None:
    sys.exit(f"no alignment with {MIN_TAX}-{MAX_TAX} sequences in the fetched slice")

acc, ident, order, aln = pick
width = len(aln[order[0]])
for n in order:
    if len(aln[n]) != width:
        sys.exit(f"ragged alignment: {n} is {len(aln[n])}, expected {width}")

# Stockholm uses '.' and '-' for gaps; IQ-TREE wants one gap character. Sanitise
# names too: '/' and '.' are legal in Pfam names but awkward in Newick output.
seen = set()
with open("alignment.fa", "w") as out:
    for n in order:
        safe = re.sub(r"[^A-Za-z0-9_]", "_", n)
        if safe in seen:
            sys.exit(f"name collision after sanitising: {safe}")
        seen.add(safe)
        out.write(f">{safe}\n{aln[n].replace('.', '-').upper()}\n")

open("selection.txt", "w").write(
    f"pfam_accession\t{acc}\npfam_id\t{ident}\nsequences\t{len(order)}\ncolumns\t{width}\n")
print(f"selected {acc} ({ident}): {len(order)} sequences x {width} columns")
PY

# Independent re-check of what python wrote, so a bug there cannot pass silently.
NTAX=$(grep -c '^>' alignment.fa)
[ "$NTAX" -ge 40 ] && [ "$NTAX" -le 150 ] || { echo "ntax $NTAX out of range" >&2; exit 1; }
[ "$(grep -vc '^>' alignment.fa)" -eq "$NTAX" ] || { echo "not one line per sequence" >&2; exit 1; }
[ "$(grep -v '^>' alignment.fa | awk '{print length}' | sort -u | wc -l)" -eq 1 ] \
  || { echo "sequence lines differ in length" >&2; exit 1; }

aws s3 cp alignment.fa "s3://$BUCKET/inputs/iqtree/pfam38.2_seed_alignment.fa" --only-show-errors
echo "--- pins (record these in README.md):"
sha256sum alignment.fa
cat selection.txt
