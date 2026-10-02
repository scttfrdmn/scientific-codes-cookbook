#!/usr/bin/env bash
# Four ways to put one 28.6 GiB STAR index in front of an alignment, on ONE box so the
# only variable is the data path: local copy, EFS, FSx for Lustre (S3-hydrated), lith mount.
#
# The workload is fixed and real: the published GRCh38 + Ensembl 116 index, and the complete
# ERR188026 run (15.8M reads). Every route must return input_reads 15800127 and 92.47% unique,
# or it did not do the same work.
#
# Each route is timed twice where a cache can exist (cold, then warm), because "FSx is fast"
# and "EFS is fast" are claims about the second read, and the first read is the one you pay
# for when a cohort fans out.
set -uo pipefail
B="${COOKBOOK_BUCKET:?set COOKBOOK_BUCKET (make print-bucket)}"
IDX_S3="s3://$B/inputs/star-index-GRCh38-116"
W=/root/dp; mkdir -p "$W"; cd "$W"
R=$W/four-data-paths.txt; : > "$R"
say(){ printf '%s\n' "$*" | tee -a "$R"; }
push(){ aws s3 cp "$R" "s3://$B/measurements/star-real/four-data-paths.txt" --only-show-errors 2>/dev/null || true; }
STAR_IMG=quay.io/aarchbio/star@sha256:90331f64bd73eadaefbebc8d4aecfed3ebed1a7e40b761041acbbcf53b3e13ae
LITHSHA=ca145a1cc40823fa3b5304351f91e7397bde2f28c12052e035db6af509d9b706

# The container runs as mambauser (57439); a host dir it must write needs to be world-writable.
OUT=$W/out; mkdir -p "$OUT"; chmod 777 "$OUT"

say "instance_type	$(curl -s -m 3 http://169.254.169.254/latest/meta-data/instance-type 2>/dev/null || echo '?')"
say "nproc	$(nproc)"
say "mem_total_gib	$(awk '/MemTotal/{printf "%.0f", $2/1048576}' /proc/meminfo)"
say "index_bytes_on_s3	$(aws s3 ls "$IDX_S3/" --recursive --summarize 2>/dev/null | awk '/Total Size/{print $3}')"
push

# ---------- setup ----------
dnf install -y -q docker fuse fuse3 amazon-efs-utils lustre-client >/dev/null 2>&1
systemctl enable --now docker >/dev/null 2>&1
sed -i 's/^#\s*user_allow_other/user_allow_other/' /etc/fuse.conf
curl -fsSL -o "$W/lith" https://github.com/scttfrdmn/lith/releases/download/v1.1.3/lith_linux_arm64
echo "$LITHSHA  $W/lith" | sha256sum -c - >/dev/null 2>&1 && say "lith_sha	verified" || say "lith_sha	MISMATCH"
chmod +x "$W/lith"; LITH=$W/lith
docker pull -q "$STAR_IMG" >/dev/null 2>&1 && say "star_image	pulled" || say "star_image	PULL-FAILED"
# the per-sample reads are small (2 GB) and copied once; every route shares them
for r in 1 2; do aws s3 cp "s3://$B/inputs/salmon-real/ERR188026_${r}.fastq.gz" "$OUT/" --only-show-errors; done
say "reads_local	$(du -sb "$OUT" | cut -f1)"
push

# align() ROUTE_TAG GENOME_DIR PREFIX -- runs STAR with the genome at GENOME_DIR
align(){
  local tag=$1 gdir=$2 pfx=$3 t0 t1
  rm -f "$OUT"/${pfx}* 2>/dev/null
  sync; echo 3 > /proc/sys/vm/drop_caches 2>/dev/null   # make "cold" mean cold
  t0=$(date +%s)
  docker run --rm -v "$gdir":/idx:ro -v "$OUT":/w -w /w "$STAR_IMG" bash -lc \
    "export PATH=/opt/conda/bin:\$PATH; STAR --runThreadN $(nproc) --genomeDir /idx \
       --readFilesIn ERR188026_1.fastq.gz ERR188026_2.fastq.gz --readFilesCommand zcat \
       --outSAMtype BAM Unsorted --outFileNamePrefix ${pfx} > ${pfx}star.log 2>&1"
  local rc=$?; t1=$(date +%s)
  say "${tag}_align_rc	$rc"
  say "${tag}_align_s	$(( t1 - t0 ))"
  # STAR logs its own genome-load and mapping boundaries -- the load is the data path's share
  say "${tag}_load_s	$(awk '/loading genome/{a=$2" "$3} /started mapping/{b=$2" "$3} END{if(a&&b){print "see_log"}}' "$OUT/${pfx}star.log" 2>/dev/null)"
  grep -E 'loading genome|started mapping|finished mapping|finished successfully' "$OUT/${pfx}star.log" 2>/dev/null | sed "s/^/${tag}_phase	/" | tee -a "$R"
  say "${tag}_input_reads	$(awk -F'\t' '/input reads/{gsub(/[^0-9]/,"",$2); print $2}' "$OUT/${pfx}Log.final.out" 2>/dev/null)"
  say "${tag}_pct_unique	$(awk -F'\t' '/Uniquely mapped reads %/{gsub(/[^0-9.]/,"",$2); print $2}' "$OUT/${pfx}Log.final.out" 2>/dev/null)"
  say "${tag}_bam_bytes	$(stat -c%s "$OUT/${pfx}Aligned.out.bam" 2>/dev/null || echo 0)"
  push
}

