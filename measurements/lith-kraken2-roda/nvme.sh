#!/usr/bin/env bash
# Is local NVMe the right home for a 1.1 TiB kraken2 hash table, and what does a result cost?
#
# Three phases on ONE box, which is the point -- same chip, same NIC, same kernel, so the
# mount and the NVMe path are directly comparable instead of being two measurements.
#
#   P1  lith mount, 200 random 4 KiB probes, counting NIC bytes.
#       This measures the two things the amplification argument was ASSUMING:
#       lith's effective fill size (lith#232 says 8 MiB, lith#118 implies a 1 MiB chunk
#       cache -- a 7x difference in the predicted amplification) and the per-probe latency
#       in-region on this box. Bytes-off-the-NIC / bytes-requested IS the amplification,
#       measured rather than modelled.
#   P2  parallel copy of the runtime set (hash.k2d + taxo.k2d + opts.k2d, ~1108 GiB) from
#       S3 to local NVMe, timed, with progress streamed so a TTL kill still yields the rate.
#   P3  200 random 4 KiB probes on NVMe (the direct comparison against P1), then kraken2
#       --memory-mapping over the NVMe copy on 1M real reads -> reads/min and $/sample.
#
# Streams every result to S3 as it goes: `spawn launch --command` does not stage logs out
# (spawn#643's pre-stop flush is a `task run` feature), and a report-at-the-end design
# already lost one run here.
set -uo pipefail
B="${COOKBOOK_BUCKET:?}"
W=/tmp/w; mkdir -p "$W"
R="$W/result.txt"; : > "$R"
say(){ printf '%s\t%s\n' "$1" "${2-}" | tee -a "$R"; }
push(){ aws s3 cp "$R" "s3://$B/measurements/lith-kraken2-roda/nvme.txt" --only-show-errors 2>/dev/null || true; }
trap 'say trap_exit_line "$LINENO"; push' EXIT
nicrx(){ awk '/eth0|ens|enp/{s+=$2} END{print s+0}' /proc/net/dev; }

DB=s3://kraken2-ncbi-refseq-complete-v205/Kraken2_RefSeqCompleteV205
K2=quay.io/aarchbio/kraken2@sha256:fc6dd9becb7fec054ee01575d85e12c7fc509e53001c0ade5ed2e095c3cbe455
LITH_URL=https://github.com/scttfrdmn/lith/releases/download/v1.6.0/lith_linux_arm64
LITH_SHA=fa361748a2bd611594f0f2801e50157cb80e0135da6936b82733287b30ba7259
RATE=2.3514          # $/hr, r8gd.8xlarge us-west-2, read from the Price List API

