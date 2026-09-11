#!/usr/bin/env bash
# Stage the shared MSA input for mafft / muscle / nf-spawn: a 114-protein Pfam family,
# UNALIGNED (gaps stripped). Derived — reproducible from an immutable source.
#
# It is the same family iqtree/raxml-ng use (recipes/iqtree derives the *aligned* FASTA
# from Pfam 38.2's seed), here with gaps removed so an aligner has something to align.
# Same deterministic selection rule (first Pfam-A.seed family with 40-150 sequences), so
# re-running picks the same family and reproduces the same bytes. Lives under the shared
# inputs/mafft-muscle/ prefix because muscle and nf-spawn read the identical file.
#
# Derivation command (the record that was missing for the 30x reads): see the python below.
set -euo pipefail

BUCKET="${1:?pass your bucket -- make stage RECIPE=NAME does this}"
SEED="https://ftp.ebi.ac.uk/pub/databases/Pfam/releases/Pfam38.2/Pfam-A.seed.gz"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
cd "$WORK"

# Same 4 MiB slice + latin-1 decode as recipes/iqtree — holds many complete alignments.
curl -sSf -r 0-4194303 -o seed_part.gz "$SEED"
gzip -dc seed_part.gz 2>/dev/null > seed_part.sto || true

python3 - <<'PY'
import re, sys
MIN_TAX, MAX_TAX = 40, 150
text = open("seed_part.sto", encoding="latin-1", errors="replace").read()

def alignments(text):
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
                cur[name] = ""; order.append(name)
            cur[name] += seq

pick = next(((a, i, o, al) for a, i, o, al in alignments(text) if MIN_TAX <= len(o) <= MAX_TAX), None)
if pick is None:
    sys.exit(f"no alignment with {MIN_TAX}-{MAX_TAX} sequences in the fetched slice")
acc, ident, order, aln = pick

# UNALIGNED output: strip both Stockholm gap chars ('.', '-'); sanitise names as iqtree does.
seen = set()
with open("pfam_unaligned.fa", "w") as out:
    for n in order:
        safe = re.sub(r"[^A-Za-z0-9_]", "_", n)
        if safe in seen:
            sys.exit(f"name collision after sanitising: {safe}")
        seen.add(safe)
        seq = aln[n].replace(".", "").replace("-", "").upper()
        if not seq:
            sys.exit(f"empty sequence after ungapping: {n}")
        out.write(f">{safe}\n{seq}\n")
print(f"selected {acc} ({ident}): {len(order)} sequences, ungapped")
PY

# Independent re-check: one header + one sequence line per record, all non-empty.
NSEQ=$(grep -c '^>' pfam_unaligned.fa)
[ "$NSEQ" -ge 40 ] && [ "$NSEQ" -le 150 ] || { echo "nseq $NSEQ out of range" >&2; exit 1; }
[ "$(grep -vc '^>' pfam_unaligned.fa)" -eq "$NSEQ" ] || { echo "not one line per sequence" >&2; exit 1; }

aws s3 cp pfam_unaligned.fa "s3://$BUCKET/inputs/mafft-muscle/pfam_unaligned.fa" --only-show-errors
echo "--- staged inputs/mafft-muscle/pfam_unaligned.fa ; pin (record in the READMEs):"
shasum -a 256 pfam_unaligned.fa 2>/dev/null || sha256sum pfam_unaligned.fa
