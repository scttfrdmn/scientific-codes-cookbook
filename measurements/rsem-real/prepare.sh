#!/usr/bin/env bash
# measurements/rsem-real task 0 (RSEM image, Graviton4, run once): the RSEM reference for the
# whole Ensembl 116 cDNA set -- the same bytes salmon-real indexed -- with RSEM's isoform -> gene
# map taken from each record's own `gene:` field, so genes.results aggregates real isoforms.
#   prepare.sh
set +e
set -uo pipefail
export PATH=/usr/local/bin:/opt/conda/bin:$PATH HOME=/tmp TMPDIR=/tmp
O=/tmp/out; R=/tmp/ref; W=/var/tmp/prep
mkdir -p $O $R $W
fail() { echo "FAIL: $*" | tee -a $O/pins.txt; exit 1; }
{
  echo "uname_m	$(uname -m)"
  echo "nproc	$(nproc)"
  for t in python3 perl Rscript samtools pigz bgzip; do echo "has_$t	$(command -v $t || echo no)"; done
  echo "rsem	$(rsem-calculate-expression --version 2>&1 | head -n 1)"
} > $O/host.txt 2>&1
cat $O/host.txt

echo "== the staged transcriptome against Ensembl's own CHECKSUMS =="
FS=$(sum /tmp/ensembl116_cdna.fa.gz | awk '{print $1, $2}')
echo "Homo_sapiens.GRCh38.cdna.all.fa.gz sum $FS want 22380 179589 (Ensembl release-116 cdna/CHECKSUMS)" > $O/pins.txt
cat $O/pins.txt
[ "$FS" = "22380 179589" ] || fail "cDNA sum"

gzip -dc /tmp/ensembl116_cdna.fa.gz > $W/cdna.fa || fail "gunzip cDNA"
# gene_id <TAB> transcript_id, from the header's own gene: field. RSEM names a transcript by the
# header up to its first space, which is also the name salmon's quant.sf carries.
awk '/^>/{t=substr($1,2); g=""; for(i=2;i<=NF;i++) if($i ~ /^gene:/) g=substr($i,6);
  if (g=="") { print "no gene: field for " t > "/dev/stderr"; exit 1 } print g "\t" t }' $W/cdna.fa > $O/gene_map.tsv \
  || fail "gene map"
NT=$(grep -c '^>' $W/cdna.fa)
NM=$(wc -l < $O/gene_map.tsv | tr -d ' ')
NG=$(cut -f1 $O/gene_map.tsv | sort -u | wc -l | tr -d ' ')
NU=$(cut -f2 $O/gene_map.tsv | sort -u | wc -l | tr -d ' ')
echo "transcripts $NT map_rows $NM unique_transcript_ids $NU genes $NG" | tee -a $O/pins.txt
[ "$NT" = "$NM" ] && [ "$NT" = "$NU" ] || fail "transcript ids are not unique or the map lost rows"

T0=$(date +%s)
rsem-prepare-reference --transcript-to-gene-map $O/gene_map.tsv $W/cdna.fa $R/ens116 > $O/prepare.log 2>&1 \
  || { tail -n 30 $O/prepare.log; fail "rsem-prepare-reference"; }
echo "prepare_wall_s	$(( $(date +%s) - T0 ))" | tee -a $O/pins.txt
NR=$(grep -c '^>' $R/ens116.transcripts.fa)
echo "ref_transcripts $NR" | tee -a $O/pins.txt
[ "$NR" = "$NT" ] || fail "RSEM reference holds $NR transcripts, the cDNA $NT"
rm -f $W/cdna.fa
( cd $R && sha256sum ens116.* ) > $O/ref.sha256
# salmon-real's Graviton4 quant.sf, which task 2 ranks against: pinned here, where it is staged first
( cd /tmp && sha256sum salmon.quant.sf ) > $O/salmon.sha256
echo "salmon_quant_rows $(( $(wc -l < /tmp/salmon.quant.sf) - 1 ))" | tee -a $O/pins.txt
cat $O/salmon.sha256
cat $O/ref.sha256
ls -la $R
echo "PREPARE OK"
