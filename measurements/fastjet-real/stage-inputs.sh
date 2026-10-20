#!/usr/bin/env bash
# Stage the fastjet-real input and scripts.
#
#   bash measurements/fastjet-real/stage-inputs.sh <bucket>
#
# Data: the LHC Olympics 2020 R&D dataset, Zenodo record 6466204, v5 (DOI 10.5281/zenodo.6466204):
# 1.1 M Pythia 8 + Delphes events, every reconstructed hadron as (pt, eta, phi), and the organisers'
# own anti-k_t R = 1.0 jet features. Tier: a DOI-identified public record. Both steps run as spawn
# tasks ON AWS, never on this machine: 00-fetch (fetch.py checks Zenodo's size and md5) writes the
# record's files to the bucket; 01-convert (convert.py, in the md env, which has pytables) writes flat
# arrays the hep env can read and proves the conversion lossless. Every sha256 they compute is held
# to the committed pins here.
set -euo pipefail

BUCKET="${1:?pass your bucket}"
REGION="${AWS_REGION:-us-west-2}"
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOTDIR="$(cd "$HERE/../.." && pwd)"
P="s3://$BUCKET/inputs/fastjet-real"

cd "$HERE"
for f in fetch.py convert.py real.sh cluster.py brute.py; do
  grep -E " $f\$" pins.sha256 | shasum -a 256 -c
  aws s3 cp --region "$REGION" --only-show-errors "$f" "$P/$f"
done
aws s3 cp --region "$REGION" --only-show-errors pins.sha256 "$P/pins.sha256"

# check(<s3 sha256 file>): every line in it must equal the committed pin for that name
check() {
  aws s3 cp --region "$REGION" --only-show-errors "$P/$1" - 2>/dev/null | while read -r h n; do
    want=$(awk -v f="$n" '$2==f{print $1}' pins.sha256)
    [ -n "$want" ] && [ "$h" = "$want" ] || { echo "  $n: $h, pinned ${want:-nothing}"; exit 1; }
    echo "  $n == pin"
  done
}
run() {
  spawn task run --spec "$(python3 "$ROOTDIR/scripts/spec_arch.py" fastjet arm64 "$BUCKET" "$(date +%s)" "$HERE/$1")" --wait
}

for f in events_anomalydetection_v2.h5 events_anomalydetection_v2.features.h5; do
  check "zenodo/$f.sha256" || { run 00-fetch.task.json; check "zenodo/$f.sha256"; }
done
check data/converted.sha256 || { run 01-convert.task.json; check data/converted.sha256; }
echo "staged to $P/"
