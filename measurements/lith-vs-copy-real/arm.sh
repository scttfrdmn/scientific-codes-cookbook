#!/usr/bin/env bash
# lith-vs-copy-real: one arm of one workload, on the HOST of a spawn host-path task. mkspec.py
# writes the spec; spawn has already staged the original measurement's small inputs (its scripts
# and pins) exactly where its own spec put them. This script supplies the big inputs, then runs the
# original spec's container and command unchanged:
#
#   ARM_MODE=copy  aws s3 cp at tuned concurrency (ARM_S3CFG) into the same /tmp paths, i.e. the
#                  half-RAM tmpfs, exactly as spawn's stage-in does at the CLI default;
#   ARM_MODE=lith  lith v1.18.1 (sha256-pinned) mounts ARM_ROOT read-only and each input path
#                  becomes a symlink into the mount, so nothing is staged and the original script's
#                  own sha256 check reads every byte through lith and matches it against its pins.
#
# Evidence goes to /tmp/arm/ (staged out whatever happens): arm.tsv (phase times, peaks, request
# counts), the container's own log, lith's log and its Prometheus counters.
set +e
set -u -o pipefail
A=/tmp/arm; mkdir -p "$A"; T="$A/arm.tsv"; : > "$T"
say(){ printf '%s\t%s\n' "$1" "${2-}" | tee -a "$T"; }
t(){ date +%s.%N; }
el(){ awk -v a="$1" -v b="$2" 'BEGIN{printf "%.1f", b-a}'; }
: "${ARM_MODE:?}" "${ARM_ID:?}" "${ARM_BUCKET:?}" "${ARM_ORIG:?}" "${ARM_ORIG_SHA:?}" "${ARM_DATA:?}" "${ARM_ROOT:?}"
LITH_URL=https://github.com/scttfrdmn/lith/releases/download/v1.18.1/lith_linux_arm64
LITH_SHA=8b4f6218b1d549fc10b5fb6d4cdd18121121efd3f922cac323fb251529551f63
BASE="s3://$ARM_BUCKET/inputs/structural-variants-real/derived/hs37d5.fa"     # 3,189,750,467 B, the no-tool baseline
TSTART=$(t)
say arm "$ARM_ID"
say mode "$ARM_MODE"
say dollar_dash "$-"

echo "$ARM_ORIG_SHA  $ARM_ORIG" | sha256sum -c - > /dev/null 2>&1 || { say FAIL "the original spec does not match its pin"; exit 1; }
IMG=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["container"])' "$ARM_ORIG")
python3 -c 'import json,sys; c=json.load(open(sys.argv[1]))["command"]; assert c[:2]==["bash","-c"]; open(sys.argv[2],"w").write(c[2])' \
  "$ARM_ORIG" "$A/command.sh" || { say FAIL "cannot read the original command"; exit 1; }
say image "$IMG"
say command_sha256 "$(sha256sum < "$A/command.sh" | cut -c1-64)"

