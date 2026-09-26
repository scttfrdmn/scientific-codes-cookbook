set -euo pipefail
export PATH=/opt/conda/bin:$PATH
export TMPDIR=/tmp HOME=/tmp
cd /tmp
THREADS="${THREADS:-32}"
gunzip -c GRCh38.primary_assembly.fa.gz > genome.fa
gunzip -c GRCh38.116.gtf.gz            > genes.gtf
echo "== build the full human index once =="
T0=$(date +%s)
STAR --runMode genomeGenerate --runThreadN "$THREADS" --genomeDir idx \
     --genomeFastaFiles genome.fa --sjdbGTFfile genes.gtf --sjdbOverhang 100 \
     --outFileNamePrefix gg_ > gg.log 2>&1 || { tail -20 gg.log Log.out 2>/dev/null; exit 1; }
T1=$(date +%s)
# Flatten into /tmp so each file can be a declared spawn output. This image has neither aws
# nor python3, so the upload MUST be spawn's stage-out, not a CLI call inside the container.
for f in idx/*; do cp "$f" "/tmp/idx_$(basename "$f")"; done
{ printf 'star_version\t%s\n' "$(STAR --version)"
  printf 'threads\t%s\n' "$THREADS"
  printf 'index_wall_s\t%s\n' "$(( T1 - T0 ))"
  printf 'index_bytes\t%s\n' "$(du -sb idx | awk '{print $1}')"
  printf 'index_files\t%s\n' "$(ls idx | wc -l)"
} | tee index-stats.txt
ls -l idx | awk 'NR>1{printf "  %-28s %s\n", $9, $5}'
echo "BUILD OK -- spawn stages the index out"
