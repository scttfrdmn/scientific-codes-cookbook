#!/usr/bin/env bash
# Is kraken2 queue-depth-bound or CPU-bound? And is the S3 concurrency plateau the client
# or the instance?
#
# Four questions on one box, because the 1107 GiB copy is a fixed cost and everything else
# is minutes:
#
#   Q1  S3 concurrency sweep on 32 vCPU / 15 Gbps SUSTAINED. The earlier sweep plateaued at
#       ~790 lookups/s on a c8g.2xlarge and I blamed "my Python client" WITHOUT TESTING IT.
#       c8g.2xlarge is "Up to 15 Gigabit" -- burstable, with smaller PPS and connection
#       allowances. 790 x 4 KiB = 3.2 MB/s, so raw bandwidth was never the limit, but PPS and
#       concurrent connections plausibly are, and both scale with instance size. If the
#       plateau MOVES here, it was the instance, not the client.
#   Q2  kraken2 --threads 4/8/16/32 on identical work (100k reads). Threads do double duty in
#       kraken2 as CPU parallelism AND as I/O queue depth. Linear in threads => depth binds.
#       Plateau => CPU binds. This is the number that tells a redesign what to attack.
#   Q3  a genuinely COLD rung. Every rung in the previous ladder started with 241-244 GiB of
#       page cache, because the copy leaves it full of the database -- so "warm vs cold" was
#       never tested. drop_caches first, then repeat.
#   Q4  the copy's WALL cost, not just its dollar cost. It is a stage-then-run barrier:
#       minutes in which no science happens, paid again on every fresh box.
#
# spawn runs --command under bash -e and `set -uo pipefail` does not clear it (spawn#707), so:
set +e
set -uo pipefail
B="${COOKBOOK_BUCKET:?}"
W=/tmp/w; mkdir -p "$W"; R="$W/result.txt"; : > "$R"
say(){ printf '%s\t%s\n' "$1" "${2-}" | tee -a "$R"; }
push(){ aws s3 cp "$R" "s3://$B/measurements/lith-kraken2-roda/threadsweep.txt" --only-show-errors 2>/dev/null || true; }
trap 'say trap_exit "rc=$?"; push' EXIT
DB=s3://kraken2-ncbi-refseq-complete-v205/Kraken2_RefSeqCompleteV205
K2=quay.io/aarchbio/kraken2@sha256:fc6dd9becb7fec054ee01575d85e12c7fc509e53001c0ade5ed2e095c3cbe455
RATE=2.3514
T_START=$(date +%s)
cost(){ awk -v s="$1" -v r="$RATE" 'BEGIN{printf "%.4f", r*s/3600}'; }

