#!/usr/bin/env bash
# Stage measurements/mageck-real: the Brunello A375 dropout screen (Sanson et al. 2018,
# PRJNA508200: plasmid SRR8297998, replicates SRR8279891 / SRR8281054; 7.7 GB of FASTQ), the
# Brunello library (Zenodo 10.5281/zenodo.5750854) and Hart's CEGv2 / NEGv1 gene sets.
#
# The FASTQs are fetched from ENA on AWS by fetch.task.json (fetch.sh), never here: this script
# uploads the scripts and pins, then launches that task. fetch.sh checks each FASTQ against ENA's
# md5; the run checks every staged byte against inputs.sha256.
#
# Usage: stage-inputs.sh <bucket>
set -euo pipefail
B="${1:?pass your bucket}"
D="$(cd "$(dirname "$0")" && pwd)"
for f in fetch.sh run.sh recount.py check.py interp.py inputs.sha256; do
  [ -f "$D/$f" ] && aws s3 cp "$D/$f" "s3://$B/inputs/mageck-real/$f" --only-show-errors
done
if ! aws s3 ls "s3://$B/inputs/mageck-real/data/inputs.sha256" > /dev/null 2>&1; then
  sed "s|\${COOKBOOK_BUCKET}|$B|g" "$D/fetch.task.json" > "${TMPDIR:-/tmp}/mageck-real-fetch.json"
  spawn task run --spec "${TMPDIR:-/tmp}/mageck-real-fetch.json" --wait
fi
aws s3 ls "s3://$B/inputs/mageck-real/data/"
