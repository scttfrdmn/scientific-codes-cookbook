#!/usr/bin/env bash
# CANARY: can kraken2 classify against a 1.1 TiB RODA database it never copies?
#
# The question is NOT throughput, it is whether the access PATTERN survives. STAR's lith
# result (28 s, ~1 GiB/s) was a sequential index LOAD. kraken2 --memory-mapping does
# RANDOM page faults over a 1.1 TiB hash table and touches only a sliver of it, which is
# the adversarial case for FUSE+S3 latency. So this measures the fault rate and reports it
# incrementally -- a TTL or cost-limit kill must still return the number, which spawn
# 0.116.0 makes possible by flushing command.log on a lifecycle kill (spawn#643).
set -uo pipefail
B="${COOKBOOK_BUCKET:?}"
W=/tmp/w; mkdir -p "$W"
R="$W/result.txt"; : > "$R"
# ${2-} not "$2": this runs under `set -u`, and the single-argument header calls below
# (say "== ... ==") would otherwise reference an unbound $2 and kill the script at the
# first one. That is exactly how the first attempt died, before it indexed anything.
say(){ printf '%s\t%s\n' "$1" "${2-}" | tee -a "$R"; }
push(){ aws s3 cp "$R" "s3://$B/measurements/lith-kraken2-roda/canary.txt" --only-show-errors 2>/dev/null || true; }

DB=s3://kraken2-ncbi-refseq-complete-v205/Kraken2_RefSeqCompleteV205
K2=quay.io/aarchbio/kraken2@sha256:fc6dd9becb7fec054ee01575d85e12c7fc509e53001c0ade5ed2e095c3cbe455
LITH_URL=https://github.com/scttfrdmn/lith/releases/download/v1.6.0/lith_linux_arm64
LITH_SHA=fa361748a2bd611594f0f2801e50157cb80e0135da6936b82733287b30ba7259

