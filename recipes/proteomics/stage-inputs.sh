#!/usr/bin/env bash
# Stage three things, all version-matched to the OpenMS in the image (3.5.0):
#
#   1. SimpleSearchEngine's OWN committed test fixture -- input spectra, database, .ini and the
#      expected idXML. The conda package ships share/OpenMS with CHEMISTRY, CV and SCHEMAS but
#      no examples/ and no tests/, established by probing the image, so this comes from the
#      release tag. It turns "produce a number" into "reproduce the tool's committed number".
#   2. A real BSA run (BSA1.mzML), the 18-protein target-decoy database, and OpenMS's committed
#      OMSSA identification OF THAT FILE -- a second code's answer on identical spectra.
#   3. Percolator's committed PIN, for the rescoring leg.
#
# ABOUT THAT DATABASE NAME. 18Protein_SoCe_Tr_detergents_trace_target_decoy.fasta really is a
# target-decoy database: 9,439 targets and 9,439 decoys. The decoys are marked by a `_rev`
# SUFFIX on the accession, not by a prefix -- so a prefix test (DECOY_/rev_/XXX) finds none and
# reads as "the filename lies". It does not. Both the pairing and the convention are asserted
# below, because `-decoy_string` has to match the file's actual convention or every decoy is
# silently scored as a target and the FDR estimate is meaningless.
set -euo pipefail

BUCKET="s3://${1:?pass your bucket -- make stage RECIPE=proteomics does this}"
REGION="${AWS_REGION:-us-west-2}"
TAG=release/3.5.0
RAW="https://raw.githubusercontent.com/OpenMS/OpenMS/$TAG"
PERC_TAG=rel-3-08
PERC_RAW="https://raw.githubusercontent.com/percolator/percolator/$PERC_TAG"

# Resolve this script's own directory BEFORE cd'ing away, so identities.sh can be found later
# whether this was invoked by an absolute or a relative path.
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT
cd "$tmp"

# Guard on the LAST file staged, so a run interrupted part-way completes rather than
# short-circuiting on the first one.
if aws s3 ls "$BUCKET/inputs/proteomics/percolatorTab" --region "$REGION" >/dev/null 2>&1; then
  echo "proteomics inputs already staged"; exit 0
fi

echo "== 1. SimpleSearchEngine's own committed fixture, from OpenMS $TAG =="
for f in SimpleSearchEngine_1.fasta SimpleSearchEngine_1.mzML \
         SimpleSearchEngine_1.ini   SimpleSearchEngine_1_out.idXML; do
  curl -fsSL -o "$f" "$RAW/src/tests/topp/$f"
  printf '  %-32s %9s bytes\n' "$f" "$(wc -c < "$f")"
done

echo "== the expected output must be a real identification set =="
# Three hits is the whole committed answer, so if a future tag changes it this recipe must be
# re-derived rather than silently assert the wrong count.
NEXP=$(grep -c '<PeptideHit ' SimpleSearchEngine_1_out.idXML)
echo "  committed PeptideHits: $NEXP"
test "$NEXP" -eq 3 || { echo "  expected 3 committed hits, found $NEXP -- re-derive" >&2; exit 1; }
grep -q 'score="' SimpleSearchEngine_1_out.idXML || { echo "  no scores" >&2; exit 1; }

echo "== and the .ini must carry the configuration that produced it =="
# Fragment tolerance is the load-bearing one: SimpleSearchEngine ABORTS above 0.1 Da / 100 ppm
# (the deisotoper rejects it), so a recipe that guesses a low-res 0.5 Da never runs at all.
grep -q 'value="0.1"' SimpleSearchEngine_1.ini || { echo "  no 0.1 fragment tolerance" >&2; exit 1; }
python3 - <<'PY'
import xml.etree.ElementTree as ET, sys
r = ET.parse("SimpleSearchEngine_1.ini").getroot()
want = {"precursor/mass_tolerance": "5", "fragment/mass_tolerance": "0.1", "threads": "1"}
seen = {}
def walk(n, p):
    for c in n:
        nm = c.get("name", "")
        q = (p + "/" + nm).lstrip("/")
        if c.tag == "ITEM":
            for w in want:
                if q.endswith(w):
                    seen[w] = c.get("value")
        else:
            walk(c, q)
walk(r, "")
for k, v in want.items():
    print("  ini %-28s = %s" % (k, seen.get(k)))
    if seen.get(k) != v:
        sys.exit("  ini %s is %s, expected %s -- re-derive" % (k, seen.get(k), v))
