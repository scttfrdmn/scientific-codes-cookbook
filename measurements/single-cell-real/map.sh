# Task 1 of 2 -- piscem: index the whole Ensembl 116 mouse cDNA set, map one whole 10x v2 channel.
# Staged as a pinned file (the spec checks its sha256 before running it). Writes /tmp/map.txt.
set +e
set -uo pipefail
export PATH=/opt/conda/bin:$PATH
export TMPDIR=/tmp HOME=/tmp
cd /tmp
LBL="${LBL:-$(uname -m)}"; THREADS="${THREADS:-$(nproc)}"
R1S="${R1S:-10X_P5_13_S14_L001_R1_001.fastq.gz,10X_P5_13_S14_L002_R1_001.fastq.gz}"
R2S="${R2S:-10X_P5_13_S14_L001_R2_001.fastq.gz,10X_P5_13_S14_L002_R2_001.fastq.gz}"
CDNA=Mus_musculus.GRCm39.cdna.all.fa.gz
OUT=/tmp/map.txt
: > "$OUT"
rec(){ printf '%s\t%s\n' "$1" "$2" | tee -a "$OUT"; }
die(){ rec FAIL "$1"; exit 1; }

# pins: the staged bytes are the bytes this measurement was verified on
[ -s pins-map.sha256 ] || die "no pins-map.sha256 staged"; {
  sha256sum -c pins-map.sha256 > pins.log 2>&1 || { cat pins.log; die "input sha256 mismatch"; }
  rec inputs_sha256 "all $(wc -l < pins-map.sha256 | tr -d ' ') match pins-map.sha256"
}

# ---- 1 Hz cgroup sampler: anon = process memory (tmpfs pages are shmem, counted apart) ----
CG=/sys/fs/cgroup
( while :; do
    printf '%s\t%s\t%s\t%s\n' "$(date +%s)" \
      "$(awk '/^anon /{print $2}' $CG/memory.stat 2>/dev/null || echo 0)" \
      "$(awk '/^shmem /{print $2}' $CG/memory.stat 2>/dev/null || echo 0)" \
      "$(awk '/^usage_usec/{print $2}' $CG/cpu.stat 2>/dev/null || echo 0)" >> /tmp/samp.tsv
    sleep 1
  done ) & SP=$!
trap 'kill $SP 2>/dev/null' EXIT

rec label "$LBL"; rec arch "$(uname -m)"; rec nproc "$(nproc)"; rec threads "$THREADS"
rec piscem_version "$(piscem --version 2>&1 | tr '\n' ' ')"

# read counts straight from the FASTQs, in the background while the index builds.
# One reader to EOF per file -- no early exit, so no SIGPIPE under pipefail.
for f in ${R1S//,/ } ${R2S//,/ }; do
  ( zcat "$f" | awk 'END{print NR/4}' > "$f.nreads" ) &
done

# t2g from Ensembl's own headers: transcript id -> gene id, both versioned, exactly as indexed
zcat "$CDNA" | awk '/^>/{t=substr($1,2); g=""; for(i=2;i<=NF;i++) if($i ~ /^gene:/) g=substr($i,6); print t"\t"g}' > t2g.tsv
NTX=$(wc -l < t2g.tsv | tr -d ' ')
NOG=$(awk -F'\t' '$2==""' t2g.tsv | wc -l | tr -d ' ')
rec transcripts "$NTX"; rec genes "$(cut -f2 t2g.tsv | sort -u | wc -l | tr -d ' ')"
[ "$NOG" -eq 0 ] || die "$NOG transcripts have no gene: field"

T0=$(date +%s)
mkdir -p idx
piscem build -s "$CDNA" -k 31 -m 19 -t "$THREADS" -o idx/mm116 > build.log 2>&1 || die "piscem build failed (build.log)"
T1=$(date +%s)
rec build_wall_s "$((T1-T0))"
rec index_kib "$(du -sk idx | cut -f1)"

piscem map-sc -i idx/mm116 -g "1{b[16]u[10]x:}2{r:}" -1 "$R1S" -2 "$R2S" -t "$THREADS" -o rad > map.log 2>&1 \
  || die "piscem map-sc failed (map.log)"
T2=$(date +%s)
rec map_wall_s "$((T2-T1))"
kill $SP 2>/dev/null

J=rad/map_info.json
jnum(){ awk -v k="\"$1\"" -F: '$1 ~ k {gsub(/[ ",]/,"",$2); print $2; exit}' "$J"; }
NREADS=$(jnum num_reads); NMAPPED=$(jnum num_mapped)
rec piscem_num_reads "$NREADS"; rec piscem_num_mapped "$NMAPPED"; rec piscem_pct_mapped "$(jnum percent_mapped)"
rec rad_kib "$(du -sk rad | cut -f1)"

wait   # the background read counters
F1=0; F2=0
for f in ${R1S//,/ }; do F1=$((F1 + $(cat "$f.nreads"))); done
for f in ${R2S//,/ }; do F2=$((F2 + $(cat "$f.nreads"))); done
rec fastq_reads_R1 "$F1"; rec fastq_reads_R2 "$F2"

awk -F'\t' '{if($2+0>a)a=$2+0; if($3+0>s)s=$3+0} NR==1{u0=$4;t0=$1} {u=$4;t=$1}
  END{printf "peak_anon_gib\t%.2f\npeak_shmem_gib\t%.2f\navg_cores\t%.2f\n", a/2^30, s/2^30, (u-u0)/1e6/(t-t0)}' /tmp/samp.tsv | tee -a "$OUT"

# ---- identities ----
[ "$F1" -eq "$F2" ] || die "R1 has $F1 reads, R2 has $F2 -- mates disagree"
[ "$NREADS" -eq "$F1" ] || die "piscem processed $NREADS reads, the FASTQs hold $F1 -- input not fully read"
[ "$NMAPPED" -gt 0 ] && [ "$NMAPPED" -le "$NREADS" ] || die "num_mapped $NMAPPED out of range"
test -s rad/map.rad || die "no map.rad"
rec MAP_OK "piscem read every FASTQ record ($NREADS) and wrote rad/"
