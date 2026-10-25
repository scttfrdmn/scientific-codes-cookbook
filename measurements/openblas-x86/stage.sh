#!/usr/bin/env bash
# Stage the x86 probe env (x86.linux-64.lock: conda-forge linux-64, URL + sha256 per package) into the
# bucket, the same layout scripts/stage_env_x86.sh uses, so the x86 specs install it offline with
# scripts/xenv_install.sh. A download that does not match its sha256 fails here, before S3.
#   bash measurements/openblas-x86/stage.sh $(make -s print-bucket)
set -euo pipefail
BUCKET="s3://${1:?usage: stage.sh <bucket>}"
HERE="$(cd "$(dirname "$0")" && pwd)"; ROOT="$(cd "$HERE/../.." && pwd)"
DEST="$BUCKET/inputs/env-x86_64/openblas-x86-probe"
tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT
while IFS= read -r line; do
  case "$line" in https*) ;; *) continue ;; esac
  url="${line%%#*}"; sha="${line##*#}"; f="$tmp/$(basename "$url")"
  curl -fsSL --retry 5 -o "$f" "$url"
  [ "$(shasum -a 256 "$f" | cut -d' ' -f1)" = "$sha" ] || { echo "$(basename "$url"): sha256 mismatch" >&2; exit 1; }
done < "$HERE/x86.linux-64.lock"
aws s3 cp --only-show-errors --recursive "$tmp/" "$DEST/pkgs/"
aws s3 cp --only-show-errors "$HERE/x86.linux-64.lock" "$DEST/x86.linux-64.lock"
aws s3 cp --only-show-errors "$ROOT/scripts/xenv_install.sh" "$BUCKET/inputs/env-x86_64/xenv_install.sh"
echo "$(ls "$tmp" | wc -l | tr -d ' ') packages verified -> $DEST/"
