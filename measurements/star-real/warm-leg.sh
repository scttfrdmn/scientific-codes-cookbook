#!/usr/bin/env bash
# A true-warm pass. four-data-paths.sh drops the page cache before EVERY align, so its
# "warm" rows are really second cold reads -- which is the right default (a cohort fans out
# onto fresh boxes, and every one of them is cold) but it never measures the re-read.
#
# This run does the opposite: no drop_caches, back-to-back on a box whose 124 GiB of RAM can
# hold the whole 28.6 GiB index in page cache. That is the best case for every copy route, so
# it is the fairest test of "but it's fast once it's local."
set -uo pipefail
B=cookbook-942542972736-us-west-2
W=/root/dp; cd "$W"
R=$W/warm-leg.txt; : > "$R"
say(){ printf '%s\n' "$*" | tee -a "$R"; }
push(){ aws s3 cp "$R" "s3://$B/measurements/star-real/warm-leg.txt" --only-show-errors 2>/dev/null || true; }
STAR_IMG=quay.io/aarchbio/star@sha256:90331f64bd73eadaefbebc8d4aecfed3ebed1a7e40b761041acbbcf53b3e13ae
OUT=$W/out

# align_nodrop TAG GENOME_DIR PREFIX -- identical to four-data-paths.sh's align() minus the
# drop_caches, so the two sets of numbers differ in exactly one thing.
align_nodrop(){
  local tag=$1 gdir=$2 pfx=$3 t0 t1
  rm -f "$OUT"/${pfx}* 2>/dev/null
  t0=$(date +%s)
  docker run --rm -v "$gdir":/idx:ro -v "$OUT":/w -w /w "$STAR_IMG" bash -lc \
    "export PATH=/opt/conda/bin:\$PATH; STAR --runThreadN $(nproc) --genomeDir /idx \
       --readFilesIn ERR188026_1.fastq.gz ERR188026_2.fastq.gz --readFilesCommand zcat \
       --outSAMtype BAM Unsorted --outFileNamePrefix ${pfx} > ${pfx}star.log 2>&1"
  local rc=$?; t1=$(date +%s)
  say "${tag}_rc	$rc"
  say "${tag}_align_s	$(( t1 - t0 ))"
  grep -E 'loading genome|started mapping|finished mapping|finished successfully' "$OUT/${pfx}star.log" 2>/dev/null | sed "s/^/${tag}_phase	/" | tee -a "$R"
  say "${tag}_input_reads	$(awk -F'\t' '/input reads/{gsub(/[^0-9]/,"",$2); print $2}' "$OUT/${pfx}Log.final.out" 2>/dev/null)"
  say "${tag}_pct_unique	$(awk -F'\t' '/Uniquely mapped reads %/{gsub(/[^0-9.]/,"",$2); print $2}' "$OUT/${pfx}Log.final.out" 2>/dev/null)"
  push
}

say "== TRUE WARM: no drop_caches, 124 GiB of RAM to cache a 28.6 GiB index in =="
say "mem_total_gib	$(awk '/MemTotal/{printf "%.0f", $2/1048576}' /proc/meminfo)"
push

# EBS local copy -- the route this pass exists to be fair to
[ -d /data/idx ] && { align_nodrop ebs_warm1 /data/idx w1_; align_nodrop ebs_warm2 /data/idx w2_; }
say "cache_after_ebs	$(free -g | awk 'NR==2{print $6" GiB buff/cache"}')"; push

# EFS
[ -d /efs/staridx ] && align_nodrop efs_truewarm /efs/staridx w3_
# FSx -- by now the bytes have been faulted in from S3 by the cold pass
FSXD=$(dirname "$(find /fsx -maxdepth 4 -name 'SAindex' 2>/dev/null | head -1)")
[ -n "$FSXD" ] && [ "$FSXD" != "." ] && align_nodrop fsx_truewarm "$FSXD" w4_
# lith, for the same treatment
[ -d /mnt/lithidx ] && align_nodrop lith_truewarm /mnt/lithidx w5_

say WARM_DONE; push