TOK=$(curl -sX PUT -m 3 http://169.254.169.254/latest/api/token -H 'X-aws-ec2-metadata-token-ttl-seconds: 300' 2>/dev/null || echo '')
say instance_type "$(curl -s -m 3 -H "X-aws-ec2-metadata-token: $TOK" http://169.254.169.254/latest/meta-data/instance-type || echo '?')"
say mem_total_gib "$(awk '/MemTotal/{printf "%.0f", $2/1048576}' /proc/meminfo)"
say nproc "$(nproc --all)"; push

sudo dnf install -y -q docker fuse fuse3 >/dev/null 2>&1
sudo systemctl enable --now docker >/dev/null 2>&1
sudo sed -i 's/^#\s*user_allow_other/user_allow_other/' /etc/fuse.conf
curl -fsSL -o "$W/lith" "$LITH_URL"
echo "$LITH_SHA  $W/lith" | sha256sum -c - >/dev/null 2>&1 && say lith_sha verified || { say lith_sha MISMATCH; push; exit 1; }
chmod +x "$W/lith"; LITH="$W/lith"
say lith_version "$($LITH version 2>&1 | head -1)"; push

say "== index 1.1 TiB of RODA (metadata only, public bucket) =="
T0=$(date +%s)
$LITH index build "$DB" --index-file "$W/db.lithidx" --no-sign-request > "$W/ix.log" 2>&1
T1=$(date +%s)
say lith_index_s "$(( T1 - T0 ))"
say lith_index_bytes "$(stat -c%s "$W/db.lithidx" 2>/dev/null || echo 0)"
sudo mkdir -p /mnt/k2db && sudo chown "$(whoami)" /mnt/k2db
$LITH mount "$DB" /mnt/k2db --index-file "$W/db.lithidx" --no-sign-request --allow-other --daemon > "$W/mt.log" 2>&1
for i in $(seq 1 60); do mountpoint -q /mnt/k2db && break; sleep 2; done
say mount_ok "$(mountpoint -q /mnt/k2db && echo yes || echo NO)"
say db_files_visible "$(ls /mnt/k2db 2>/dev/null | wc -l)"
say hash_k2d_apparent_bytes "$(stat -c%s /mnt/k2db/hash.k2d 2>/dev/null || echo 0)"
push

say "== a tiny read set: the fault rate is what we are measuring, not throughput =="
aws s3 cp "s3://$B/inputs/bwa-real/SRR062634_1.filt.fastq.gz" "$W/r1.fq.gz" --only-show-errors
say reads_fetch_rc "$?"
say reads_gz_bytes "$(stat -c%s "$W/r1.fq.gz" 2>/dev/null || echo 0)"
push
# 1,000 reads. Deliberately small: if the pattern is viable this is seconds, and if it is
# latency-bound we still get a rate instead of a timeout with nothing in it.
# NOT `zcat | head`: head exits at 4000 lines, zcat takes SIGPIPE and returns 141, and
# `set -o pipefail` turns that into a failed pipeline -- the trap this project has hit
# before. Decompress to a file, then cut.
zcat "$W/r1.fq.gz" > "$W/all.fq" 2>/dev/null; say unzip_rc "$?"
sed -n '1,4000p' "$W/all.fq" > "$W/reads.fq"; rm -f "$W/all.fq"
NR=$(wc -l < "$W/reads.fq" 2>/dev/null || echo 0)
say reads "$(( NR / 4 ))"
if [ "$NR" -lt 4 ]; then say ABORT "no reads to classify"; push; exit 1; fi
RX0=$(awk '/^rchar/{print $2}' /proc/self/io 2>/dev/null || echo 0)
NETRX0=$(awk '/eth0|ens/{print $2; exit}' /proc/net/dev 2>/dev/null || echo 0)
push

# Run kraken2 in the BACKGROUND and sample it, instead of waiting for it and reporting at
# the end. Two reasons, both learned the expensive way on the previous two attempts:
#
#   1. The thing being measured IS the fault rate over time. A single wall-clock number at
#      the end is a worse answer than the curve, and the curve is what says whether the
#      access pattern is viable or latency-bound.
#   2. A report-at-the-end design loses everything to a TTL kill. `spawn launch --command`
#      does not stage logs to S3 (the spawn#643 pre-stop flush is a `task run` feature), so
#      an unfinished run returned NOTHING -- which is exactly what happened here: kraken2
#      was still faulting at TTL and the whole measurement was lost.
#
# kraken2 writes per-read classifications to --output as it goes, so `wc -l` on that file
# is a direct progress counter. /dev/null was throwing away the progress signal.
T2=$(date +%s)
sudo docker run --rm -v /mnt/k2db:/db:ro -v "$W":/w "$K2" bash -lc '
  export PATH=/opt/conda/bin:$PATH
  kraken2 --db /db --memory-mapping --threads 4 \
    --report /w/out.report --output /w/out.kraken /w/reads.fq' > "$W/k2.log" 2>&1 &
K2PID=$!

say "== fault-rate curve: reads classified and S3 bytes pulled, sampled every 20 s =="
say "# columns" "elapsed_s / reads_done / s3_mib / lith_rss_mib"
LAST=0
for i in $(seq 1 85); do          # 85 x 20 s = 28 min, inside the 30 m TTL
  kill -0 "$K2PID" 2>/dev/null || break
  sleep 20
  EL=$(( $(date +%s) - T2 ))
  DONE=$(wc -l < "$W/out.kraken" 2>/dev/null | tr -d ' ' || echo 0)
  NRX=$(awk '/eth0|ens/{print $2; exit}' /proc/net/dev 2>/dev/null || echo 0)
  MIB=$(( (NRX - NETRX0) / 1048576 ))
  RSS=$(ps -o rss= -C lith 2>/dev/null | awk '{s+=$1} END{printf "%d", s/1024}')
  say "  sample" "$(printf '%s\t%s\t%s\t%s' "$EL" "${DONE:-0}" "$MIB" "${RSS:-0}")"
  # push only when something moved, or every 3rd tick, to keep the S3 PUTs cheap
  if [ "${DONE:-0}" -ne "$LAST" ] || [ $(( i % 3 )) -eq 0 ]; then push; fi
  LAST=${DONE:-0}
done
wait "$K2PID"; RC=$?
T3=$(date +%s)
say kraken2_rc "$RC"
say kraken2_wall_s "$(( T3 - T2 ))"
NETRX1=$(awk '/eth0|ens/{print $2; exit}' /proc/net/dev 2>/dev/null || echo 0)
say bytes_from_s3_mib "$(( (NETRX1 - NETRX0) / 1048576 ))"
say reads_classified_lines "$(wc -l < "$W/out.kraken" 2>/dev/null | tr -d ' ' || echo 0)"
say classified_pct "$(awk 'NR==1{print $1}' "$W/out.report" 2>/dev/null || echo '-')"
say report_lines "$(wc -l < "$W/out.report" 2>/dev/null | tr -d ' ' || echo 0)"
say reads_per_s "$(awk -v r="$(( NR / 4 ))" -v s="$(( T3 - T2 ))" 'BEGIN{if(s>0) printf "%.2f", r/s; else print "inf"}')"
tail -8 "$W/k2.log" 2>/dev/null | sed 's/^/k2log\t/' | tee -a "$R"
push
aws s3 cp "$W/out.report" "s3://$B/measurements/lith-kraken2-roda/out.report" --only-show-errors 2>/dev/null || true
say DONE yes; push
