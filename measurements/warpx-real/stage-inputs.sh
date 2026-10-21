#!/usr/bin/env bash
# Stage the Turner et al. (2013) capacitive-discharge benchmark, case 1, as WarpX ships it:
#   - upstream's PICMI deck and analysis script at tag 26.10 (the packaged warpx version),
#   - the He cross sections the deck reads, from BLAST-WarpX/warpx-data at the commit that added
#     them (b59a35f8, unchanged since 2021),
#   - this directory's run.sh / check.py.
# Every file is streamed GitHub -> S3 (nothing lands on the local disk) and pinned by sha256 in
# inputs.sha256, which the task re-checks before anything runs.
#   measurements/warpx-real/stage-inputs.sh <bucket>
set -euo pipefail
BUCKET="s3://${1:?bucket}"
REGION="${AWS_REGION:-us-west-2}"
D="$(cd "$(dirname "$0")" && pwd)"
DST="$BUCKET/inputs/warpx-real"
TAG=26.10
DATA=b59a35f8d2d10592b2ea0ea18608dc2a12bc1380
WX="https://raw.githubusercontent.com/ECP-WarpX/WarpX/$TAG/Examples/Physics_applications/capacitive_discharge"
XS="https://raw.githubusercontent.com/BLAST-WarpX/warpx-data/$DATA/MCC_cross_sections/He"

put() {  # url name: stream to S3, then verify the S3 copy against the pin
  local want got
  want="$(awk -v f="$2" '$2==f{print $1}' "$D/inputs.sha256")"
  [ -n "$want" ] || { echo "no pin for $2" >&2; exit 1; }
  curl -fsSL "$1" | aws s3 cp --region "$REGION" - "$DST/$2" >/dev/null
  got="$(aws s3 cp --region "$REGION" "$DST/$2" - | shasum -a 256 | cut -d' ' -f1)"
  [ "$got" = "$want" ] || { echo "FAIL: $2 is $got, pinned $want" >&2; exit 1; }
  echo "  ok  $2"
}

put "$WX/inputs_base_1d_picmi.py" inputs_base_1d_picmi.py
put "$WX/analysis_1d.py" analysis_1d.py
for f in electron_scattering excitation_1 excitation_2 ionization ion_scattering ion_back_scatter; do
  put "$XS/$f.dat" "$f.dat"
done

# The asserted tolerance must be upstream's, read from the pinned script, not typed here.
aws s3 cp --region "$REGION" "$DST/analysis_1d.py" - | grep -q '^tolerance = 0.065$' \
  || { echo "FAIL: upstream's analysis_1d.py no longer asserts tolerance = 0.065" >&2; exit 1; }
echo "  upstream tolerance: RMS relative error < 0.065"

for f in run.sh check.py inputs.sha256; do
  aws s3 cp --region "$REGION" "$D/$f" "$DST/$f" >/dev/null
  echo "  ok  $f"
done
echo "done: $DST/"
