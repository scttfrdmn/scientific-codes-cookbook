#!/usr/bin/env bash
# measurements/qc-aggregate-real, task 2: MultiQC over task 1's eleven FastQC reports, then
# check_multiqc.py (row count, lossless round trip, conservation, the silent missing sample).
# Reads task 1's output from /tmp/in; everything kept goes flat under /tmp/out.
set +e
set -uo pipefail
export PATH=/opt/conda/bin:/usr/local/bin:$PATH HOME=/tmp TMPDIR=/tmp
mkdir -p /tmp/out /tmp/work/zips /tmp/work/partial
cd /tmp/work
{
  echo "arch	$(uname -m)"
  echo "nproc	$(nproc)"
  lscpu 2>/dev/null | grep -E '^(Model name|Thread\(s\) per core)' || true
  echo "cpu0_thread_siblings	$(cat /sys/devices/system/cpu/cpu0/topology/thread_siblings_list 2>/dev/null)"
  echo "multiqc	$(multiqc --version 2>&1 | head -1)"
  echo "python	$(python3 --version 2>&1)"
} > /tmp/out/host.txt 2>&1
cat /tmp/out/host.txt
cp /tmp/in/*_fastqc.zip zips/
echo "zips staged: $(ls zips | grep -c '_fastqc.zip$')"

T0=$(date +%s)
multiqc -f -o mqc zips > /tmp/out/multiqc.log 2>&1; MRC=$?
echo "multiqc_rc	$MRC" | tee -a /tmp/out/host.txt
echo "multiqc_wall_s	$(( $(date +%s) - T0 ))" | tee -a /tmp/out/host.txt
[ "$MRC" -eq 0 ] || { tail -20 /tmp/out/multiqc.log; exit 1; }
cp mqc/multiqc_data/multiqc_fastqc.txt mqc/multiqc_data/multiqc_general_stats.txt /tmp/out/
cp mqc/multiqc_data/multiqc.log /tmp/out/multiqc_data.log 2>/dev/null

echo "== withhold the largest file's report: what does the aggregator say? =="
BIG=$(sort -t"$(printf '\t')" -k2,2n /tmp/in/fastqc_values.tsv | tail -1 | cut -f1)
for z in zips/*_fastqc.zip; do [ "$(basename "$z")" = "${BIG}_fastqc.zip" ] || cp "$z" partial/; done
multiqc -f -o mqcp partial > /tmp/out/multiqc-partial.log 2>&1; PRC=$?
cp mqcp/multiqc_data/multiqc_fastqc.txt /tmp/out/multiqc_fastqc.partial.txt
PWARN=$(grep -icE 'warn|error' mqcp/multiqc_data/multiqc.log)
PNAME=$(grep -c "$BIG" mqcp/multiqc_data/multiqc.log)
printf 'withheld\t%s\npartial_exit\t%s\npartial_warn_lines\t%s\npartial_lines_naming_withheld\t%s\n' \
  "$BIG" "$PRC" "$PWARN" "$PNAME" > /tmp/out/partial.txt
cat /tmp/out/partial.txt

python3 -u /tmp/check_multiqc.py /tmp/in /tmp/out > /tmp/out/smoke-check.txt 2>&1; CRC=$?
cat /tmp/out/smoke-check.txt
[ "$CRC" -eq 0 ] || { echo "MULTIQC CHECK FAILED"; exit 1; }
echo "SMOKE CHECK PASSED"
