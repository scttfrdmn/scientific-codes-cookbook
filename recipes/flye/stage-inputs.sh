#!/usr/bin/env bash
# Stage one real Oxford Nanopore run of Escherichia coli for de novo assembly.
#
# ERR10114907: 55,898 reads / 299,527,299 bases on a MinION. Against E. coli's ~4.6 Mb
# genome that is ~65x, which is the depth ONT assembly is actually run at -- high enough
# for a complete single-contig assembly, low enough that Flye finishes in minutes rather
# than hours.
#
# PATH PROVENANCE. The FASTQ path is NOT constructed here. ENA's portal API is asked for it,
# because the vol1/fastq/<prefix>/<subdir>/ layout is not something to guess: an earlier
# attempt at a different accession built the path by hand and 404'd. The run accession is the
# durable id; ENA resolves it to bytes.
#
# The downloaded file's read and base counts are checked against ENA's own reported values,
# which is an independent cross-check on the transfer -- a truncated download would still be
# a valid gzip and would still assemble, into a worse genome, silently.
set -euo pipefail

BUCKET="${1:?pass your bucket -- make stage RECIPE=NAME does this}"
ACC="ERR10114907"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
cd "$WORK"

META=$(curl -sSf "https://www.ebi.ac.uk/ena/portal/api/filereport?accession=${ACC}&result=read_run&fields=fastq_ftp,base_count,read_count&format=tsv" | awk 'NR==2')
FTP=$(printf '%s' "$META" | cut -f2)
WANT_BASES=$(printf '%s' "$META" | cut -f3)
WANT_READS=$(printf '%s' "$META" | cut -f4)
[ -n "$FTP" ] || { echo "ENA returned no fastq path for $ACC" >&2; exit 1; }
echo "ENA says: $WANT_READS reads, $WANT_BASES bases, at $FTP"

curl -sSf -o "$ACC.fastq.gz" "https://$FTP"

# One pass, reading to EOF: an `exit` after the count would SIGPIPE gzip, and under pipefail
# that is exit 141.
read -r GOT_READS GOT_BASES <<<"$(gzip -dc "$ACC.fastq.gz" \
  | awk 'NR%4==2 {r++; b+=length($0)} END{printf "%d %d", r, b}')"
echo "downloaded: $GOT_READS reads, $GOT_BASES bases"
[ "$GOT_READS" = "$WANT_READS" ] || { echo "read count $GOT_READS != ENA's $WANT_READS" >&2; exit 1; }
[ "$GOT_BASES" = "$WANT_BASES" ] || { echo "base count $GOT_BASES != ENA's $WANT_BASES" >&2; exit 1; }

aws s3 cp "$ACC.fastq.gz" "s3://$BUCKET/inputs/flye/$ACC.fastq.gz" --only-show-errors
echo "--- pin (record in README.md):"
sha256sum "$ACC.fastq.gz"
echo "--- $GOT_READS reads, $GOT_BASES bases (~$(( GOT_BASES / 4600000 ))x of a 4.6 Mb genome)"
