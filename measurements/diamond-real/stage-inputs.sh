#!/usr/bin/env bash
# Stage measurements/diamond-real: UniProt release 2026_03's UniRef50 (8.78 GB), Swiss-Prot and
# human id mapping, fetched by a spawn task ON AWS (00-fetch: fetch.py checks UniProt's published
# size and md5 for each file) straight into the bucket -- nothing is downloaded to this machine.
# Every sha256 the task computes is then held to pins.sha256.
#
# Tier: a versioned release with a durable id (UniProt 2026_03). UniProt serves it from its
# current_release path only until the next release, so the pin is pins.sha256 and the bucket copy
# holds the bytes; fetch.py refuses to fetch once the release notes name another release.
#   stage-inputs.sh <bucket>
set -euo pipefail
B="${1:?pass your bucket}"
REGION="${AWS_REGION:-us-west-2}"
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
P="s3://$B/inputs/diamond-real"
FILES="uniref50.fasta.gz uniprot_sprot.fasta.gz HUMAN_9606_idmapping_selected.tab.gz"

for f in fetch.py search.sh compare.sh pins.sha256; do
  aws s3 cp --region "$REGION" --only-show-errors "$HERE/$f" "$P/$f"
done
check() {   # every staged file's task-computed sha256 must equal its pin
  for f in $FILES; do
    got=$(aws s3 cp --region "$REGION" --only-show-errors "$P/uniprot-2026_03/$f.sha256" - 2>/dev/null | cut -d' ' -f1)
    want=$(awk -v f="$f" '$2==f{print $1}' "$HERE/pins.sha256")
    [ -n "$got" ] && [ "$got" = "$want" ] || { echo "  $f: ${got:-absent}, pinned ${want:-nothing}"; return 1; }
    echo "  $f == pin"
  done
}
check || {
  spawn task run --wait --spec "$(python3 "$ROOT/scripts/spec_arch.py" diamond arm64 "$B" "$(date +%s)" \
    "$HERE/00-fetch.task.json")"
  check
}
echo "staged to $P/"
