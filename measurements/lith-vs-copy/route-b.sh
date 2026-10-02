set -uo pipefail
B="${COOKBOOK_BUCKET:?set COOKBOOK_BUCKET (make print-bucket)}"
W=/home/scttfrdmn/work
R=$W/resultB.txt; : > "$R"
say(){ printf '%s\n' "$*" | tee -a "$R"; }
push(){ aws s3 cp "$R" "s3://$B/measurements/lith-vs-copy/resultB.txt" --only-show-errors 2>/dev/null || true; }
REF=GRCh38_full_analysis_set_plus_decoy_hla.fa
BWA=quay.io/aarchbio/bwa@sha256:19f0eceab80740b821be7ada082d4434acf778912aac658dd1b4c6692dd2e9ba
N=$(nproc); LITH=$W/lith
# A FUSE mount is private to the mounting user; the pinned image runs as mambauser (57439),
# so the container got EACCES. --allow-other fixes it, and it needs user_allow_other in fuse.conf.
sudo sed -i 's/^#\s*user_allow_other/user_allow_other/' /etc/fuse.conf
say "fuse_conf	$(grep -c '^user_allow_other' /etc/fuse.conf) (1 = enabled)"
fusermount3 -u /mnt/ref 2>/dev/null; fusermount3 -u /mnt/reads 2>/dev/null; sleep 2
M0=$(date +%s)
$LITH mount s3://1000genomes/technical/reference/GRCh38_reference_genome /mnt/ref \
   --index-file "$W/ref.lithidx" --no-sign-request --allow-other --daemon > "$W/mr3.log" 2>&1
$LITH mount s3://1000genomes/phase3/data/HG00096/sequence_read /mnt/reads \
   --index-file "$W/rd.lithidx" --no-sign-request --allow-other --daemon > "$W/md3.log" 2>&1
for i in $(seq 1 60); do mountpoint -q /mnt/ref && mountpoint -q /mnt/reads && break; sleep 2; done
M1=$(date +%s)
say "lith_mount_s	$(( M1 - M0 ))"
say "index_bytes	$(( $(stat -c%s "$W/ref.lithidx") + $(stat -c%s "$W/rd.lithidx") ))"
say "container_sees	$(sudo docker run --rm -v /mnt/ref:/ref:ro "$BWA" bash -lc 'ls /ref 2>&1 | wc -l')"
push
say "== bwa reading the PUBLIC 1000genomes bucket through lith, zero bulk copy =="
T0=$(date +%s)
CB=$(sudo docker run --rm -v /mnt/ref:/ref:ro -v /mnt/reads:/reads:ro "$BWA" bash -lc \
  "export PATH=/opt/conda/bin:\$PATH; bwa mem -t $N /ref/$REF /reads/SRR062634_1.filt.fastq.gz /reads/SRR062634_2.filt.fastq.gz 2>/tmp/eb.log | awk '!/^@/{n++} END{print n}'")
T1=$(date +%s)
say "lithB_align_s	$(( T1 - T0 ))"
say "lithB_records	${CB:-NA}"
say DONE_B; push