print("  threads=1 in the committed config, so the reproduction is single-threaded")
PY

echo "== 2. the real BSA run, its database, and OpenMS's committed OMSSA result =="
curl -fsSL -o BSA1.mzML      "$RAW/share/OpenMS/examples/BSA/BSA1.mzML"
curl -fsSL -o BSA1_OMSSA.idXML "$RAW/share/OpenMS/examples/BSA/BSA1_OMSSA.idXML"
curl -fsSL -o db.fasta \
  "$RAW/share/OpenMS/examples/TOPPAS/data/BSA_Identification/18Protein_SoCe_Tr_detergents_trace_target_decoy.fasta"
for f in BSA1.mzML BSA1_OMSSA.idXML db.fasta; do
  printf '  %-32s %9s bytes\n' "$f" "$(wc -c < "$f")"
done

echo "== the mzML must be real spectra, not an empty shell =="
grep -q '<mzML' BSA1.mzML || { echo "  not mzML" >&2; exit 1; }
NSPEC=$(grep -o '<spectrum ' BSA1.mzML | wc -l | tr -d ' ')
NMS2=$(grep -o 'ms level" value="2"' BSA1.mzML | wc -l | tr -d ' ')
echo "  spectra: $NSPEC   MS2 spectra: $NMS2"
test "$NSPEC" -gt 500 || { echo "  expected >500 spectra" >&2; exit 1; }
test "$NMS2"  -gt 100 || { echo "  expected >100 MS2 spectra to search" >&2; exit 1; }

echo "== the database is target-decoy, paired 1:1 by a _rev SUFFIX =="
# This is the identity the FDR estimate rests on, and it is a property of the staged bytes, so
# it is asserted here rather than recomputed in the task.
python3 - <<'PY'
import sys
t, d = set(), set()
for line in open("db.fasta"):
    if line.startswith(">"):
        a = line[1:].split()[0]
        (d if a.endswith("_rev") else t).add(a[:-4] if a.endswith("_rev") else a)
print("  targets: %d   decoys: %d" % (len(t), len(d)))
if not t or not d:
    sys.exit("  database is not target-decoy")
miss, orph = t - d, d - t
print("  targets without a decoy twin: %d   decoys without a target: %d" % (len(miss), len(orph)))
if miss or orph:
    sys.exit("  the _rev pairing is not 1:1 -- FDR would be biased")
if "P02769|ALBU_BOVIN" not in t:
    sys.exit("  ALBU_BOVIN absent -- the right answer is not in the search space")
print("  ALBU_BOVIN present, so the correct answer for a BSA digest is reachable")
open("dbcounts.txt", "w").write("%d %d\n" % (len(t), len(d)))
PY
read -r NTGT NDEC < dbcounts.txt

echo "== the OMSSA reference must be a usable second opinion on THESE spectra =="
# Same spectra, same database -- that is what makes it comparable at all. Extracting its
# peptide set here means the task compares against a file, not against numbers typed into it.
python3 - <<'PY'
import xml.etree.ElementTree as ET, sys, re
from collections import Counter
r = ET.parse("BSA1_OMSSA.idXML").getroot()
sp = next(r.iter("SearchParameters"))
print("  reference db:        %s" % sp.get("db"))
print("  reference tolerances: precursor %s, fragment %s" %
      (sp.get("precursor_peak_tolerance"), sp.get("peak_mass_tolerance")))
if "18Protein" not in (sp.get("db") or ""):
    sys.exit("  the reference searched a different database -- not comparable")
pid = {p.get("id"): p.get("accession") for p in r.iter("ProteinHit")}
prot, seqs = Counter(), []
for pi in r.iter("PeptideIdentification"):
    for ph in pi.iter("PeptideHit"):
        seqs.append(ph.get("sequence"))
        for ref in (ph.get("protein_refs") or "").split():
            prot[pid.get(ref, ref)] += 1
# Compare on the BARE backbone: the reference scored Carbamidomethyl as VARIABLE while a
# normal search fixes it, so modification notation differs by configuration, not by result.
bare = sorted({re.sub(r"\([^)]*\)", "", s) for s in seqs})
top, n = prot.most_common(1)[0]
print("  reference PSMs: %d   unique peptides: %d   bare backbones: %d" %
      (len(seqs), len(set(seqs)), len(bare)))
print("  reference top protein: %s with %d of %d PSMs" % (top, n, len(seqs)))
if "ALBU_BOVIN" not in top:
    sys.exit("  the reference's top protein is not ALBU_BOVIN")
