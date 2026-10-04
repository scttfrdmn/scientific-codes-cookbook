#!/usr/bin/env bash
# Why is a 1.1 TiB mount serving kraken2 at ~1.5 MiB/s when the same tool serves STAR's
# index at 445-741 MB/s?
#
# The canary measured the SYMPTOM (reads_done stuck at 0 for 28 min while S3 bytes trickled
# in at ~1.56 MiB/s). That rate is close enough to "one synchronous 128 KiB FUSE read per S3
# round trip" (128 KiB / ~80 ms = 1.6 MiB/s) to be worth testing directly, because the fix
# differs completely depending on which of three things is true:
#
#   H1  the mount is RTT-bound for everyone -- sequential reads get no readahead
#   H2  the mount is fine on the host and slow INSIDE A CONTAINER -- a bind-mounted FUSE
#       mount losing kernel readahead would break EVERY mount-based recipe, since recipes
#       always run in containers
#   H3  it is specific to the file kraken2 touches first, or to random access
#
# So: read the same bytes four ways on one box and print four numbers. This is cheap
# (minutes, no kraken2) and it discriminates. Deliberately NOT a fix attempt -- the point is
# to find out which thing to report.
set -uo pipefail
B="${COOKBOOK_BUCKET:?}"
W=/tmp/w; mkdir -p "$W"
R="$W/result.txt"; : > "$R"
say(){ printf '%s\t%s\n' "$1" "${2-}" | tee -a "$R"; }
push(){ aws s3 cp "$R" "s3://$B/measurements/lith-kraken2-roda/readpath.txt" --only-show-errors 2>/dev/null || true; }
trap 'say exit_line "$LINENO"; push' EXIT

DB=s3://kraken2-ncbi-refseq-complete-v205/Kraken2_RefSeqCompleteV205
K2=quay.io/aarchbio/kraken2@sha256:fc6dd9becb7fec054ee01575d85e12c7fc509e53001c0ade5ed2e095c3cbe455
LITH_URL=https://github.com/scttfrdmn/lith/releases/download/v1.6.0/lith_linux_arm64
LITH_SHA=fa361748a2bd611594f0f2801e50157cb80e0135da6936b82733287b30ba7259

say nproc "$(nproc --all)"
say mem_gib "$(awk '/MemTotal/{printf "%.0f", $2/1048576}' /proc/meminfo)"; push

sudo dnf install -y -q docker fuse fuse3 >/dev/null 2>&1
sudo systemctl enable --now docker >/dev/null 2>&1
sudo sed -i 's/^#\s*user_allow_other/user_allow_other/' /etc/fuse.conf
curl -fsSL -o "$W/lith" "$LITH_URL"
echo "$LITH_SHA  $W/lith" | sha256sum -c - >/dev/null 2>&1 || { say lith_sha MISMATCH; exit 1; }
chmod +x "$W/lith"; LITH="$W/lith"
$LITH index build "$DB" --index-file "$W/db.lithidx" --no-sign-request >/dev/null 2>&1
sudo mkdir -p /mnt/k2db && sudo chown "$(whoami)" /mnt/k2db
$LITH mount "$DB" /mnt/k2db --index-file "$W/db.lithidx" --no-sign-request --allow-other --daemon >/dev/null 2>&1
for i in $(seq 1 60); do mountpoint -q /mnt/k2db && break; sleep 2; done
say mount_ok "$(mountpoint -q /mnt/k2db && echo yes || echo NO)"
say files "$(ls /mnt/k2db | tr '\n' ' ')"
for f in /mnt/k2db/*; do say "  size $(basename "$f")" "$(stat -c%s "$f")"; done
push

# 256 MiB is enough to be bandwidth-dominated if bandwidth is what binds, and small enough
# that an RTT-bound path still finishes (256 MiB at 1.6 MiB/s = 2.7 min).
N=256
# Let dd report its own bytes and rate rather than assuming it read all of count=$N --
# taxo.k2d may be smaller than 256 MiB, and dividing by an assumed size would invent a
# number. dd's last stderr line is "<bytes> bytes (...) copied, <s> s, <rate>".
ddrate(){ # ddrate <label> <path-or-"container">
  local out
  if [ "$2" = "container" ]; then
    out=$(sudo docker run --rm -v /mnt/k2db:/db:ro "$K2" \
            dd if=/db/taxo.k2d of=/dev/null bs=1M count=$N iflag=fullblock 2>&1 | tail -1)
  else
    out=$(dd if="$2" of=/dev/null bs=1M count=$N iflag=fullblock 2>&1 | tail -1)
  fi
  say "  $1" "$(echo "$out" | sed 's/^[0-9]* bytes (\([^)]*\)) copied, /\1 in /')"
}

say "== H1/H3: sequential read of taxo.k2d, ON THE HOST =="
ddrate host_taxo_seq /mnt/k2db/taxo.k2d; push

say "== H1/H3: sequential read of hash.k2d (the 1.1 TiB file), ON THE HOST =="
ddrate host_hash_seq /mnt/k2db/hash.k2d; push

say "== H3: RANDOM 4 KiB reads into hash.k2d, on the host (kraken2's actual pattern) =="
T=$(date +%s)
python3 -u - <<'PY' 2>&1 | tail -1 | sed 's/^/  random_4k\t/' | tee -a "$R"
import os, random
fd = os.open("/mnt/k2db/hash.k2d", os.O_RDONLY)
sz = os.fstat(fd).st_size
random.seed(1); n = 200
import time; t0 = time.time()
for _ in range(n):
    os.pread(fd, 4096, random.randrange(0, sz - 4096))
dt = time.time() - t0
print("%d probes in %.1fs = %.1f probes/s (%.0f ms each)" % (n, dt, n/dt, 1000*dt/n))
PY
push

say "== H2: the SAME sequential read, INSIDE the container (bind-mounted FUSE) =="
sudo docker pull -q "$K2" >/dev/null 2>&1    # pull first, so the pull is not in the timing
ddrate container_taxo_seq container; push

say "== baseline: same bytes straight from S3, no mount, same box =="
T=$(date +%s)
aws s3 cp "$DB/taxo.k2d" "$W/taxo.bin" --no-sign-request --only-show-errors 2>/dev/null
S=$(( $(date +%s) - T ))
say "  s3_cp_full_taxo" "$(awk -v s="$S" -v m="$(stat -c%s "$W/taxo.bin" 2>/dev/null || echo 0)" \
  'BEGIN{m=m/1048576; if(s>0) printf "%.1f MiB/s (%.0f MiB in %ss)", m/s, m, s; else print "inst"}')"
rm -f "$W/taxo.bin"
say DONE yes; push
