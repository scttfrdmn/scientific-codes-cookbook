#!/usr/bin/env bash
# Stage the task script. No data is staged: tleap builds the whole system in-task from the
# force fields the ambertools package ships. md.sh is staged, and pinned by sha256 in the spec,
# because inline the command exceeds EC2's 16 KiB user-data limit.
set -euo pipefail

BUCKET="s3://${1:?pass your bucket -- make stage RECIPE=ambertools does this}"
REGION="${AWS_REGION:-us-west-2}"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

bash -n "$SCRIPT_DIR/md.sh" || { echo "  md.sh does not parse" >&2; exit 1; }
aws s3 cp --region "$REGION" --only-show-errors "$SCRIPT_DIR/md.sh" "$BUCKET/inputs/ambertools/md.sh"
(cd "$SCRIPT_DIR" && { sha256sum md.sh 2>/dev/null || shasum -a 256 md.sh; })
echo "done."
echo "  $BUCKET/inputs/ambertools/md.sh"
