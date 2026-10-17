#!/usr/bin/env bash
# Stage the packages of an x86_64 env twin (envs-x86_64/<name>.linux-64.lock) into the bucket,
# once per env, for every recipe whose frontmatter says `env_x86_64: <name>`.
#
# The lock pins every package by URL and sha256 (scripts/lock_env_x86.py writes it); a download
# that does not match fails here, before anything reaches S3. The task installs them OFFLINE
# (recipes/fsl's model), so the x86 run never resolves or fetches anything at run time.
set -euo pipefail

NAME="${1:?usage: stage_env_x86.sh <env-name> <bucket>}"
BUCKET="s3://${2:?usage: stage_env_x86.sh <env-name> <bucket>}"
REGION="${AWS_REGION:-us-west-2}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
LOCK="$ROOT/envs-x86_64/$NAME.linux-64.lock"
DEST="$BUCKET/inputs/env-x86_64/$NAME"
[ -s "$LOCK" ] || { echo "  no $LOCK -- run scripts/lock_env_x86.py <recipe> first" >&2; exit 1; }

# the shared installer every env-twin task sources (spec_arch.py pins its sha256 into the spec)
aws s3 cp --region "$REGION" --only-show-errors "$ROOT/scripts/xenv_install.sh" "$BUCKET/inputs/env-x86_64/xenv_install.sh"

want=$(grep -c '^https' "$LOCK")
have=$({ aws s3 ls --region "$REGION" "$DEST/pkgs/" 2>/dev/null || true; } | wc -l | tr -d ' ')   # ls exits 1 on a new prefix
if [ "$have" = "$want" ] && aws s3 cp --region "$REGION" --only-show-errors "$DEST/$NAME.linux-64.lock" - 2>/dev/null | cmp -s - "$LOCK"; then
  echo "  $NAME: already staged ($want packages, lock unchanged)"
  exit 0
fi

tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT
n=0; bytes=0
while IFS= read -r line; do
  case "$line" in ''|'#'*|'@EXPLICIT') continue ;; esac
  url="${line%%#*}"; sha="${line##*#}"; f="$tmp/$(basename "$url")"
  curl -fsSL --retry 5 -o "$f" "$url"
  got=$(shasum -a 256 "$f" | cut -d' ' -f1)
  [ "$got" = "$sha" ] || { echo "  $(basename "$url"): sha256 $got, lock says $sha" >&2; exit 1; }
  n=$((n + 1)); bytes=$((bytes + $(wc -c < "$f")))
done < "$LOCK"
echo "  $NAME: $n packages verified against its lock ($((bytes / 1000000)) MB)"
aws s3 rm --region "$REGION" --only-show-errors --recursive "$DEST/pkgs/" 2>/dev/null || true
aws s3 cp --region "$REGION" --only-show-errors --recursive "$tmp/" "$DEST/pkgs/"
aws s3 cp --region "$REGION" --only-show-errors "$LOCK" "$DEST/$NAME.linux-64.lock"
echo "  $DEST/"
