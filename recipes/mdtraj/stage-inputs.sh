#!/usr/bin/env bash
# Stage the task script. No data is staged: the system is solvated in-task from the
# spc216 template the gromacs package ships. trajectory.sh is staged, and pinned by sha256 in
# the spec, because inline the command exceeds EC2's 16 KiB user-data limit.
set -euo pipefail

BUCKET="s3://${1:?pass your bucket -- make stage RECIPE=mdtraj does this}"
REGION="${AWS_REGION:-us-west-2}"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

bash -n "$SCRIPT_DIR/trajectory.sh" || { echo "  trajectory.sh does not parse" >&2; exit 1; }
aws s3 cp --region "$REGION" --only-show-errors "$SCRIPT_DIR/trajectory.sh" "$BUCKET/inputs/mdtraj/trajectory.sh"
(cd "$SCRIPT_DIR" && { sha256sum trajectory.sh 2>/dev/null || shasum -a 256 trajectory.sh; })
echo "done."
echo "  $BUCKET/inputs/mdtraj/trajectory.sh"