TOK=$(curl -sX PUT -m 3 http://169.254.169.254/latest/api/token -H 'X-aws-ec2-metadata-token-ttl-seconds: 21600')
md(){ curl -s -m 3 -H "X-aws-ec2-metadata-token: $TOK" "http://169.254.169.254/latest/meta-data/$1"; }
REGION=$(md placement/region)
BREG=$(aws s3api get-bucket-location --bucket "$ARM_BUCKET" --query LocationConstraint --output text 2>&1)
say instance_type "$(md instance-type)"
say instance_id "$(md instance-id)"
say region "$REGION"
say bucket_region "$BREG"
[ "$REGION" = "$BREG" ] || { say FAIL "instance in $REGION, bucket in $BREG: a cross-region read voids every number"; exit 1; }
say nproc "$(nproc)"
say mem_total_kb "$(awk '/^MemTotal/{print $2}' /proc/meminfo)"
say tmp_size "$(df -B1 --output=size /tmp | tail -n 1 | tr -d ' ')"
say aws_version "$(aws --version 2>&1)"

# peaks: host memory in use (MemTotal - MemAvailable) and /tmp used, sampled each second
( mx=0; tx=0
  while :; do
    m=$(awk '/^MemTotal/{a=$2} /^MemAvailable/{b=$2} END{print a-b}' /proc/meminfo)
    u=$(df -B1 --output=used /tmp | tail -n 1 | tr -d ' ')
    [ "$m" -gt "$mx" ] && { mx=$m; echo "$mx" > "$A/.mem_peak_kb"; }
    [ "$u" -gt "$tx" ] && { tx=$u; echo "$tx" > "$A/.tmp_peak_b"; }
    sleep 1
  done ) &
SAMP=$!

# Docker (and FUSE) install and the image pull overlap whatever comes next, in both arms
( sudo dnf install -y -q docker fuse3 fuse > "$A/dnf.log" 2>&1; echo "dnf_rc $?" >> "$A/dnf.log"; touch "$A/.dnf"
  sudo systemctl enable --now docker >> "$A/dnf.log" 2>&1
  for i in $(seq 1 60); do sudo docker info > /dev/null 2>&1 && break; sleep 2; done
  T0=$(t); sudo docker pull -q "$IMG" >> "$A/dnf.log" 2>&1; echo "pull_rc $? pull_s $(el "$T0" "$(t)")" >> "$A/dnf.log"
  touch "$A/.docker" ) &
DOCK=$!

cfg(){  # a private CLI config file, so nothing else on the box (spawn's stage-out) changes
  local f="$A/cfg.$1"; shift
  printf '[default]\nregion = %s\n' "$REGION" > "$f"
  [ $# -gt 0 ] && printf 's3 =\n' >> "$f"   # an empty `s3 =` section makes the CLI exit 255
  for kv in "$@"; do printf '  %s = %s\n' "${kv%%=*}" "${kv#*=}" >> "$f"; done
  echo "$f"
}
# shellcheck disable=SC2086
TUNED=$(cfg tuned ${ARM_S3CFG})
DEFAULT=$(cfg default)

# the data as S3 lists it: object count and bytes, for the request arithmetic
aws s3 ls --recursive "$ARM_ROOT" > "$A/data.ls" 2>&1
say data_objects "$(wc -l < "$A/data.ls" | tr -d ' ')"
say data_bytes "$(awk '{s+=$3} END{printf "%.0f", s}' "$A/data.ls")"

# no-tool baseline on this box: one 3.19 GB object into tmpfs, CLI default, then tuned
for c in "$DEFAULT" "$TUNED"; do
  T0=$(t); AWS_CONFIG_FILE=$c aws s3 cp --only-show-errors "$BASE" /tmp/.baseline; rc=$?; T1=$(t); rm -f /tmp/.baseline
  say "baseline_$(basename "$c" | sed 's/^cfg\.//')" "rc $rc $(el "$T0" "$T1") s $(awk -v s="$(el "$T0" "$T1")" 'BEGIN{printf "%.0f", 3189750467/s/1e6}') MB/s"
done
TB=$(t); say baseline_s "$(el "$TSTART" "$TB")"

MOUNTS=""
if [ "$ARM_MODE" = copy ]; then
  T0=$(t); RC=0
  for p in $ARM_DATA; do
    src=${p%%|*}; dst=${p#*|}
    if [ "${src%/}" != "$src" ]; then
      AWS_CONFIG_FILE=$TUNED aws s3 cp --recursive --only-show-errors "$src" "$dst" || RC=$?
    else
      AWS_CONFIG_FILE=$TUNED aws s3 cp --only-show-errors "$src" "$dst" || RC=$?
    fi
    say "copied $(basename "${dst%/}")" "rc $RC at $(el "$T0" "$(t)") s"
  done
  T1=$(t)
  say stage_s "$(el "$T0" "$T1")"
  say stage_rc "$RC"
  say stage_MBps "$(awk -v s="$(el "$T0" "$T1")" -v n="$(awk '{s+=$3} END{printf "%.0f", s}' "$A/data.ls")" 'BEGIN{printf "%.0f", n/s/1e6}')"
  [ "$RC" = 0 ] || { say FAIL "copy"; exit 1; }
else
  T0=$(t)
  curl -fsSL -o /tmp/lith "$LITH_URL"
  echo "$LITH_SHA  /tmp/lith" | sha256sum -c - > /dev/null 2>&1 || { say FAIL "lith binary does not match its pin"; exit 1; }
  chmod +x /tmp/lith
  say lith_version "$(/tmp/lith version 2>&1 | head -n 1)"
  /tmp/lith index build "$ARM_ROOT" --index-file /tmp/arm.lithidx > "$A/lith-index.log" 2>&1 || { say FAIL "lith index build"; exit 1; }
  say lith_index_s "$(el "$T0" "$(t)")"
  say lith_index_bytes "$(stat -c%s /tmp/arm.lithidx)"
  while [ ! -e "$A/.dnf" ]; do sleep 1; done
  sudo sed -i 's/^#\s*user_allow_other/user_allow_other/' /etc/fuse.conf
  grep -q '^user_allow_other' /etc/fuse.conf || echo user_allow_other | sudo tee -a /etc/fuse.conf > /dev/null
  sudo mkdir -p /mnt/lith && sudo chown "$(id -u):$(id -g)" /mnt/lith
  /tmp/lith mount "$ARM_ROOT" /mnt/lith --index-file /tmp/arm.lithidx --allow-other \
    --nic-gbps "${ARM_NIC:?}" --mem-cache "${ARM_MEMCACHE:?}" --metrics 127.0.0.1:9101 > "$A/lith.log" 2>&1 &
  LPID=$!
  for i in $(seq 1 120); do mountpoint -q /mnt/lith && break; sleep 1; done
  mountpoint -q /mnt/lith || { say FAIL "lith did not mount"; tail -n 20 "$A/lith.log"; exit 1; }
  for p in $ARM_DATA; do
    src=${p%%|*}; dst=${p#*|}; rel=${src#"$ARM_ROOT"}
    ln -sfn "/mnt/lith/${rel%/}" "${dst%/}"
  done
  ls -l /tmp > "$A/tmp-links.txt" 2>&1
  T1=$(t)
  say stage_s "$(el "$T0" "$T1")   (no copy: binary fetch + index + mount)"
  MOUNTS="-v /mnt/lith:/mnt/lith:ro"
fi

while [ ! -e "$A/.docker" ]; do sleep 1; done
TD=$(t)
say docker_wait_s "$(el "$T1" "$TD")"
cat "$A/dnf.log" | grep -E 'rc|pull_s' | tr '\n' ' ' | sed 's/^/docker\t/' | tee -a "$T"; echo | tee -a "$T"

# the original measurement's own container and command, unchanged (spawn runs it the same way:
# docker run --rm --user <instance uid> with the staged dirs bind-mounted; here /tmp)
# Progress into command.log every 30 s: a lifecycle kill flushes command.log (spawn#643) but does
# not stage /tmp/arm out, so this is the only evidence such a run leaves (gsw-lith-b1 left none).
if [ "$ARM_MODE" = lith ]; then
  ( while :; do sleep 30
      printf 'progress\t%s s  s3_bytes %s  gets %s  last: %s\n' "$(el "$TD" "$(t)")" \
        "$(curl -s 127.0.0.1:9101/metrics | awk '/^lith_s3_bytes_total/{s+=$NF} END{printf "%.0f", s}')" \
        "$(curl -s 127.0.0.1:9101/metrics | awk '/^lith_s3_requests_total/{s+=$NF} END{printf "%.0f", s}')" \
        "$(tail -n 1 "$A/container.log" 2>/dev/null | cut -c1-160)"
    done ) &
  PROG=$!
fi
# shellcheck disable=SC2086
sudo docker run --rm --user "$(id -u):$(id -g)" -v /tmp:/tmp $MOUNTS "$IMG" bash -c "$(cat "$A/command.sh")" 2>&1 \
  | tee "$A/container.log"
RC=${PIPESTATUS[0]}
[ -n "${PROG:-}" ] && kill "$PROG" 2>/dev/null
TE=$(t)
say command_rc "$RC"
say command_s "$(el "$TD" "$TE")"

if [ "$ARM_MODE" = lith ]; then
  curl -s 127.0.0.1:9101/metrics > "$A/lith-metrics.txt"
  say lith_requests "$(awk '/^lith_s3_requests_total/{split($0,a,"op=\""); split(a[2],b,"\""); n[b[1]]+=$NF} END{for(k in n) printf "%s=%d ", k, n[k]}' "$A/lith-metrics.txt")"
  say lith_s3_bytes "$(awk '/^lith_s3_bytes_total/{s+=$NF} END{printf "%.0f", s}' "$A/lith-metrics.txt")"
  say lith_vmhwm_kb "$(awk '/^VmHWM/{print $2}' /proc/$LPID/status 2>/dev/null)"
  fusermount3 -u /mnt/lith 2>/dev/null || fusermount -u /mnt/lith 2>/dev/null
fi
kill "$SAMP" 2>/dev/null
say host_mem_peak_kb "$(cat "$A/.mem_peak_kb" 2>/dev/null)"
say tmp_peak_bytes "$(cat "$A/.tmp_peak_b" 2>/dev/null)"
say arm_s "$(el "$TSTART" "$(t)")"
exit "$RC"
