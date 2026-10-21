#!/usr/bin/env bash
# rule stats (Snakefile): one whole FASTQ on its own spawn instance. Runs the recipe's pinned
# seqkit by digest, relabels the row with the file name, and checks the file against the
# published values in truth/<file>.tsv -- sha256 of the staged bytes, read count, base count.
# Any mismatch exits 1, which fails the job and so the workflow.
#
# Everything the measurement needs about the job goes to the rule's log as `key<TAB>value`:
# the instance, boot and phase timestamps, seqkit's wall time and peak RSS, and disk use.
set +e
FQ=${snakemake_input[fq]}; TRUTH=${snakemake_input[truth]}; OUT=${snakemake_output[0]}
LOG=${snakemake_log[0]}; F=${snakemake_wildcards[f]}; T=${snakemake[threads]}
mkdir -p "$(dirname "$OUT")" "$(dirname "$LOG")"
: > "$LOG"
rec() { printf '%s\t%s\n' "$1" "$2" >> "$LOG"; }
now() { date +%s.%N; }
FAIL=0
fail() { rec FAIL "$1"; FAIL=1; }

rec file "$F"
rec script_start "$(now)"
TOK=$(curl -s -X PUT http://169.254.169.254/latest/api/token -H 'X-aws-ec2-metadata-token-ttl-seconds: 60')
md() { curl -s -H "X-aws-ec2-metadata-token: $TOK" "http://169.254.169.254/latest/meta-data/$1"; }
rec instance_id "$(md instance-id)"
rec instance_type "$(md instance-type)"
rec host_arch "$(uname -m)"
rec vcpus "$(getconf _NPROCESSORS_ONLN)"
rec threads_per_core "$(lscpu | awk -F: '/Thread\(s\) per core/{gsub(/ /,"",$2); print $2}')"
rec cpu_model "$(lscpu | awk -F: '/Model name/{sub(/^ +/,"",$2); print $2; exit}')"
rec mem_total_kib "$(awk '/MemTotal/{print $2}' /proc/meminfo)"
rec boot_epoch "$(awk '/^btime/{print $2}' /proc/stat)"
# The executor's install preamble builds this venv before snakemake runs, so its birth time
# and the staged input's ctime split "boot to this script" into install and stage-in.
rec venv_birth_epoch "$(stat -c %W /opt/snakemake-spawn-venv 2>/dev/null)"
rec input_ctime_epoch "$(stat -L -c %Z "$FQ")"
rec input_bytes "$(stat -L -c %s "$FQ")"
rec snakemake_version "$(snakemake --version 2>&1 | tail -n 1)"
rec threads "$T"

if ! command -v docker > /dev/null 2>&1; then sudo dnf install -y -q docker > /dev/null 2>&1; fi
sudo systemctl is-active --quiet docker || sudo systemctl start docker > /dev/null 2>&1
rec docker_ready "$(now)"
case "$(uname -m)" in
  aarch64) IMG=${snakemake_params[arm64]} ;;
  x86_64)  IMG=${snakemake_params[x86_64]} ;;
  *) fail "no seqkit pin for $(uname -m)"; exit 1 ;;
esac
sudo docker pull -q "$IMG" > /dev/null 2>&1
GOT=$(sudo docker inspect --format '{{index .RepoDigests 0}}' "$IMG" 2>/dev/null)
rec image "$GOT"
[ "$GOT" = "$IMG" ] || fail "image digest $GOT != $IMG"
rec image_pulled "$(now)"

# seqkit as the container's only process, its VmHWM (the kernel's own peak-RSS counter, which
# only rises) read until it exits.
SEQ='export PATH=/opt/conda/bin:/usr/local/bin:$PATH
seqkit version 1>&2
seqkit stats -T -a -j "$1" "/w/$2" > /tmp/w.out &
p=$!; h=0
while [ -r /proc/$p/status ] && ! grep -q "^State:.*Z" /proc/$p/status; do
  x=$(awk "/^VmHWM/{print \$2}" /proc/$p/status 2>/dev/null); [ -n "$x" ] && h=$x; sleep 0.2
done
wait $p; rc=$?
echo "VMHWM_KB $h" 1>&2
cat /tmp/w.out; exit $rc'
D=$(cd "$(dirname "$FQ")" && pwd)
S0=$(now)
sudo docker run --rm -v "$D":/w:ro "$IMG" bash -c "$SEQ" seqkit "$T" "$(basename "$FQ")" \
  > /tmp/stats.raw 2> /tmp/stats.err
RC=$?
S1=$(now)
rec seqkit_start "$S0"; rec seqkit_end "$S1"; rec seqkit_exit "$RC"
rec seqkit_version "$(grep -m1 -i '^seqkit' /tmp/stats.err)"
rec seqkit_vmhwm_kib "$(awk '/^VMHWM_KB/{print $2}' /tmp/stats.err)"
[ "$RC" = 0 ] || { fail "seqkit exit $RC"; cat /tmp/stats.err >> "$LOG"; }

# Relabel column 1 (the path inside the container) with the file name; keep seqkit's columns.
awk -v f="$F" 'BEGIN{FS=OFS="\t"} NR==1{print; next} {$1=f; print}' /tmp/stats.raw > "$OUT"
cat "$OUT" >> "$LOG"

# The published values this file has to reproduce.
SHA=$(sha256sum "$FQ" | awk '{print $1}')
rec sha256_done "$(now)"
W_SHA=$(awk -F'\t' 'NR==2{print $2}' "$TRUTH"); W_N=$(awk -F'\t' 'NR==2{print $3}' "$TRUTH")
W_L=$(awk -F'\t' 'NR==2{print $4}' "$TRUTH")
col() { awk -F'\t' -v c="$1" 'NR==1{for(i=1;i<=NF;i++) if($i==c) k=i} NR==2{print $k}' "$OUT"; }
N=$(col num_seqs); L=$(col sum_len)
rec sha256 "$SHA"; rec num_seqs "$N"; rec sum_len "$L"
[ "$SHA" = "$W_SHA" ] || fail "sha256 $SHA != pinned $W_SHA"
[ -n "$N" ] && [ "$N" = "$W_N" ] || fail "num_seqs $N != published $W_N"
[ -n "$L" ] && [ "$L" = "$W_L" ] || fail "sum_len $L != published $W_L"
[ "$(awk 'END{print NR}' "$OUT")" = 2 ] || fail "stats table is not one header + one row"

rec disk_used_kib "$(df -k --output=used "$D" | tail -n 1 | tr -d ' ')"
rec disk_size_kib "$(df -k --output=size "$D" | tail -n 1 | tr -d ' ')"
rec script_end "$(now)"
rec fails "$FAIL"
cat "$LOG" >&2   # into spawn's command.log too, which survives a failed job
exit $FAIL
