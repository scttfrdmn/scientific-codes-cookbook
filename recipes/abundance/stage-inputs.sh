#!/usr/bin/env bash
# Stage the three extra viral genomes this recipe mixes. SARS-CoV-2 and the viral DB are
# NOT re-staged: they already live under inputs/kraken2/ and this recipe reads them there.
# A second copy of a pinned input is a second thing to keep true.
#
# FETCHED, not derived — NCBI efetch reproduces these bytes exactly, so the sha256 is the pin.
set -euo pipefail
BUCKET="${1:?pass your bucket -- make stage RECIPE=NAME does this}"
WORK="$(mktemp -d)"; trap 'rm -rf "$WORK"' EXIT; cd "$WORK"

declare -A WANT=(
  [NC_001802.1]=a5d0c305e567432806a5e68a99baf61cd67ab37a21414a3b310790fcb83e01a5   # HIV-1
  [NC_003977.2]=f67fb089e5d313a56c7d8ddc51e1fd7beecc5544d3e355d74c821cd4ef7f0321   # Hepatitis B
  [NC_001526.4]=ebf7834e615c60f819b2fed442ecf1c2fcf5eb52362edcba28f8f0730e407101   # HPV type 16
)
for acc in "${!WANT[@]}"; do
  echo "== fetch $acc (NCBI efetch) =="
  curl -fsSL "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi?db=nuccore&id=$acc&rettype=fasta&retmode=text" -o "$acc.fasta"
  printf '%s  %s\n' "${WANT[$acc]}" "$acc.fasta" | sha256sum -c -
  sleep 1   # NCBI rate-limits; a bare loop here returns HTTP 429
done
aws s3 cp . "s3://$BUCKET/inputs/abundance/" --recursive --exclude '*' --include '*.fasta'
echo "staged 3 genomes; the viral DB and NC_045512.2 are reused from inputs/kraken2/"