# ---------- ROUTE D: lith mount over S3 in place (first: it is the cheapest to set up) ----------
say "== ROUTE D: lith FUSE mount, index read in place from S3 =="
D0=$(date +%s)
$LITH index build "$IDX_S3" --index-file "$W/idx.lithidx" > "$W/ix.log" 2>&1
D1=$(date +%s)
say "lith_index_s	$(( D1 - D0 ))"
say "lith_index_bytes	$(stat -c%s "$W/idx.lithidx" 2>/dev/null || echo 0)"
mkdir -p /mnt/lithidx
M0=$(date +%s)
$LITH mount "$IDX_S3" /mnt/lithidx --index-file "$W/idx.lithidx" --allow-other --daemon > "$W/mt.log" 2>&1
for i in $(seq 1 60); do mountpoint -q /mnt/lithidx && break; sleep 2; done
M1=$(date +%s)
say "lith_mount_s	$(( M1 - M0 ))"
say "lith_setup_bytes_moved	$(stat -c%s "$W/idx.lithidx" 2>/dev/null || echo 0)"
say "lith_files_visible	$(ls /mnt/lithidx 2>/dev/null | wc -l)"
push
align lith_cold /mnt/lithidx d1_
align lith_warm /mnt/lithidx d2_

# ---------- ROUTE A: aws s3 cp to local EBS ----------
say "== ROUTE A: aws s3 cp the whole index to local disk =="
A_D=/data/idx; mkdir -p "$A_D"
say "local_disk_avail_gib	$(df -BG --output=avail /data 2>/dev/null | tail -1 | tr -dc 0-9)"
A0=$(date +%s)
aws s3 cp "$IDX_S3/" "$A_D/" --recursive --only-show-errors
arc=$?; A1=$(date +%s)
say "copy_rc	$arc"
say "copy_s	$(( A1 - A0 ))"
say "copy_bytes_moved	$(du -sb "$A_D" | cut -f1)"
say "copy_mib_s	$(awk -v b="$(du -sb "$A_D" | cut -f1)" -v s="$(( A1 - A0 ))" 'BEGIN{if(s>0)printf "%.0f", b/1048576/s}')"
push
align copy_cold "$A_D" a1_
align copy_warm "$A_D" a2_

# ---------- ROUTE B: EFS ----------
say "== ROUTE B: EFS -- hydrate from S3 once, then read over NFS =="
if mountpoint -q /efs; then
  say "efs_mounted	yes	$(df -h /efs | awk 'NR==2{print $1}')"
  B_D=/efs/staridx; mkdir -p "$B_D"
  B0=$(date +%s)
  aws s3 cp "$IDX_S3/" "$B_D/" --recursive --only-show-errors
  brc=$?; B1=$(date +%s)
  say "efs_hydrate_rc	$brc"
  say "efs_hydrate_s	$(( B1 - B0 ))"
  say "efs_hydrate_bytes	$(du -sb "$B_D" | cut -f1)"
  say "efs_hydrate_mib_s	$(awk -v b="$(du -sb "$B_D" | cut -f1)" -v s="$(( B1 - B0 ))" 'BEGIN{if(s>0)printf "%.0f", b/1048576/s}')"
  push
  align efs_cold "$B_D" b1_
  align efs_warm "$B_D" b2_
else
  say "efs_mounted	NO -- route B skipped"
fi
push

# ---------- ROUTE C: FSx for Lustre, hydrated from S3 ----------
say "== ROUTE C: FSx for Lustre linked to S3 -- metadata imported, bytes lazy-load on first read =="
if mountpoint -q /fsx; then
  say "fsx_mounted	yes	$(df -h /fsx | awk 'NR==2{print $1"  "$2}')"
  say "fsx_tree	$(find /fsx -maxdepth 3 -type f 2>/dev/null | head -3 | tr '\n' ' ')"
  C_D=$(dirname "$(find /fsx -maxdepth 4 -name 'SAindex' 2>/dev/null | head -1)")
  say "fsx_index_dir	$C_D"
  if [ -n "$C_D" ] && [ "$C_D" != "." ]; then
    say "fsx_files_visible	$(ls "$C_D" 2>/dev/null | wc -l)"
    # metadata is there but bytes are not: stat is instant, first read faults from S3
    S0=$(date +%s); ls -l "$C_D" >/dev/null 2>&1; S1=$(date +%s)
    say "fsx_stat_all_s	$(( S1 - S0 ))"
    align fsx_cold "$C_D" c1_
    align fsx_warm "$C_D" c2_
  else
    say "fsx_index_dir	NOT-FOUND -- route C align skipped"
    ls -R /fsx 2>/dev/null | head -20 | tee -a "$R"
  fi
else
  say "fsx_mounted	NO -- route C skipped"
fi
push

say "== leftovers each route wants cleaned up =="
say "local_copy_gib	$(du -sBG "$A_D" 2>/dev/null | cut -f1)"
df -h /data /efs /fsx /mnt/lithidx 2>/dev/null | tee -a "$R"
say FOUR_PATHS_DONE; push
for f in "$OUT"/*Log.final.out; do
  aws s3 cp "$f" "s3://$B/measurements/star-real/logs/$(basename "$f")" --only-show-errors 2>/dev/null || true
done
