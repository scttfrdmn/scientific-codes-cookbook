#!/usr/bin/env bash
# Resolve a spec and launch it, waiting for completion.
#   - substitutes ${COOKBOOK_BUCKET} (spawn task run does NOT expand env vars)
#   - gives each launch a UNIQUE task_id (a fixed id collides on the shared
#     completion.json path, and --wait then reads a stale prior result)
#   COOKBOOK_BUCKET=... AWS_PROFILE=aws AWS_REGION=us-west-2 bash launch.sh <tool>
set -euo pipefail
T="${1:?usage: launch.sh <tool>}"
: "${COOKBOOK_BUCKET:?set COOKBOOK_BUCKET}"
DIR="$(cd "$(dirname "$0")" && pwd)"
OUT="/tmp/${T}.resolved.json"
NONCE="$(date +%Y%m%d%H%M%S)"
python3 - "$DIR/${T}.task.json" "$COOKBOOK_BUCKET" "$NONCE" > "$OUT" <<'PY'
import json, sys
spec = json.load(open(sys.argv[1])); bkt, nonce = sys.argv[2], sys.argv[3]
spec["task_id"] = spec["task_id"].replace("-r1", "") + f"-{nonce}"
for io in spec.get("inputs", []) + spec.get("outputs", []):
    io["source"] = io["source"].replace("${COOKBOOK_BUCKET}", bkt)
    io["destination"] = io["destination"].replace("${COOKBOOK_BUCKET}", bkt)
json.dump(spec, sys.stdout, indent=2)
PY
if grep -q '${COOKBOOK_BUCKET}' "$OUT"; then echo "bucket substitution failed" >&2; exit 1; fi
TID="$(python3 -c "import json;print(json.load(open('$OUT'))['task_id'])")"
echo "launching $TID against s3://$COOKBOOK_BUCKET (--wait)"
spawn task run --spec "$OUT" --wait
