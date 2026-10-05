#!/usr/bin/env bash
# What does one kraken2 classification against the full 1.1 TiB RefSeq DB actually cost?
#
# The number this directory still lacks. The previous attempt lost it to TTL because the run
# was one 1M-read invocation that reported only on completion -- so a kill returned nothing,
# and instance store being ephemeral meant re-copying 1107 GiB to retry. Two fixes:
#
#   1. A READ-COUNT LADDER, cheap first (10k, 100k, 1M). Each rung reports before the next
#      starts, so the measurement is monotonically useful: a TTL kill costs the largest rung,
#      never all of them.
#   2. Every rung STREAMS -- kraken2's --output is sampled while it runs, so even a killed
#      rung yields a rate instead of nothing.
#
# A WARM REPEAT of the 100k rung is the point, not a bonus: it separates "what the first
# sample costs" from "what the next one costs" on the same box, which is the whole
# amortization question. kraken2 --memory-mapping faults pages in as it goes, so 247 GiB of
# page cache against a 1107 GiB table should make rung 2 of the same size cheaper. The ladder
# is therefore NOT independent -- later rungs inherit warmth -- and the warm repeat is how we
# quantify that rather than pretend it away.
# MEASURED: spawn runs --command under `bash -e` ($- == "ehB" before this line runs), and
# `set -uo pipefail` does NOT clear an inherited -e. That silently killed four runs today:
# a SIGPIPE'd `zcat | sed` under pipefail (rc=141), a kraken2 that exited non-zero, and a
# `wait` on a failed background job -- each time exiting BEFORE the line that would have
# reported why. So disable it explicitly and check every status by hand.
set +e
set -uo pipefail
B="${COOKBOOK_BUCKET:?}"
W=/tmp/w; mkdir -p "$W"
R="$W/result.txt"; : > "$R"
say(){ printf '%s\t%s\n' "$1" "${2-}" | tee -a "$R"; }
push(){ aws s3 cp "$R" "s3://$B/measurements/lith-kraken2-roda/kraken2-cost.txt" --only-show-errors 2>/dev/null || true; }
trap 'say trap_exit_line "$LINENO"; push' EXIT

DB=s3://kraken2-ncbi-refseq-complete-v205/Kraken2_RefSeqCompleteV205
K2=quay.io/aarchbio/kraken2@sha256:fc6dd9becb7fec054ee01575d85e12c7fc509e53001c0ade5ed2e095c3cbe455
RATE=2.3514      # $/hr r8gd.8xlarge us-west-2, from the Price List API
T_START=$(date +%s)
cost(){ awk -v s="$1" -v r="$RATE" 'BEGIN{printf "%.4f", r*s/3600}'; }