T_START=$(date +%s)
say instance_type "$(curl -s -m 3 -H "X-aws-ec2-metadata-token: $(curl -sX PUT -m 3 http://169.254.169.254/latest/api/token -H 'X-aws-ec2-metadata-token-ttl-seconds: 300')" http://169.254.169.254/latest/meta-data/instance-type || echo '?')"
say nproc "$(nproc --all)"
say mem_gib "$(awk '/MemTotal/{printf "%.0f", $2/1048576}' /proc/meminfo)"
push

# ---- P0: the NVMe instance store arrives RAW. Nothing in spawn is involved: instance
# store is physically attached, so it just appears as a block device that is not the root.
say "== P0: find and format the instance-store NVMe ==" ""
ROOTDEV=$(findmnt -no SOURCE / | sed 's/p\?[0-9]*$//;s|/dev/||')
say root_dev "$ROOTDEV"
NVME=$(lsblk -dno NAME,SIZE,TYPE | awk -v r="$ROOTDEV" '$3=="disk" && $1!=r {print $1" "$2}' | sort -k2 -hr | head -1)
say nvme_found "${NVME:-NONE}"
DEV=/dev/$(echo "$NVME" | awk '{print $1}')
if [ ! -b "$DEV" ]; then say ABORT "no instance-store device"; push; exit 1; fi
sudo dnf install -y -q docker fuse fuse3 >/dev/null 2>&1
sudo systemctl enable --now docker >/dev/null 2>&1
sudo sed -i 's/^#\s*user_allow_other/user_allow_other/' /etc/fuse.conf
sudo mkfs.xfs -f -q "$DEV" >/dev/null 2>&1 || sudo mkfs.ext4 -F -q "$DEV" >/dev/null 2>&1
sudo mkdir -p /mnt/nvme && sudo mount "$DEV" /mnt/nvme && sudo mkdir -p /mnt/nvme/db
sudo chown -R "$(whoami)" /mnt/nvme
say nvme_mounted "$(df -h /mnt/nvme | awk 'NR==2{print $2" avail "$4" fs "$1}')"
push

# ---- P1: the measurement the amplification argument needed
curl -fsSL -o "$W/lith" "$LITH_URL"
echo "$LITH_SHA  $W/lith" | sha256sum -c - >/dev/null 2>&1 || { say lith_sha MISMATCH; exit 1; }
chmod +x "$W/lith"; LITH="$W/lith"
$LITH index build "$DB" --index-file "$W/db.lithidx" --no-sign-request >/dev/null 2>&1
sudo mkdir -p /mnt/k2db && sudo chown "$(whoami)" /mnt/k2db
$LITH mount "$DB" /mnt/k2db --index-file "$W/db.lithidx" --no-sign-request --allow-other --daemon >/dev/null 2>&1
for i in $(seq 1 60); do mountpoint -q /mnt/k2db && break; sleep 2; done
say "== P1: lith random 4 KiB probes, with NIC bytes -> MEASURED amplification ==" ""
say mount_ok "$(mountpoint -q /mnt/k2db && echo yes || echo NO)"
RX0=$(nicrx)
python3 -u - <<'PY' 2>&1 | sed 's/^/  lith_probe\t/' | tee -a "$R"
import os, random, time
fd = os.open("/mnt/k2db/hash.k2d", os.O_RDONLY)
sz = os.fstat(fd).st_size
random.seed(1); n = 200; t0 = time.time()
for _ in range(n): os.pread(fd, 4096, random.randrange(0, sz - 4096))
dt = time.time() - t0
print("%d probes %.1fs %.1f/s %.0f ms_each" % (n, dt, n/dt, 1000*dt/n))
PY
RX1=$(nicrx)
say lith_probe_nic_mib "$(( (RX1 - RX0) / 1048576 ))"
# 200 probes x 4 KiB = 0.78 MiB requested. NIC bytes / requested = the real amplification,
# and NIC bytes / 200 = the real fill size.
say lith_amplification "$(awk -v b="$((RX1-RX0))" 'BEGIN{printf "%.0fx over 200x4KiB", b/(200*4096)}')"
say lith_fill_kib_per_probe "$(awk -v b="$((RX1-RX0))" 'BEGIN{printf "%.0f", b/200/1024}')"
push
fusermount -u /mnt/k2db 2>/dev/null || sudo umount /mnt/k2db 2>/dev/null || true

# ---- P2: the copy
say "== P2: parallel copy of the RUNTIME set (hash.k2d+taxo.k2d+opts.k2d) to NVMe ==" ""
aws configure set default.s3.max_concurrent_requests 64
aws configure set default.s3.multipart_chunksize 64MB
aws configure set default.s3.max_queue_size 10000
( while sleep 20; do
    printf '  copy_progress\t%s GiB / %s s\n' \
      "$(du -s --block-size=1G /mnt/nvme/db 2>/dev/null | awk '{print $1}')" \
      "$(( $(date +%s) - T_START ))" >> "$R"
    aws s3 cp "$R" "s3://$B/measurements/lith-kraken2-roda/nvme.txt" --only-show-errors 2>/dev/null
  done ) & PROG=$!
T2=$(date +%s)
for f in opts.k2d taxo.k2d hash.k2d; do
  aws s3 cp "$DB/$f" "/mnt/nvme/db/$f" --no-sign-request --only-show-errors 2>/dev/null
  say "  copied" "$f $(stat -c%s "/mnt/nvme/db/$f" 2>/dev/null || echo FAILED)"
done
T3=$(date +%s)
kill $PROG 2>/dev/null
BYTES=$(du -sb /mnt/nvme/db | awk '{print $1}')
say copy_wall_s "$(( T3 - T2 ))"
say copy_gib "$(awk -v b="$BYTES" 'BEGIN{printf "%.1f", b/2^30}')"
say copy_gbps "$(awk -v b="$BYTES" -v s="$((T3-T2))" 'BEGIN{if(s>0) printf "%.2f GB/s", b/1e9/s}')"
say copy_cost_usd "$(awk -v s="$((T3-T2))" -v r="$RATE" 'BEGIN{printf "%.4f", r*s/3600}')"
push

# ---- P3: NVMe latency, then the real run
say "== P3: random 4 KiB probes on NVMe -- the direct comparison to P1 ==" ""
python3 -u - <<'PY' 2>&1 | sed 's/^/  nvme_probe\t/' | tee -a "$R"
import os, random, time
fd = os.open("/mnt/nvme/db/hash.k2d", os.O_RDONLY)
sz = os.fstat(fd).st_size
random.seed(1); n = 2000; t0 = time.time()
for _ in range(n): os.pread(fd, 4096, random.randrange(0, sz - 4096))
dt = time.time() - t0
print("%d probes %.2fs %.0f/s %.3f ms_each" % (n, dt, n/dt, 1000*dt/n))
PY
push

say "== P3: kraken2 --memory-mapping on 1M real reads from NVMe ==" ""
aws s3 cp "s3://$B/inputs/bwa-real/SRR062634_1.filt.fastq.gz" "$W/r1.fq.gz" --only-show-errors
zcat "$W/r1.fq.gz" > "$W/all.fq" 2>/dev/null
sed -n '1,4000000p' "$W/all.fq" > /mnt/nvme/reads.fq; rm -f "$W/all.fq" "$W/r1.fq.gz"
NR=$(wc -l < /mnt/nvme/reads.fq); READS=$(( NR / 4 ))
say reads_in "$READS"; push
sudo docker pull -q "$K2" >/dev/null 2>&1
T4=$(date +%s)
sudo docker run --rm -v /mnt/nvme:/w "$K2" bash -lc '
  export PATH=/opt/conda/bin:$PATH
  kraken2 --db /w/db --memory-mapping --threads '"$(nproc --all)"' \
    --report /w/out.report --output /w/out.kraken /w/reads.fq' > "$W/k2.log" 2>&1
RC=$?
T5=$(date +%s)
say kraken2_rc "$RC"
say kraken2_wall_s "$(( T5 - T4 ))"
say kraken2_reads_per_min "$(awk -v r="$READS" -v s="$((T5-T4))" 'BEGIN{if(s>0) printf "%.0f", r*60/s}')"
say classified_lines "$(wc -l < /mnt/nvme/out.kraken 2>/dev/null | tr -d ' ' || echo 0)"
say classified_pct "$(awk 'NR==1{print $1}' /mnt/nvme/out.report 2>/dev/null || echo '-')"
say report_lines "$(wc -l < /mnt/nvme/out.report 2>/dev/null | tr -d ' ' || echo 0)"
say cost_classify_usd "$(awk -v s="$((T5-T4))" -v r="$RATE" 'BEGIN{printf "%.4f", r*s/3600}')"
say cost_total_box_usd "$(awk -v s="$(( $(date +%s) - T_START ))" -v r="$RATE" 'BEGIN{printf "%.4f", r*s/3600}')"
tail -6 "$W/k2.log" | sed 's/^/  k2log\t/' | tee -a "$R"
push
aws s3 cp /mnt/nvme/out.report "s3://$B/measurements/lith-kraken2-roda/nvme-out.report" --only-show-errors 2>/dev/null || true
say DONE yes; push
