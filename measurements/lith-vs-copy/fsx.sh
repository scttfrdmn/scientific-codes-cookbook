#!/usr/bin/env bash
# FSx for Lustre, S3-linked. Tests the received wisdom that "Lustre is fast" against the
# access pattern that actually matters here: bwa mmap'ing an 8.9 GiB index.
set -uo pipefail
exec > >(tee -a /var/log/fsxcmp.log) 2>&1
R=/tmp/fsxcmp.txt; : > "$R"
B="${COOKBOOK_BUCKET:?set COOKBOOK_BUCKET (make print-bucket)}"
say(){ printf '%s\n' "$*" | tee -a "$R"; }
push(){ aws s3 cp "$R" "s3://$B/measurements/lith-vs-copy/fsx-result.txt" --only-show-errors 2>/dev/null || true; }
REF=GRCh38_full_analysis_set_plus_decoy_hla.fa
BWA=quay.io/aarchbio/bwa@sha256:19f0eceab80740b821be7ada082d4434acf778912aac658dd1b4c6692dd2e9ba
say "instance_type	$(curl -s -m 3 http://169.254.169.254/latest/meta-data/instance-type 2>/dev/null || echo '?')"
say "nproc	$(nproc)"
# push results after EVERY phase: the lith run spent 30+ min and uploaded nothing because
# it only pushed at the end. Never again.
M=$(mount | grep -i lustre | head -1); say "lustre_mount	${M:-NONE}"
FSX=/mnt/fsx
say "fsx_listing	$(ls "$FSX" 2>&1 | tr '\n' ' ' | cut -c1-200)"
ls -l "$FSX" 2>&1 | head -12 | tee -a "$R"; push

say "== is the data a stub until touched? (hsm state) =="
command -v lfs >/dev/null 2>&1 && lfs hsm_state "$FSX/$REF.bwt" 2>&1 | tee -a "$R" || say "lfs	not-present"
say "df	$(df -h "$FSX" 2>/dev/null | tail -1)"; push

say "== COLD: align reading straight off Lustre (lazy-load from S3 on first touch) =="
T0=$(date +%s)
docker run --rm -v "$FSX":/d:ro -v /tmp:/out "$BWA" bash -lc \
  "export PATH=/opt/conda/bin:\$PATH; bwa mem -t $(nproc) /d/$REF /d/SRR062634_1.filt.fastq.gz /d/SRR062634_2.filt.fastq.gz 2>/tmp/f.log | awk '!/^@/{n++} END{print n}' > /out/countF.txt"
T1=$(date +%s)
say "fsx_cold_align_s	$(( T1 - T0 ))"
say "fsx_cold_records	$(cat /tmp/countF.txt 2>/dev/null || echo NA)"; push

say "== WARM: same again, now that Lustre has the blocks =="
T2=$(date +%s)
docker run --rm -v "$FSX":/d:ro -v /tmp:/out "$BWA" bash -lc \
  "export PATH=/opt/conda/bin:\$PATH; bwa mem -t $(nproc) /d/$REF /d/SRR062634_1.filt.fastq.gz /d/SRR062634_2.filt.fastq.gz 2>/tmp/f2.log | awk '!/^@/{n++} END{print n}' > /out/countF2.txt"
T3=$(date +%s)
say "fsx_warm_align_s	$(( T3 - T2 ))"
say "fsx_warm_records	$(cat /tmp/countF2.txt 2>/dev/null || echo NA)"
command -v lfs >/dev/null 2>&1 && lfs hsm_state "$FSX/$REF.bwt" 2>&1 | tee -a "$R"
say DONE; push
aws s3 cp /var/log/fsxcmp.log "s3://$B/measurements/lith-vs-copy/fsx-full.log" --only-show-errors 2>/dev/null || true