say instance_type "$(curl -s -m 3 -H "X-aws-ec2-metadata-token: $(curl -sX PUT -m 3 http://169.254.169.254/latest/api/token -H 'X-aws-ec2-metadata-token-ttl-seconds: 300')" http://169.254.169.254/latest/meta-data/instance-type || echo '?')"
NP=$(nproc --all); say nproc "$NP"
say mem_gib "$(awk '/MemTotal/{printf "%.0f", $2/1048576}' /proc/meminfo)"; push

# ---- NVMe instance store (raw; not a spawn concern -- it is physically attached)
ROOTDEV=$(findmnt -no SOURCE / | sed 's/p\?[0-9]*$//;s|/dev/||')
DEV=/dev/$(lsblk -dno NAME,SIZE,TYPE | awk -v r="$ROOTDEV" '$3=="disk" && $1!=r {print $1" "$2}' | sort -k2 -hr | head -1 | awk '{print $1}')
[ -b "$DEV" ] || { say ABORT "no instance-store device"; exit 1; }
sudo dnf install -y -q docker >/dev/null 2>&1
sudo systemctl enable --now docker >/dev/null 2>&1
sudo mkfs.xfs -f -q "$DEV" >/dev/null 2>&1 || sudo mkfs.ext4 -F -q "$DEV" >/dev/null 2>&1
sudo mkdir -p /mnt/nvme && sudo mount "$DEV" /mnt/nvme && sudo mkdir -p /mnt/nvme/db /mnt/nvme/out
sudo chown -R "$(whoami)" /mnt/nvme
# THE FIX. chown to the instance user is NOT enough: the container runs as the IMAGE's user,
# so kraken2 could load the database fine and then die with
#   Unable to open file: /w/out-10k.kraken, reason: Permission denied
# -- the same ownership trap this project documents for staged INPUTS, arriving through an
# output path. 1777 is what host /tmp uses, for exactly this reason. Measured: this killed
# the kraken2 phase of two runs, the first of which I misdiagnosed as a TTL/mmap problem.
sudo chmod 1777 /mnt/nvme /mnt/nvme/out
say nvme "$(df -h /mnt/nvme | awk 'NR==2{print $2}')"
sudo docker pull -q "$K2" >/dev/null 2>&1
say image_pulled yes
# Prove the container can WRITE where the rungs will write, before spending 28 minutes on a
# copy. Two runs died on this; it costs one second to check.
if sudo docker run --rm -v /mnt/nvme:/w "$K2" bash -lc 'touch /w/out/.wtest && echo ok' 2>/dev/null | grep -q ok; then
  say container_can_write yes; sudo rm -f /mnt/nvme/out/.wtest
else
  say ABORT "container cannot write /mnt/nvme/out -- the perms fix regressed"; push; exit 1
fi
push

# ---- reads first, so a slow copy never costs us the fixture
say "== reads ==" ""
aws s3 cp "s3://$B/inputs/bwa-real/SRR062634_1.filt.fastq.gz" "$W/r1.fq.gz" --only-show-errors
# NOT `sed ...;Nq`: sed quitting early closes the pipe, zcat takes SIGPIPE (141), and
# pipefail turns that into a failed pipeline. Read the whole stream instead -- slower by
# seconds, and it cannot take the run down.
zcat "$W/r1.fq.gz" | sed -n '1,4000000p' > /mnt/nvme/reads1m.fq 2>/dev/null
sed -n '1,400000p' /mnt/nvme/reads1m.fq > /mnt/nvme/reads100k.fq
sed -n '1,40000p'  /mnt/nvme/reads1m.fq > /mnt/nvme/reads10k.fq
rm -f "$W/r1.fq.gz"
for f in 10k 100k 1m; do say "  reads_$f" "$(( $(wc -l < /mnt/nvme/reads$f.fq) / 4 ))"; done
push

# ---- copy the runtime set. 128-way, up from 64: last run used only 41% of a 15 Gbps NIC,
# so it was bounded by write throughput or client concurrency, not the network.
say "== copy the 1108 GiB runtime set to local NVMe ==" ""
aws configure set default.s3.max_concurrent_requests 128
aws configure set default.s3.multipart_chunksize 128MB
aws configure set default.s3.max_queue_size 20000
( while sleep 30; do
    printf '  copy_progress\t%s GiB / %s s\n' \
      "$(du -s --block-size=1G /mnt/nvme/db 2>/dev/null | awk '{print $1}')" "$(( $(date +%s) - T_START ))" >> "$R"
    aws s3 cp "$R" "s3://$B/measurements/lith-kraken2-roda/kraken2-cost.txt" --only-show-errors 2>/dev/null
  done ) & PROG=$!
T2=$(date +%s)
for f in opts.k2d taxo.k2d hash.k2d; do
  aws s3 cp "$DB/$f" "/mnt/nvme/db/$f" --no-sign-request --only-show-errors 2>/dev/null
done
T3=$(date +%s); kill $PROG 2>/dev/null
BY=$(du -sb /mnt/nvme/db | awk '{print $1}')
say copy_wall_s "$(( T3 - T2 ))"
say copy_gib "$(awk -v b="$BY" 'BEGIN{printf "%.1f", b/2^30}')"
say copy_gbps "$(awk -v b="$BY" -v s="$((T3-T2))" 'BEGIN{if(s>0) printf "%.2f", b/1e9/s}')"
say copy_cost_usd "$(cost $((T3-T2)))"
[ "$BY" -lt 1180000000000 ] && { say ABORT "copy incomplete: $BY bytes"; push; exit 1; }
push

# ---- the ladder. Each rung samples kraken2's own --output while it runs.
rung(){                       # rung <label> <readsfile> <nreads>
  local L="$1" F="$2" N="$3" OUT=/mnt/nvme/out/out-$1.kraken
  say "== rung $L: $N reads ==" ""
  say "  cache_gib_before" "$(free -g | awk '/^Mem:/{print $6}')"
  local T4=$(date +%s)
  sudo docker run --rm -v /mnt/nvme:/w "$K2" bash -lc "
    export PATH=/opt/conda/bin:\$PATH
    kraken2 --db /w/db --memory-mapping --threads $NP \
      --report /w/out/rep-$L.txt --output /w/out/out-$L.kraken /w/$(basename $F)" > "$W/k2-$L.log" 2>&1 &
  local DP=$!
  while kill -0 $DP 2>/dev/null; do
    sleep 20
    printf '  %s_progress\t%s reads / %s s\n' "$L" \
      "$(wc -l < "$OUT" 2>/dev/null | tr -d ' ' || echo 0)" "$(( $(date +%s) - T4 ))" >> "$R"
    aws s3 cp "$R" "s3://$B/measurements/lith-kraken2-roda/kraken2-cost.txt" --only-show-errors 2>/dev/null
  done
  wait $DP; local RC=$?      # safe now that -e is off; under -e this exited silently
  local T5=$(date +%s) S=$(( $(date +%s) - T4 ))
  say "  ${L}_rc" "$RC"
  say "  ${L}_wall_s" "$S"
  say "  ${L}_reads_per_min" "$(awk -v n="$N" -v s="$S" 'BEGIN{if(s>0) printf "%.0f", n*60/s}')"
  say "  ${L}_classified_pct" "$(awk 'NR==1{print $1}' /mnt/nvme/out/rep-$L.txt 2>/dev/null || echo '-')"
  say "  ${L}_out_lines" "$(wc -l < "$OUT" 2>/dev/null | tr -d ' ' || echo 0)"
  say "  ${L}_compute_usd" "$(cost $S)"
  say "  ${L}_cache_gib_after" "$(free -g | awk '/^Mem:/{print $6}')"
  tail -3 "$W/k2-$L.log" | sed "s/^/  ${L}_log\t/" >> "$R"
  push
}
rung 10k  /mnt/nvme/reads10k.fq  10000
rung 100k /mnt/nvme/reads100k.fq 100000
rung 1m   /mnt/nvme/reads1m.fq   1000000
# the amortization measurement: identical work, warm page cache
rung 100kwarm /mnt/nvme/reads100k.fq 100000

say "== totals ==" ""
say total_box_s "$(( $(date +%s) - T_START ))"
say total_box_usd "$(cost $(( $(date +%s) - T_START )))"
aws s3 cp /mnt/nvme/out/rep-1m.txt "s3://$B/measurements/lith-kraken2-roda/kraken2-1m.report" --only-show-errors 2>/dev/null || true
say DONE yes; push
