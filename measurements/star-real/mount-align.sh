#!/usr/bin/env bash
# STAR align against a MOUNTED published index. The build needed 71.63 GiB and 1047 s;
# this asks what an align costs when it does not rebuild and does not copy 28.6 GiB.
# Realistic split: mount the big immutable index, copy the small per-sample reads.
set -uo pipefail
B=cookbook-942542972736-us-west-2
W=$HOME/work; mkdir -p "$W"; cd "$W"
R=$W/mount-align.txt; : > "$R"
say(){ printf '%s\n' "$*" | tee -a "$R"; }
push(){ aws s3 cp "$R" "s3://$B/measurements/star-real/mount-align.txt" --only-show-errors 2>/dev/null || true; }
STAR_IMG=quay.io/aarchbio/star@sha256:90331f64bd73eadaefbebc8d4aecfed3ebed1a7e40b761041acbbcf53b3e13ae
LITHSHA=ca145a1cc40823fa3b5304351f91e7397bde2f28c12052e035db6af509d9b706
say "instance_type	$(curl -s -m 3 http://169.254.169.254/latest/meta-data/instance-type 2>/dev/null || echo '?')"
say "nproc	$(nproc)"
say "mem_total_gib	$(awk '/MemTotal/{printf "%.0f", $2/1048576}' /proc/meminfo)"; push

sudo dnf install -y -q docker fuse fuse3 >/dev/null 2>&1
sudo systemctl enable --now docker >/dev/null 2>&1
sudo sed -i 's/^#\s*user_allow_other/user_allow_other/' /etc/fuse.conf
curl -fsSL -o "$W/lith" https://github.com/scttfrdmn/lith/releases/download/v1.1.3/lith_linux_arm64
echo "$LITHSHA  $W/lith" | sha256sum -c - >/dev/null 2>&1 && say "lith_sha	verified" || say "lith_sha	MISMATCH"
chmod +x "$W/lith"; LITH=$W/lith
sudo docker pull -q "$STAR_IMG" >/dev/null 2>&1 && say "star_image	pulled" || say "star_image	PULL-FAILED"; push

say "== index the published prefix (metadata only) and mount it =="
I0=$(date +%s)
$LITH index build "s3://$B/inputs/star-index-GRCh38-116" --index-file "$W/idx.lithidx" > "$W/ix.log" 2>&1
I1=$(date +%s)
say "lith_index_s	$(( I1 - I0 ))"
say "lith_index_bytes	$(stat -c%s "$W/idx.lithidx" 2>/dev/null || echo 0)"
sudo mkdir -p /mnt/staridx && sudo chown "$(whoami)" /mnt/staridx
$LITH mount "s3://$B/inputs/star-index-GRCh38-116" /mnt/staridx --index-file "$W/idx.lithidx" --allow-other --daemon > "$W/mt.log" 2>&1
for i in $(seq 1 60); do mountpoint -q /mnt/staridx && break; sleep 2; done
say "mount_ok	$(mountpoint -q /mnt/staridx && echo yes || echo NO)"
say "index_files_visible	$(ls /mnt/staridx 2>/dev/null | wc -l)"
say "index_bytes_on_s3	$(aws s3 ls "s3://$B/inputs/star-index-GRCh38-116/" --recursive --summarize 2>/dev/null | awk '/Total Size/{print $3}')"
push

say "== copy just the reads (2 GB) -- the per-sample data, which is small =="
C0=$(date +%s)
for r in 1 2; do aws s3 cp "s3://$B/inputs/salmon-real/ERR188026_${r}.fastq.gz" "$W/" --only-show-errors; done
C1=$(date +%s)
say "reads_copy_s	$(( C1 - C0 ))"; push

say "== STAR align, genome read straight off the mount =="
A0=$(date +%s)
sudo docker run --rm -v /mnt/staridx:/idx:ro -v "$W":/w -w /w "$STAR_IMG" bash -lc \
  "export PATH=/opt/conda/bin:\$PATH; STAR --runThreadN $(nproc) --genomeDir /idx \
     --readFilesIn ERR188026_1.fastq.gz ERR188026_2.fastq.gz --readFilesCommand zcat \
     --outSAMtype BAM Unsorted --outFileNamePrefix m_ > star.log 2>&1" 
A1=$(date +%s)
say "mount_align_s	$(( A1 - A0 ))"
say "input_reads	$(awk -F'\t' '/input reads/{gsub(/[^0-9]/,"",$2); print $2}' "$W/m_Log.final.out" 2>/dev/null || echo NA)"
say "pct_unique	$(awk -F'\t' '/Uniquely mapped reads %/{gsub(/[^0-9.]/,"",$2); print $2}' "$W/m_Log.final.out" 2>/dev/null || echo NA)"
say "bam_bytes	$(stat -c%s "$W/m_Aligned.out.bam" 2>/dev/null || echo 0)"
tail -5 "$W/star.log" 2>/dev/null | tee -a "$R"
say DONE; push
aws s3 cp "$W/m_Log.final.out" "s3://$B/measurements/star-real/mount_Log.final.out" --only-show-errors 2>/dev/null || true
