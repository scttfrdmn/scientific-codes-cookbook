#!/usr/bin/env bash
# lith-vs-copy-real canary: the wiring and the no-tool baselines, on the HOST of a spawn host-path
# task (no `container` in the spec), before any workload is trusted.
#
#   1. where the box is (instance type, region) and that the bucket is in the same region;
#   2. `aws s3 cp` throughput with no tool in the path: one 3.19 GB object and 365
#      Argo files (5.77 GB) into tmpfs, at the CLI default and at three tuned settings, so arm A's
#      setting is picked from a measurement on this NIC class, not from a guess;
#   3. lith v1.18.1 (sha256-pinned) indexing, mounting and reading the same bytes, with its own
#      request counters;
#   4. a container (--user = the instance uid, as spawn runs one) reading a lith file through a
#      /tmp symlink and writing /tmp/out, which is how arm.sh presents a mount to an unchanged script.
#
# Everything lands in /tmp/out/, which the spec stages out whatever happens.
set +e
set -u -o pipefail
O=/tmp/out; mkdir -p "$O"; R="$O/canary.tsv"; : > "$R"
say(){ printf '%s\t%s\n' "$1" "${2-}" | tee -a "$R"; }
B=${ARM_BUCKET:?}
LITH_URL=https://github.com/scttfrdmn/lith/releases/download/v1.18.1/lith_linux_arm64
LITH_SHA=8b4f6218b1d549fc10b5fb6d4cdd18121121efd3f922cac323fb251529551f63
BIG="s3://$B/inputs/structural-variants-real/derived/hs37d5.fa"            # 3,189,750,467 B
MANY="s3://$B/inputs/gsw-real/argo/pacific_ocean/2023/"                    # 365 files, 5,767,045,552 B
IMG=quay.io/aarchbio/delly@sha256:c1d0492cb7d52d56f0a01e790dd1434ba9ddbb47cc5c95cf90dc12c20d5aaeb5

