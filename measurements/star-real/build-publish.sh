set -euo pipefail
export PATH=/opt/conda/bin:$PATH
export TMPDIR=/tmp HOME=/tmp
cd /tmp
B=cookbook-942542972736-us-west-2
THREADS="${THREADS:-32}"
gunzip -c GRCh38.primary_assembly.fa.gz > genome.fa
gunzip -c GRCh38.116.gtf.gz            > genes.gtf
echo "== build the full human index once =="
T0=$(date +%s)
STAR --runMode genomeGenerate --runThreadN "$THREADS" --genomeDir idx \
     --genomeFastaFiles genome.fa --sjdbGTFfile genes.gtf --sjdbOverhang 100 \
     --outFileNamePrefix gg_ > gg.log 2>&1 || { tail -20 gg.log Log.out 2>/dev/null; exit 1; }
T1=$(date +%s)
IDXB=$(du -sb idx | awk '{print $1}')
# Publish the index as INDIVIDUAL FILES under a prefix, not a tar: that is what makes it
# mountable later. A tar would have to be copied and unpacked by every consumer.
echo "== publish it so every later align can mount it instead of rebuilding =="
T2=$(date +%s)
aws s3 cp idx/ "s3://$B/inputs/star-index-GRCh38-116/" --recursive --only-show-errors
T3=$(date +%s)
{ printf 'star_version\t%s\n' "$(STAR --version)"
  printf 'threads\t%s\n' "$THREADS"
  printf 'index_wall_s\t%s\n' "$(( T1 - T0 ))"
  printf 'index_bytes\t%s\n' "$IDXB"
  printf 'publish_wall_s\t%s\n' "$(( T3 - T2 ))"
  printf 'index_files\t%s\n' "$(ls idx | wc -l)"
} | tee index-stats.txt
ls -l idx | awk 'NR>1{printf "  %-28s %s\n", $9, $5}'
echo "PUBLISH OK"