NP=$(nproc --all)
say instance_type "$(curl -s -m 3 -H "X-aws-ec2-metadata-token: $(curl -sX PUT -m 3 http://169.254.169.254/latest/api/token -H 'X-aws-ec2-metadata-token-ttl-seconds: 300')" http://169.254.169.254/latest/meta-data/instance-type || echo '?')"
say nproc "$NP"; say mem_gib "$(awk '/MemTotal/{printf "%.0f",$2/1048576}' /proc/meminfo)"; push

# ---- Q1 first: cheap, and independent of the copy, so a later failure cannot cost it
sudo dnf install -y -q docker python3-pip >/dev/null 2>&1
sudo systemctl enable --now docker >/dev/null 2>&1
pip3 install --quiet --disable-pip-version-check boto3 >/dev/null 2>&1
say "== Q1: S3 concurrency sweep on this NIC (was it the client or the instance?) ==" ""
push
python3 -u - <<'PY' 2>&1 | tee -a "$R"
import boto3, botocore, random, time
from concurrent.futures import ThreadPoolExecutor
BUCKET="kraken2-ncbi-refseq-complete-v205"; KEY="Kraken2_RefSeqCompleteV205/hash.k2d"
cfg=botocore.config.Config(signature_version=botocore.UNSIGNED, max_pool_connections=4096,
                           retries={"max_attempts":3,"mode":"adaptive"})
s3=boto3.client("s3",config=cfg); SZ=s3.head_object(Bucket=BUCKET,Key=KEY)["ContentLength"]
def get(o): return len(s3.get_object(Bucket=BUCKET,Key=KEY,Range="bytes=%d-%d"%(o,o+4095))["Body"].read())
print("  depth\tn\tlookups_per_s\tMB_per_s")
base=None
for d,n in ((1,40),(16,320),(64,1280),(256,2560),(1024,5120),(2048,8192)):
    random.seed(42); offs=[random.randrange(0,SZ-4096) for _ in range(n)]
    t0=time.time()
    if d==1:
        for o in offs: get(o)
    else:
        with ThreadPoolExecutor(max_workers=d) as ex: list(ex.map(get,offs))
    dt=time.time()-t0; r=n/dt
    if base is None: base=r
    print("  %d\t%d\t%.1f\t%.2f\t(%.0fx)"%(d,n,r,n*4096/1e6/dt,r/base))
PY
push

# ---- nvme + the copy (Q4 measures its wall, not just its dollars)
ROOTDEV=$(findmnt -no SOURCE / | sed 's/p\?[0-9]*$//;s|/dev/||')
DEV=/dev/$(lsblk -dno NAME,SIZE,TYPE | awk -v r="$ROOTDEV" '$3=="disk" && $1!=r {print $1" "$2}' | sort -k2 -hr | head -1 | awk '{print $1}')
sudo mkfs.xfs -f -q "$DEV" >/dev/null 2>&1 || sudo mkfs.ext4 -F -q "$DEV" >/dev/null 2>&1
sudo mkdir -p /mnt/nvme && sudo mount "$DEV" /mnt/nvme && sudo mkdir -p /mnt/nvme/db /mnt/nvme/out
sudo chown -R "$(whoami)" /mnt/nvme && sudo chmod 1777 /mnt/nvme /mnt/nvme/out
sudo docker pull -q "$K2" >/dev/null 2>&1
sudo docker run --rm -v /mnt/nvme:/w "$K2" bash -lc 'touch /w/out/.t && echo ok' 2>/dev/null | grep -q ok \
  && say container_can_write yes || { say ABORT "container cannot write"; push; exit 1; }
sudo rm -f /mnt/nvme/out/.t
aws s3 cp "s3://$B/inputs/bwa-real/SRR062634_1.filt.fastq.gz" "$W/r1.fq.gz" --only-show-errors
zcat "$W/r1.fq.gz" | sed -n '1,400000p' > /mnt/nvme/reads100k.fq 2>/dev/null
say reads "$(( $(wc -l < /mnt/nvme/reads100k.fq) / 4 ))"; rm -f "$W/r1.fq.gz"; push

say "== Q4: the copy is a stage-then-run barrier -- measure its WALL, not just its $ ==" ""
aws configure set default.s3.max_concurrent_requests 64
aws configure set default.s3.multipart_chunksize 64MB
( while sleep 60; do printf '  copy_prog\t%s GiB / %s s\n' "$(du -s --block-size=1G /mnt/nvme/db 2>/dev/null|awk '{print $1}')" "$(( $(date +%s)-T_START ))" >> "$R"
  aws s3 cp "$R" "s3://$B/measurements/lith-kraken2-roda/threadsweep.txt" --only-show-errors 2>/dev/null; done ) & PROG=$!
T2=$(date +%s)
for f in opts.k2d taxo.k2d hash.k2d; do aws s3 cp "$DB/$f" "/mnt/nvme/db/$f" --no-sign-request --only-show-errors 2>/dev/null; done
T3=$(date +%s); kill $PROG 2>/dev/null
BY=$(du -sb /mnt/nvme/db | awk '{print $1}')
say copy_wall_s "$(( T3 - T2 ))"; say copy_wall_min "$(awk -v s=$((T3-T2)) 'BEGIN{printf "%.1f",s/60}')"
say copy_gbps "$(awk -v b=$BY -v s=$((T3-T2)) 'BEGIN{printf "%.2f",b/1e9/s}')"
say copy_cost_usd "$(cost $((T3-T2)))"
say copy_barrier "no science happens during these $(awk -v s=$((T3-T2)) 'BEGIN{printf "%.0f",s/60}') minutes"
[ "$BY" -lt 1180000000000 ] && { say ABORT "copy incomplete"; push; exit 1; }
push

rung(){   # rung <label> <threads> [cold]
  local L="$1" TH="$2" COLD="${3-}" T4 S RC
  if [ "$COLD" = cold ]; then sync; sudo sh -c 'echo 3 > /proc/sys/vm/drop_caches'; sleep 3; fi
  say "-- $L: --threads $TH ${COLD} --" ""
  say "  ${L}_cache_gib_before" "$(free -g | awk '/^Mem:/{print $6}')"; push
  T4=$(date +%s)
  sudo docker run --rm -v /mnt/nvme:/w "$K2" bash -lc "
    export PATH=/opt/conda/bin:\$PATH
    kraken2 --db /w/db --memory-mapping --threads $TH \
      --report /w/out/rep-$L.txt --output /w/out/out-$L.kraken /w/reads100k.fq" > "$W/k2-$L.log" 2>&1 &
  local DP=$!
  while kill -0 $DP 2>/dev/null; do sleep 20
    say "  ${L}_prog" "$(wc -l < /mnt/nvme/out/out-$L.kraken 2>/dev/null|tr -d ' '||echo 0) / $(( $(date +%s)-T4 ))s"; push; done
  wait $DP; RC=$?; S=$(( $(date +%s)-T4 ))
  say "  ${L}_rc" "$RC"; say "  ${L}_wall_s" "$S"
  say "  ${L}_kraken_rate" "$(grep -oE '\([0-9.]+ Kseq/m' "$W/k2-$L.log" | tr -d '(' | head -1)"
  say "  ${L}_kraken_secs" "$(grep -oE 'processed in [0-9.]+s' "$W/k2-$L.log" | grep -oE '[0-9.]+' | head -1)"
  say "  ${L}_classified_pct" "$(awk 'NR==1{print $1}' /mnt/nvme/out/rep-$L.txt 2>/dev/null||echo -)"
  say "  ${L}_compute_usd" "$(cost $S)"
  push
}
say "== Q2: threads 4 -> 32 on IDENTICAL work. linear => queue depth binds; plateau => CPU ==" ""
for th in 4 8 16 32; do rung "t$th" "$th"; done
say "== Q3: the cold rung the previous ladder never had (drop_caches first) ==" ""
rung t32cold 32 cold
say total_box_s "$(( $(date +%s) - T_START ))"
say total_box_usd "$(cost $(( $(date +%s) - T_START )))"
say DONE yes; push
