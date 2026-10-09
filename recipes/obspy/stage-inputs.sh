#!/usr/bin/env bash
# Stage the Java TauP reference tables and the checks.
#
# WHY THESE FILES. obspy's taup is a Python reimplementation of the original Java TauP
# (Crotwell et al.), and obspy commits the JAVA TOOL'S OWN OUTPUT as test data. Reproducing it
# is a published-reference check against an unrelated codebase in another language, which is
# stronger than any identity obspy could satisfy on its own. The filenames encode the Java
# command line verbatim -- `taup_time_-h_10_-ph_ttall_-deg_35` is `taup_time -h 10 -ph ttall
# -deg 35` -- and the directory also ships the gendata.sh that produced them.
#
# THE VERSION MATCH IS LOAD-BEARING: the tag below must be the obspy in the image (1.5.1), or
# the reference belongs to a different implementation than the one being checked.
#
# No WAVEFORM data is staged. obspy bundles its own example stream (obspy.read() returns three
# real 100 Hz traces from BW.RJOB, 2009-08-24), so the I/O and signal identities need nothing.
set -euo pipefail

BUCKET="s3://${1:?pass your bucket -- make stage RECIPE=obspy does this}"
REGION="${AWS_REGION:-us-west-2}"
TAG=1.5.1
RAW="https://raw.githubusercontent.com/obspy/obspy/$TAG/obspy/taup/tests/data/TauP_test_data"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT
cd "$tmp"

if aws s3 ls "$BUCKET/inputs/obspy/identities.py" --region "$REGION" >/dev/null 2>&1; then
  echo "obspy inputs already staged"; exit 0
fi

echo "== fetch the Java TauP reference tables from obspy $TAG =="
# The filenames contain characters that must be percent-encoded for a raw fetch.
for f in "taup_time_-h_10_-ph_ttall_-deg_35" \
         "taup_time_-h_10_-ph_ttall_-deg_35_-mod_ak135"; do
  enc="$(python3 -c "import urllib.parse,sys;print(urllib.parse.quote(sys.argv[1]))" "$f")"
  curl -fsSL -o "$f" "$RAW/$enc"
  printf '  %-44s %6s bytes\n' "$f" "$(wc -c < "$f")"
done

echo "== each table must be a real TauP table with enough arrivals to mean something =="
python3 - <<'PY'
import sys
for fname, model in (("taup_time_-h_10_-ph_ttall_-deg_35", "iasp91"),
                     ("taup_time_-h_10_-ph_ttall_-deg_35_-mod_ak135", "ak135")):
    text = open(fname).read()
    if "Model: %s" % model not in text:
        sys.exit("  %s does not declare 'Model: %s'" % (fname, model))
    rows, phases = 0, set()
    for line in text.splitlines():
        f = line.split()
        if len(f) < 8:
            continue
        try:
            float(f[0]); float(f[1]); float(f[3])
        except ValueError:
            continue
        rows += 1
        phases.add(f[2])
    print("  %-44s %2d arrivals, %2d distinct phases" % (fname, rows, len(phases)))
    if rows < 20:
        sys.exit("  only %d arrivals parsed from %s -- the format changed" % (rows, fname))
    if "P" not in phases or "S" not in phases:
        sys.exit("  %s lacks a direct P or S arrival" % fname)
PY

cp "$SCRIPT_DIR/identities.py" .
python3 -c "import ast,io; ast.parse(io.open('identities.py',encoding='utf-8').read())" \
  || { echo "  identities.py does not parse" >&2; exit 1; }
printf '  %-44s %6s bytes\n' identities.py "$(wc -c < identities.py)"

FILES=("taup_time_-h_10_-ph_ttall_-deg_35" "taup_time_-h_10_-ph_ttall_-deg_35_-mod_ak135" identities.py)
shasum -a 256 "${FILES[@]}" > pins.sha256
sed 's/^/  /' pins.sha256

for f in "${FILES[@]}" pins.sha256; do
  aws s3 cp "$f" "$BUCKET/inputs/obspy/$f" --region "$REGION" --only-show-errors
done
echo "done."
echo "  $BUCKET/inputs/obspy/  (Java TauP references for iasp91 and ak135; no waveform staged)"
