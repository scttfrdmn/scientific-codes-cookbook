#!/usr/bin/env bash
# The fifth data path: copy the index into tmpfs (/tmp, RAM-backed) instead of onto EBS.
# This is the pro move, not a mistake -- RAM is the fastest thing on the box and staging into
# it is faster than staging onto a disk. It has one precondition, which is the whole point:
# the copy occupies RAM the tool also needs, so the box has to be big enough for BOTH. On a
# 62 GiB box this exact copy OOM-killed STAR; here there are 124 GiB, so it should fit.
set -uo pipefail
B="${COOKBOOK_BUCKET:?set COOKBOOK_BUCKET (make print-bucket)}"
IDX_S3="s3://$B/inputs/star-index-GRCh38-116"
W=/root/dp; cd "$W"
R=$W/tmpfs-leg.txt; : > "$R"
say(){ printf '%s\n' "$*" | tee -a "$R"; }
push(){ aws s3 cp "$R" "s3://$B/measurements/star-real/tmpfs-leg.txt" --only-show-errors 2>/dev/null || true; }
STAR_IMG=quay.io/aarchbio/star@sha256:90331f64bd73eadaefbebc8d4aecfed3ebed1a7e40b761041acbbcf53b3e13ae
OUT=$W/out

say "== ROUTE A2: copy the index into tmpfs (RAM) =="
say "mem_total_gib	$(awk '/MemTotal/{printf "%.0f", $2/1048576}' /proc/meminfo)"
say "tmpfs_size_gib	$(df -BG --output=size /tmp | tail -1 | tr -dc 0-9)"
push
T=/tmp/idx; mkdir -p "$T"
sync; echo 3 > /proc/sys/vm/drop_caches 2>/dev/null
C0=$(date +%s)
aws s3 cp "$IDX_S3/" "$T/" --recursive --only-show-errors
crc=$?; C1=$(date +%s)
say "tmpfs_copy_rc	$crc"
say "tmpfs_copy_s	$(( C1 - C0 ))"
say "tmpfs_copy_bytes	$(du -sb "$T" | cut -f1)"
say "tmpfs_copy_mib_s	$(awk -v b="$(du -sb "$T" | cut -f1)" -v s="$(( C1 - C0 ))" 'BEGIN{if(s>0)printf "%.0f", b/1048576/s}')"
say "tmpfs_used	$(df -h /tmp | awk 'NR==2{print $3" of "$2}')"
say "mem_available_gib	$(free -g | awk 'NR==2{print $7}')"
push
# NOTE: no drop_caches here -- dropping caches cannot evict tmpfs (it IS the page cache),
# so there is no cold/warm distinction for this route. That is the property being measured.
rm -f "$OUT"/e1_* 2>/dev/null
A0=$(date +%s)
docker run --rm -v "$T":/idx:ro -v "$OUT":/w -w /w "$STAR_IMG" bash -lc \
  "export PATH=/opt/conda/bin:\$PATH; STAR --runThreadN $(nproc) --genomeDir /idx \
     --readFilesIn ERR188026_1.fastq.gz ERR188026_2.fastq.gz --readFilesCommand zcat \
     --outSAMtype BAM Unsorted --outFileNamePrefix e1_ > e1_star.log 2>&1"
arc=$?; A1=$(date +%s)
say "tmpfs_align_rc	$arc"
say "tmpfs_align_s	$(( A1 - A0 ))"
grep -E 'loading genome|started mapping|finished mapping|finished successfully' "$OUT/e1_star.log" 2>/dev/null | sed 's/^/tmpfs_phase	/' | tee -a "$R"
say "tmpfs_input_reads	$(awk -F'\t' '/input reads/{gsub(/[^0-9]/,"",$2); print $2}' "$OUT/e1_Log.final.out" 2>/dev/null)"
say "tmpfs_pct_unique	$(awk -F'\t' '/Uniquely mapped reads %/{gsub(/[^0-9.]/,"",$2); print $2}' "$OUT/e1_Log.final.out" 2>/dev/null)"
say "tmpfs_bam_bytes	$(stat -c%s "$OUT/e1_Aligned.out.bam" 2>/dev/null || echo 0)"
say "peak_mem_used_gib	$(free -g | awk 'NR==2{print $3}')"
say "oom_events	$(dmesg 2>/dev/null | grep -ci 'oom-kill')"
say TMPFS_DONE; push