if len(bare) < 10:
    sys.exit("  too few reference peptides to compare against")
open("ref_peptides.txt", "w").write("\n".join(bare) + "\n")
PY
NREF=$(wc -l < ref_peptides.txt | tr -d ' ')
echo "  staged $NREF reference backbones for the cross-code comparison"

echo "== 3. percolator's committed PIN, from $PERC_TAG =="
curl -fsSL -o percolatorTab "$PERC_RAW/data/percolator/tab/percolatorTab"
printf '  %-32s %9s bytes\n' percolatorTab "$(wc -c < percolatorTab)"

echo "== the PIN must have the shape percolator documents =="
# Line 1 is the header; line 2 is the optional DefaultDirection feature-weight row; everything
# after is a PSM. Reading the structure rather than assuming it keeps the row-conservation
# identity in the task honest.
head -1 percolatorTab | grep -q $'^SpecId\tLabel\tScanNr' || {
  echo "  header is not SpecId/Label/ScanNr" >&2; exit 1; }
test "$(sed -n '2p' percolatorTab | cut -f1)" = "DefaultDirection" || {
  echo "  line 2 is not DefaultDirection" >&2; exit 1; }
PROW=$(awk 'NR>2' percolatorTab | wc -l | tr -d ' ')
PTGT=$(awk -F'\t' 'NR>2 && $2=="1"'  percolatorTab | wc -l | tr -d ' ')
PDEC=$(awk -F'\t' 'NR>2 && $2=="-1"' percolatorTab | wc -l | tr -d ' ')
echo "  PSM rows: $PROW   targets: $PTGT   decoys: $PDEC"
test "$PROW" -eq "$((PTGT + PDEC))" || { echo "  a row is neither target nor decoy" >&2; exit 1; }
test "$PDEC" -gt 1000 || { echo "  too few decoys for an FDR estimate" >&2; exit 1; }

echo "== Label and the decoy_ protein prefix must agree exactly =="
# Two INDEPENDENT encodings of target/decoy status in one file: the Label column and the
# protein-name prefix. They are written separately, so disagreement means the fixture is
# malformed -- and percolator is told to use the prefix (-P decoy_) while the identities in the
# task count by Label, so the two had better mean the same thing.
MISMATCH=$(awk -F'\t' 'NR>2 { isdec = ($NF ~ /^decoy_/); lab = ($2 == "-1")
                              if (isdec != lab) n++ } END { print n+0 }' percolatorTab)
echo "  rows where Label and prefix disagree: $MISMATCH"
test "$MISMATCH" -eq 0 || { echo "  Label and decoy_ prefix disagree" >&2; exit 1; }

# The leg A/B analysis travels as a staged input, not inline in the TaskSpec: a spawn task
# command rides in EC2 user data, which is capped at 16,384 bytes, and the inlined version
# pushed task 1 past it -- RunInstances refused to launch at all. Staging is strictly better
# anyway, since the script then sits under pins.sha256 like every other input.
cp "$SCRIPT_DIR/identities.sh" .

FILES="SimpleSearchEngine_1.fasta SimpleSearchEngine_1.mzML SimpleSearchEngine_1.ini
       SimpleSearchEngine_1_out.idXML BSA1.mzML BSA1_OMSSA.idXML db.fasta
       ref_peptides.txt percolatorTab identities.sh"
# shellcheck disable=SC2086
shasum -a 256 $FILES > pins.sha256
printf 'db_targets\t%s\ndb_decoys\t%s\nms2_spectra\t%s\nref_peptides\t%s\n' \
  "$NTGT" "$NDEC" "$NMS2" "$NREF" > expected.tsv
printf 'pin_psm_rows\t%s\npin_targets\t%s\npin_decoys\t%s\ncommitted_hits\t%s\n' \
  "$PROW" "$PTGT" "$PDEC" "$NEXP" >> expected.tsv
sed 's/^/  /' expected.tsv
sed 's/^/  /' pins.sha256

# shellcheck disable=SC2086
for f in $FILES pins.sha256 expected.tsv; do
  aws s3 cp "$f" "$BUCKET/inputs/proteomics/$f" --region "$REGION" --only-show-errors
done
echo "done."
echo "  $BUCKET/inputs/proteomics/"
echo "  committed fixture: $NEXP expected hits | real run: $NMS2 MS2 vs $NTGT+$NDEC target/decoy"
echo "  reference: $NREF OMSSA backbones | percolator PIN: $PROW PSMs ($PTGT/$PDEC)"
