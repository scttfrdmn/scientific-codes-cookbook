#!/usr/bin/env bash
# Stage only the conversion script. The ALIGNMENT IS NOT COPIED -- this recipe reads the
# same object iqtree staged and raxml-ng already reuses, because a cross-code comparison
# only means anything on identical bytes and a second copy is a second thing to keep true.
set -euo pipefail

BUCKET="s3://${1:?pass your bucket -- make stage RECIPE=mrbayes does this}"
REGION="${AWS_REGION:-us-west-2}"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT
cd "$tmp"
cp "$SCRIPT_DIR/fasta2nexus.awk" "$SCRIPT_DIR/nexus2newick.awk" .

echo "== the shared alignment must already be staged by iqtree =="
if ! aws s3 ls --region "$REGION" "$BUCKET/inputs/iqtree/pfam38.2_seed_alignment.fa" >/dev/null 2>&1; then
  echo "  missing: run 'make stage RECIPE=iqtree' first -- this recipe deliberately does" >&2
  echo "  not stage its own copy of the alignment." >&2
  exit 1
fi
aws s3 cp --region "$REGION" "$BUCKET/inputs/iqtree/pfam38.2_seed_alignment.fa" aln.fa --quiet
printf '  %-28s %6s bytes (shared with iqtree + raxml-ng)\n' aln.fa "$(wc -c < aln.fa | tr -d '[:space:]')"

echo "== the headers must be NEXUS-safe, or the cross-check silently becomes label-matching =="
python3 - <<'PY'
import collections, sys
hdrs, seqs, cur = [], [], []
for line in open("aln.fa"):
    if line.startswith(">"):
        hdrs.append(line[1:].strip())
        if cur: seqs.append("".join(cur)); cur = []
    else:
        cur.append(line.strip())
if cur: seqs.append("".join(cur))
print("  taxa %d   sites %s" % (len(hdrs), sorted({len(s) for s in seqs})))
if len({len(s) for s in seqs}) != 1:
    sys.exit("  sequences are not all the same length -- not an alignment")
bad = [c for h in hdrs for c in h if not (c.isalnum() or c in "_.-")]
print("  header chars needing NEXUS quoting: %r" % sorted(set(bad)))
if bad:
    sys.exit("  headers would need quoting or renaming; the tree labels would then not"
             " match the ML recipes' trees and the comparison would be meaningless")
if len(hdrs) != len(set(hdrs)):
    sys.exit("  duplicate headers: %d" % (len(hdrs) - len(set(hdrs))))
longest = max(len(h) for h in hdrs)
print("  longest header %d chars, duplicates 0 -- safe to pass through unchanged" % longest)

# The sampling arithmetic the run asserts, derived here so it is not taken on MrBayes' word.
NGEN, SAMPLEFREQ = 100000, 100
print("  ngen %d / samplefreq %d -> %d samples per run (includes generation 0)"
      % (NGEN, SAMPLEFREQ, NGEN // SAMPLEFREQ + 1))
if NGEN % SAMPLEFREQ:
    sys.exit("  ngen is not a multiple of samplefreq, so the sample count would not be exact")
PY

echo "== the conversion must reproduce the alignment exactly =="
awk -f fasta2nexus.awk aln.fa > data.nex
python3 - <<'PY'
import sys
# Re-read both and compare label-for-label, residue-for-residue.
fa, cur, name = {}, [], None
for line in open("aln.fa"):
    if line.startswith(">"):
        if name: fa[name] = "".join(cur); cur = []
        name = line[1:].strip()
    else:
        cur.append(line.strip())
fa[name] = "".join(cur)

nx, inmatrix = {}, False
for line in open("data.nex"):
    s = line.strip()
    if s == "matrix": inmatrix = True; continue
    if s == ";": inmatrix = False; continue
    if inmatrix and s:
        k, v = s.split(None, 1)
        nx[k] = v
print("  fasta %d records, nexus %d rows" % (len(fa), len(nx)))
if fa != nx:
    missing = set(fa) ^ set(nx)
    diff = [k for k in fa if k in nx and fa[k] != nx[k]]
    sys.exit("  conversion is NOT lossless: %d label mismatches, %d sequence mismatches"
             % (len(missing), len(diff)))
print("  conversion is lossless: every label and every residue round-trips")
PY

sha256sum fasta2nexus.awk nexus2newick.awk > pins.sha256
aws s3 cp --region "$REGION" fasta2nexus.awk  "$BUCKET/inputs/mrbayes/fasta2nexus.awk"  >/dev/null
aws s3 cp --region "$REGION" nexus2newick.awk "$BUCKET/inputs/mrbayes/nexus2newick.awk" >/dev/null
aws s3 cp --region "$REGION" pins.sha256     "$BUCKET/inputs/mrbayes/pins.sha256"     >/dev/null
cat pins.sha256
# Task 2's script; 02-compare.task.json carries its sha256, so a stale copy fails, not runs.
aws s3 cp --region "$REGION" "$SCRIPT_DIR/compare.sh" "$BUCKET/inputs/mrbayes/compare.sh" >/dev/null
echo "$(shasum -a 256 "$SCRIPT_DIR/compare.sh" | cut -d' ' -f1)  compare.sh"
echo "done."
echo "  $BUCKET/inputs/mrbayes/  (the alignment is NOT re-staged -- shared with iqtree)"
