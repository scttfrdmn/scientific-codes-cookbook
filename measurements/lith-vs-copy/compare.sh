#!/usr/bin/env bash
# Copy route vs lith mount: SAME instance, same NIC, same pinned bwa image, only the data
# path differs. NOTE: `spawn launch --command` runs as a NON-ROOT user and the instance has
# neither docker nor fuse (unlike the task-run path), so everything privileged uses sudo.
set -uo pipefail
B="${COOKBOOK_BUCKET:?set COOKBOOK_BUCKET (make print-bucket)}"
W=$HOME/work; mkdir -p "$W"; cd "$W"
R=$W/result.txt; : > "$R"
say(){ printf '%s\n' "$*" | tee -a "$R"; }
push(){ aws s3 cp "$R" "s3://$B/measurements/lith-vs-copy/result.txt" --only-show-errors 2>/dev/null || true; }
REF=GRCh38_full_analysis_set_plus_decoy_hla.fa
BWA=quay.io/aarchbio/bwa@sha256:19f0eceab80740b821be7ada082d4434acf778912aac658dd1b4c6692dd2e9ba
LITHSHA=ca145a1cc40823fa3b5304351f91e7397bde2f28c12052e035db6af509d9b706
say "whoami	$(whoami)"
say "instance_type	$(curl -s -m 3 http://169.254.169.254/latest/meta-data/instance-type 2>/dev/null || echo '?')"
say "nproc	$(nproc)"; push

say "== prerequisites a launch instance does NOT have: docker, fuse =="
sudo dnf install -y -q docker fuse fuse3 >/dev/null 2>&1
sudo systemctl enable --now docker >/dev/null 2>&1
say "docker	$(sudo docker --version 2>&1 | head -1)"
say "fusermount3	$(command -v fusermount3 || echo MISSING)"
curl -fsSL -o "$W/lith" https://github.com/scttfrdmn/lith/releases/download/v1.1.3/lith_linux_arm64
echo "$LITHSHA  $W/lith" | sha256sum -c - >/dev/null 2>&1 && say "lith_sha	verified" || say "lith_sha	MISMATCH"
chmod +x "$W/lith"; LITH="$W/lith"
say "lith_version	$($LITH version 2>&1 | head -1)"; push
sudo docker pull -q "$BWA" >/dev/null 2>&1 && say "bwa_image	pulled" || say "bwa_image	PULL-FAILED"; push

# ---------------- route A: copy (what everyone does) ----------------
say "== route A: aws s3 cp index + reads to local disk, then align =="
mkdir -p "$W/copy"; cd "$W/copy"
A0=$(date +%s)
for e in amb ann bwt pac sa; do aws s3 cp "s3://$B/inputs/bwa-real/$REF.$e" . --only-show-errors; done
for r in 1 2; do aws s3 cp "s3://$B/inputs/bwa-real/SRR062634_${r}.filt.fastq.gz" . --only-show-errors; done
A1=$(date +%s)
say "copyA_stage_s	$(( A1 - A0 ))"
say "copyA_bytes	$(du -sb "$W/copy" | awk '{print $1}')"; push
sudo docker run --rm -v "$W/copy":/d -w /d "$BWA" bash -lc \
  "export PATH=/opt/conda/bin:\$PATH; bwa mem -t $(nproc) $REF SRR062634_1.filt.fastq.gz SRR062634_2.filt.fastq.gz 2>/tmp/a.log | awk '!/^@/{n++} END{print n}' > /d/countA.txt"
A2=$(date +%s)
say "copyA_align_s	$(( A2 - A1 ))"
say "copyA_total_s	$(( A2 - A0 ))"
say "copyA_records	$(cat "$W/copy/countA.txt" 2>/dev/null || echo NA)"; push

# ---------------- route B: lith mount (metadata only, reads the PUBLIC bucket in place) ----
say "== route B: lith index (metadata only) + mount, align in place, no bulk copy =="
cd "$W"
B0=$(date +%s)
$LITH index build s3://1000genomes/technical/reference/GRCh38_reference_genome --index-file "$W/ref.lithidx" --no-sign-request > "$W/ixr.log" 2>&1
$LITH index build s3://1000genomes/phase3/data/HG00096/sequence_read       --index-file "$W/rd.lithidx"  --no-sign-request > "$W/ixd.log" 2>&1
B1=$(date +%s)
say "lithB_index_s	$(( B1 - B0 ))"
say "lithB_index_bytes	$(( $(stat -c%s "$W/ref.lithidx" 2>/dev/null || echo 0) + $(stat -c%s "$W/rd.lithidx" 2>/dev/null || echo 0) ))"
tail -3 "$W/ixr.log" | tee -a "$R"; push
sudo mkdir -p /mnt/ref /mnt/reads && sudo chown "$(whoami)" /mnt/ref /mnt/reads
$LITH mount s3://1000genomes/technical/reference/GRCh38_reference_genome /mnt/ref   --index-file "$W/ref.lithidx" --no-sign-request > "$W/mr.log" 2>&1 &
$LITH mount s3://1000genomes/phase3/data/HG00096/sequence_read       /mnt/reads --index-file "$W/rd.lithidx"  --no-sign-request > "$W/md.log" 2>&1 &
for i in $(seq 1 60); do [ -e "/mnt/ref/$REF.bwt" ] && [ -e /mnt/reads/SRR062634_1.filt.fastq.gz ] && break; sleep 2; done
say "lithB_mount_ok	$([ -e "/mnt/ref/$REF.bwt" ] && echo yes || echo NO)"
ls -l /mnt/ref 2>&1 | head -4 | tee -a "$R"; tail -3 "$W/mr.log" | tee -a "$R"; push
B2=$(date +%s)
sudo docker run --rm \
  --mount type=bind,src=/mnt/ref,dst=/ref,readonly,bind-propagation=rslave \
  --mount type=bind,src=/mnt/reads,dst=/reads,readonly,bind-propagation=rslave \
  -v "$W":/out "$BWA" bash -lc \
  "export PATH=/opt/conda/bin:\$PATH; bwa mem -t $(nproc) /ref/$REF /reads/SRR062634_1.filt.fastq.gz /reads/SRR062634_2.filt.fastq.gz 2>/tmp/b.log | awk '!/^@/{n++} END{print n}' > /out/countB.txt"
B3=$(date +%s)
say "lithB_mount_s	$(( B2 - B1 ))"
say "lithB_align_s	$(( B3 - B2 ))"
say "lithB_total_s	$(( B3 - B0 ))"
say "lithB_records	$(cat "$W/countB.txt" 2>/dev/null || echo NA)"
say DONE; push
