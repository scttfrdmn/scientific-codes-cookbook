set -euo pipefail
export PATH=/opt/conda/bin:$PATH
export TMPDIR=/tmp HOME=/tmp
cd /tmp
LBL="${LBL:-unknown}"; THREADS="${THREADS:-$(nproc)}"
REF=GRCh38_full_analysis_set_plus_decoy_hla.fa

bwa > ver.txt 2>&1 || true          # no pipe: bwa's usage goes to a file, then awk reads it
BWAVER=$(awk '/^Version/{v=$2} END{print v}' ver.txt)

echo "== staged inputs =="
ls -l $REF.* SRR062634_*.gz | awk '{printf "  %-52s %s bytes\n", $9, $5}'
for f in $REF.bwt $REF.sa $REF.pac SRR062634_1.filt.fastq.gz SRR062634_2.filt.fastq.gz; do
  test -s "$f" || { echo "missing input $f"; exit 1; }
done

# ---- 1 Hz cgroup sampler (the measurements/sizing-ratio instrument, inline) ----
CG=/sys/fs/cgroup
sample(){ : > /tmp/samp.tsv
  while :; do
    m=$(cat $CG/memory.current 2>/dev/null || echo 0)
    a=$(awk '/^anon /{print $2}' $CG/memory.stat 2>/dev/null || echo 0)
    u=$(awk '/^usage_usec/{print $2}' $CG/cpu.stat 2>/dev/null || echo 0)
    printf '%s\t%s\t%s\t%s\n' "$(date +%s)" "$m" "$a" "$u" >> /tmp/samp.tsv
    sleep 1
  done; }
sample & SP=$!
trap 'kill $SP 2>/dev/null || true' EXIT

echo "== how much data is this really? =="
# ONE pass, and NO early exit anywhere in the pipeline. `awk ... exit` after line 2 of a
# 1.8 GiB gzip SIGPIPEs zcat, and under `set -o pipefail` the task dies with 141 -- the same
# failure as `| head`, wearing a different costume. Any reader that stops early does this.
read -r NP RL < <(zcat SRR062634_1.filt.fastq.gz | awk 'NR==2{l=length($0)} END{print NR/4, l}')
printf 'read_pairs\t%s\nread_len\t%s\ntotal_bases\t%s\n' "$NP" "$RL" \
  "$(awk -v p="$NP" -v l="$RL" 'BEGIN{printf "%.2f Gbp", 2*p*l/1e9}')" | tee "size-$LBL.txt"

echo "== bwa mem -t $THREADS on the full GRCh38 index =="
T0=$(date +%s)
# ONE awk that reads the stream to EOF: counts, and saves the first lines to a file.
# Do NOT pipe to `head` here -- head exits early, SIGPIPEs bwa, and under `set -o pipefail`
# the task dies with exit 141 having done all the work. (Hit that here; third time in this
# project, hence the note.)
bwa mem -t "$THREADS" -R '@RG\tID:SRR062634\tSM:HG00096\tPL:ILLUMINA' \
    "$REF" SRR062634_1.filt.fastq.gz SRR062634_2.filt.fastq.gz 2> bwa.log \
  | awk 'NR<=5000 { print > "/tmp/sam_head.txt" }
         !/^@/ { n++; if ($3!="*") m++ }
         END { printf "%d\t%d\n", n, m }' > counts.tsv
T1=$(date +%s)
WALL=$(( T1 - T0 ))
read -r NREC NMAP < counts.tsv
kill $SP 2>/dev/null || true

PEAK=$(awk -F'\t' '{if($2+0>m)m=$2+0} END{printf "%.2f", m/1073741824}' /tmp/samp.tsv)
ANON=$(awk -F'\t' '{if($3+0>m)m=$3+0} END{printf "%.2f", m/1073741824}' /tmp/samp.tsv)
CORES=$(awk -F'\t' 'NR==1{u0=$4;t0=$1} END{printf "%.2f", ($4-u0)/1e6/($1-t0)}' /tmp/samp.tsv)
{
printf 'label\t%s\n' "$LBL"
printf 'threads\t%s\n' "$THREADS"
printf 'nproc\t%s\n' "$(nproc)"
printf 'bwa_version\t%s\n' "$BWAVER"
printf 'bwa_wall_s\t%s\n' "$WALL"
printf 'sam_records\t%s\n' "$NREC"
printf 'mapped_records\t%s\n' "$NMAP"
printf 'pct_mapped\t%s\n' "$(awk -v m="$NMAP" -v n="$NREC" 'BEGIN{printf "%.2f", 100*m/n}')"
printf 'reads_per_sec\t%s\n' "$(awk -v n="$NREC" -v w="$WALL" 'BEGIN{printf "%.0f", n/w}')"
printf 'peak_rss_gib\t%s\n' "$PEAK"
printf 'anon_max_gib\t%s\n' "$ANON"
printf 'avg_cores\t%s\n' "$CORES"
printf 'core_efficiency\t%s\n' "$(awk -v c="$CORES" -v t="$THREADS" 'BEGIN{printf "%.0f%%", 100*c/t}')"
} | tee -a "size-$LBL.txt"
awk '/Real time|CPU/{last2=prev; prev=$0} END{if(last2)print last2; if(prev)print prev}' bwa.log | tee -a "size-$LBL.txt"
test "$NREC" -gt 1000000 || { echo "only $NREC records -- not a real workload"; exit 1; }
echo "ALIGN OK"
