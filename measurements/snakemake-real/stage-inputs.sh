#!/usr/bin/env bash
# Stage measurements/snakemake-real. The eleven FASTQs are NOT fetched or copied: they are the
# cohort measurements/qc-aggregate-real assembled from the bwa, salmon, metaphlan and mageck real
# workloads, and the workflow reads each where its owner staged it (Snakefile's FILES).
#
# What this uploads is small: one truth file per FASTQ (its pinned sha256 and its published read
# and base counts, from qc-aggregate-real's cohort.tsv and inputs.sha256), the cohort's truth
# table, and the direct run's scripts. A truth file is uploaded only if absent, because its S3
# mtime is an input mtime to Snakemake -- re-uploading one is exactly the "touch" run.sh tests.
#
#   bash stage-inputs.sh <bucket>
set -euo pipefail
B="${1:?pass your bucket}"
HERE="$(cd "$(dirname "$0")" && pwd)"
Q="$HERE/../qc-aggregate-real"
P="s3://$B/inputs/snakemake-real"
T="$(mktemp -d "${TMPDIR:-/tmp}/snakemake-real.XXXXXX")"; trap 'rm -rf "$T"' EXIT

printf 'file\tsha256\treads\tbases\n' > "$T/cohort-truth.tsv"
awk -F'\t' 'NR>1{print $1"\t"$2}' "$Q/cohort.tsv" > "$T/keys.tsv"   # file -> bucket key, for direct.sh
while IFS=$'\t' read -r f key reads bases _; do
  [ "$f" = file ] && continue
  aws s3 ls "s3://$B/$key" > /dev/null || { echo "missing s3://$B/$key -- stage it with its owning measurement" >&2; exit 1; }
  sha=$(awk -v f="$f" '$2==f{print $1}' "$Q/inputs.sha256")
  [ -n "$sha" ] || { echo "no pinned sha256 for $f" >&2; exit 1; }
  printf 'file\tsha256\treads\tbases\n%s\t%s\t%s\t%s\n' "$f" "$sha" "$reads" "$bases" > "$T/$f.tsv"
  printf '%s\t%s\t%s\t%s\n' "$f" "$sha" "$reads" "$bases" >> "$T/cohort-truth.tsv"
  aws s3 ls "$P/truth/$f.tsv" > /dev/null 2>&1 || aws s3 cp --only-show-errors "$T/$f.tsv" "$P/truth/$f.tsv"
done < "$Q/cohort.tsv"
aws s3 ls "$P/cohort-truth.tsv" > /dev/null 2>&1 || aws s3 cp --only-show-errors "$T/cohort-truth.tsv" "$P/cohort-truth.tsv"
for f in direct.sh cohort.py; do aws s3 cp --only-show-errors "$HERE/$f" "$P/$f"; done
aws s3 cp --only-show-errors "$T/keys.tsv" "$P/keys.tsv"
aws s3 ls "$P/" --recursive
