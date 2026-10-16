#!/usr/bin/env bash
# Stage FSL from FSL's OWN conda channel into YOUR bucket, verified against the committed locks.
#
# Why staging, not an image: FSL's licence lets you copy it "without financial return" only if
# "all original and amended source code is included in any transmitted product", so a public
# binary image of FSL is not permitted -- that is why aarch.* cannot carry it. Here YOU download
# FSL from its publisher, under its licence, into your own account; nothing is redistributed.
# FSL is free for non-commercial use only: https://fsl.fmrib.ox.ac.uk/fsl/docs/license.html
#
# fsl-linux-64.lock and fsl-linux-aarch64.lock pin every package (URL + sha256); a download that
# does not match fails here. Both architectures are staged, so one recipe runs on Graviton and x86.
set -euo pipefail

BUCKET="s3://${1:?pass your bucket -- make stage RECIPE=fsl does this}"
REGION="${AWS_REGION:-us-west-2}"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT
for sub in linux-64 linux-aarch64; do
  lock="$SCRIPT_DIR/fsl-$sub.lock"
  mkdir -p "$tmp/$sub"
  n=0; bytes=0
  while IFS= read -r line; do
    case "$line" in ''|'#'*|'@EXPLICIT') continue ;; esac
    url="${line%%#*}"; want="${line##*#}"; f="$tmp/$sub/$(basename "$url")"
    curl -fsSL --retry 5 -o "$f" "$url"
    got=$(shasum -a 256 "$f" | cut -d' ' -f1)
    [ "$got" = "$want" ] || { echo "  $(basename "$url"): sha256 $got, lock says $want" >&2; exit 1; }
    n=$((n + 1)); bytes=$((bytes + $(wc -c < "$f")))
  done < "$lock"
  echo "  $sub: $n packages verified against fsl-$sub.lock ($((bytes / 1000000)) MB)"
  # the task installs offline from these files; the lock is rewritten to point at them there
  aws s3 cp --region "$REGION" --only-show-errors --recursive "$tmp/$sub/" "$BUCKET/inputs/fsl/pkgs/$sub/"
  aws s3 cp --region "$REGION" --only-show-errors "$lock" "$BUCKET/inputs/fsl/fsl-$sub.lock"
  rm -rf "$tmp/$sub"
done
echo "done."
echo "  $BUCKET/inputs/fsl/  (FSL packages for both architectures, from FSL's channel; non-commercial use only)"