TOK=$(curl -sX PUT -m 3 http://169.254.169.254/latest/api/token -H 'X-aws-ec2-metadata-token-ttl-seconds: 600')
md(){ curl -s -m 3 -H "X-aws-ec2-metadata-token: $TOK" "http://169.254.169.254/latest/meta-data/$1"; }
say dollar_dash "$-"
say whoami "$(id -un) uid $(id -u)"
say instance_type "$(md instance-type)"
REGION=$(md placement/region); say region "$REGION"
say bucket_region "$(aws s3api get-bucket-location --bucket "$B" --query LocationConstraint --output text 2>&1)"
say aws_version "$(aws --version 2>&1)"
say nproc "$(nproc)"
say mem_total_kb "$(awk '/^MemTotal/{print $2}' /proc/meminfo)"
say tmp_fs "$(df -h /tmp | tail -n 1)"

( sudo dnf install -y -q docker fuse3 fuse > "$O/dnf.log" 2>&1; echo "dnf rc $?" >> "$O/dnf.log"
  sudo systemctl enable --now docker >> "$O/dnf.log" 2>&1
  for i in $(seq 1 30); do sudo docker info >/dev/null 2>&1 && break; sleep 2; done
  sudo docker pull -q "$IMG" >> "$O/dnf.log" 2>&1; echo "pull rc $?" >> "$O/dnf.log" ) &
DNF=$!

t(){ date +%s.%N; }
el(){ awk -v a="$1" -v b="$2" 'BEGIN{printf "%.2f", b-a}'; }
mbs(){ awk -v n="$1" -v s="$2" 'BEGIN{printf "%.0f", n/s/1e6}'; }
cfg(){  # cfg <name> <key=value ...> : write a private CLI config, so nothing else on the box changes
  local f="$O/cfg.$1"; shift
  printf '[default]\nregion = %s\ns3 =\n' "$REGION" > "$f"
  for kv in "$@"; do printf '  %s = %s\n' "${kv%%=*}" "${kv#*=}" >> "$f"; done
  echo "$f"
}
C_DEF=$(cfg default)
C_64=$(cfg c64 max_concurrent_requests=64 multipart_chunksize=16MB multipart_threshold=16MB)
C_128=$(cfg c128 max_concurrent_requests=128 max_queue_size=10000 multipart_chunksize=64MB multipart_threshold=64MB)
C_CRT=$(cfg crt preferred_transfer_client=crt target_bandwidth=15Gb/s multipart_chunksize=16MB)

say "== one 3.19 GB object into tmpfs =="
for c in "$C_DEF" "$C_64" "$C_128" "$C_CRT"; do
  T0=$(t); AWS_CONFIG_FILE=$c aws s3 cp --only-show-errors "$BIG" /tmp/big.fa; rc=$?; T1=$(t); rm -f /tmp/big.fa
  say "big_$(basename "$c" | sed 's/^cfg\.//')" "rc $rc  $(el "$T0" "$T1") s  $(mbs 3189750467 "$(el "$T0" "$T1")") MB/s"
done
say "== 365 Argo files into tmpfs =="
for c in "$C_DEF" "$C_64" "$C_128" "$C_CRT"; do
  rm -rf /tmp/many; T0=$(t)
  AWS_CONFIG_FILE=$c aws s3 cp --recursive --only-show-errors "$MANY" /tmp/many/; rc=$?; T1=$(t)
  say "many_$(basename "$c" | sed 's/^cfg\.//')" "rc $rc  $(el "$T0" "$T1") s  $(mbs 5767045552 "$(el "$T0" "$T1")") MB/s  files $(find /tmp/many -type f | wc -l)"
done
( cd /tmp/many && find . -type f | sort | xargs sha256sum ) > "$O/many.copy.sha256"
rm -rf /tmp/many

say "== lith =="
curl -fsSL -o /tmp/lith "$LITH_URL"
echo "$LITH_SHA  /tmp/lith" | sha256sum -c - > /dev/null 2>&1 && say lith_sha verified || { say lith_sha MISMATCH; exit 1; }
chmod +x /tmp/lith; say lith_version "$(/tmp/lith version 2>&1 | head -n 1)"
wait "$DNF"; say dnf_and_pull "$(tr '\n' ' ' < "$O/dnf.log" | tail -c 300)"
sudo sed -i 's/^#\s*user_allow_other/user_allow_other/' /etc/fuse.conf
grep -q '^user_allow_other' /etc/fuse.conf || echo user_allow_other | sudo tee -a /etc/fuse.conf > /dev/null
T0=$(t); /tmp/lith index build "s3://$B/inputs/gsw-real/argo/pacific_ocean/2023/" --index-file /tmp/many.idx > "$O/ix-many.log" 2>&1; rc=$?; T1=$(t)
say index_many "rc $rc  $(el "$T0" "$T1") s  $(stat -c%s /tmp/many.idx 2>/dev/null) B"
T0=$(t); /tmp/lith index build "s3://$B/inputs/structural-variants-real/derived/" --index-file /tmp/big.idx > "$O/ix-big.log" 2>&1; rc=$?; T1=$(t)
say index_big "rc $rc  $(el "$T0" "$T1") s"
sudo mkdir -p /mnt/many /mnt/big && sudo chown "$(id -u):$(id -g)" /mnt/many /mnt/big
/tmp/lith mount "s3://$B/inputs/gsw-real/argo/pacific_ocean/2023/" /mnt/many --index-file /tmp/many.idx \
  --allow-other --nic-gbps 15 --mem-cache 8GiB --metrics 127.0.0.1:9101 > "$O/lith-many.log" 2>&1 &
/tmp/lith mount "s3://$B/inputs/structural-variants-real/derived/" /mnt/big --index-file /tmp/big.idx \
  --allow-other --nic-gbps 15 --mem-cache 8GiB --metrics 127.0.0.1:9102 > "$O/lith-big.log" 2>&1 &
for i in $(seq 1 60); do mountpoint -q /mnt/many && mountpoint -q /mnt/big && break; sleep 1; done
say mounted "$(mountpoint -q /mnt/many && echo many) $(mountpoint -q /mnt/big && echo big)"
T0=$(t); dd if=/mnt/big/hs37d5.fa of=/dev/null bs=4M 2> "$O/dd.txt"; rc=$?; T1=$(t)
say big_lith "rc $rc  $(el "$T0" "$T1") s  $(mbs 3189750467 "$(el "$T0" "$T1")") MB/s"
T0=$(t); ( cd /mnt/many && find . -type f | sort | xargs -P 64 -n 4 sha256sum ) | sort -k2 > "$O/many.lith.sha256"; T1=$(t)
say many_lith_sha256_p64 "$(el "$T0" "$T1") s  $(mbs 5767045552 "$(el "$T0" "$T1")") MB/s"
sort -k2 "$O/many.copy.sha256" | cmp -s - "$O/many.lith.sha256" && say many_lith_vs_copy identical || say many_lith_vs_copy DIFFER
curl -s 127.0.0.1:9101/metrics > "$O/metrics-many.txt"; curl -s 127.0.0.1:9102/metrics > "$O/metrics-big.txt"
for m in many big; do
  say "requests_$m" "$(awk '/^lith_s3_requests_total/{split($0,a,"op=\""); split(a[2],b,"\""); n[b[1]]+=$NF} END{for(k in n) printf "%s=%d ", k, n[k]}' "$O/metrics-$m.txt")"
  say "s3_bytes_$m" "$(awk '/^lith_s3_bytes_total/{s+=$NF} END{printf "%.0f", s}' "$O/metrics-$m.txt")"
done

say "== a container reads a lith file through a /tmp symlink and writes /tmp/out =="
ln -sfn /mnt/big/hs37d5.fa.fai /tmp/hs37d5.fa.fai
sudo docker run --rm --user "$(id -u):$(id -g)" -v /tmp:/tmp -v /mnt/big:/mnt/big:ro "$IMG" \
  bash -c 'cd /tmp && sha256sum hs37d5.fa.fai > /tmp/out/container.sha256 && id -u > /tmp/out/container.uid && echo ok' > "$O/container.log" 2>&1
say container_rc "$? $(cat "$O/container.log")"
say container_fai_sha256 "$(cut -c1-64 "$O/container.sha256" 2>/dev/null)  (pin 1eab7540d4b6...)"
fusermount3 -u /mnt/many; fusermount3 -u /mnt/big
say DONE yes
