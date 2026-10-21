#!/usr/bin/env bash
# Stage chip-r8g.task.json's task body. The BAM is bwa-samtools' own run output (runs*/bwa-samtools/r1/),
# not staged here; the body's sha256 is in the spec's command, so a stale copy fails rather than runs.
#   bash stage-inputs.sh <bucket>
set -euo pipefail
BUCKET="s3://${1:?pass your bucket}"
DIR="$(cd "$(dirname "$0")" && pwd)"
aws s3 cp --region "${AWS_REGION:-us-west-2}" --only-show-errors "$DIR/chip.sh" "$BUCKET/inputs/picard-real/chip.sh"
echo "  $BUCKET/inputs/picard-real/chip.sh  $(shasum -a 256 "$DIR/chip.sh" | cut -d' ' -f1)"
