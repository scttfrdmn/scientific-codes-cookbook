#!/usr/bin/env bash
# mash-real, task 00 (sra-tools image, for curl): every genome GTDB R232 places in one genus, fetched
# from NCBI by its versioned accession and checked against NCBI's own md5checksums.txt, plus GTDB's
# species clusters for the genus. Writes /tmp/out/:
#   genomes.tar   <accession>.fna.gz for every genome in the genus (flat)
#   genomes.tsv   file  accession  gtdb_species  rep_accession  is_rep  length  gtdb_genome_size
#   species.tsv   GTDB sp_clusters rows for the genus (rep, species, radius, mean/min ANI and AF, n, members)
#   missing.tsv   genomes NCBI has suppressed or withdrawn since the release (no one can fetch them)
#   prep-checks.txt, staged.sha256
#
#   bash prep.sh <genus>          e.g. g__Corynebacterium
set +e
set -uo pipefail
export PATH=/opt/conda/bin:/usr/local/bin:$PATH
GENUS=${1:?genus}
G=https://data.gtdb.ecogenomic.org/releases/release232/232.0
O=/tmp/out W=/var/tmp/w
mkdir -p "$O" "$W/g" "$W/res"
cp /tmp/fetch1.sh "$W/"
cd "$W"
row() { printf '%-26s %-52s %s\n' "$1" "$2" "$3" | tee -a "$O/prep-checks.txt"; }
check() { if [ "$1" = 1 ]; then row "$2" "$3" "ok   ($4)"; else row "$2" "$3" "FAIL ($4)"; fi; }

# The md5s are GTDB R232's own MD5SUM.txt values.
get() {  # <path under the release> <md5>
  curl -fsS --retry 5 "$G/$1" -o "$(basename "$1")"
  got=$(md5sum "$(basename "$1")" | awk '{print $1}')
  check "$([ "$got" = "$2" ] && echo 1)" "$(basename "$1")" "md5 == GTDB R232 MD5SUM.txt" "$got"
}
get bac120_metadata_r232.tsv.gz 0ba4237077b65cfc5556e1d8be797485
get auxillary_files/sp_clusters_r232.tsv 7e4b2fc21135b6173d2b91ce4f290879

awk -F'\t' -v g="$GENUS" '$3 ~ (";" g "$")' sp_clusters_r232.tsv > "$O/species.tsv"
# accession (prefix stripped), GTDB accession, rep, is_rep, species, path-safe assembly name, genome_size
gzip -dc bac120_metadata_r232.tsv.gz | awk -F'\t' -v g="$GENUS" '
  NR==1 { for (i=1;i<=NF;i++) c[$i]=i; next }
  index($c["gtdb_taxonomy"], ";" g ";") {
    sp=$c["gtdb_taxonomy"]; sub(/.*;s__/, "s__", sp)
    a=$c["accession"]; sub(/^(RS|GB)_/, "", a)
    n=$c["ncbi_assembly_name"]; gsub(/[^A-Za-z0-9._-]/, "_", n)
    printf "%s\t%s\t%s\t%s\t%s\t%s\t%s\n", a, $c["accession"], $c["gtdb_genome_representative"],
      $c["gtdb_representative"], sp, n, $c["genome_size"] }' > sel.tsv
NSEL=$(awk 'END{print NR}' sel.tsv)
NCL=$(awk -F'\t' '{n+=$9} END{print n}' "$O/species.tsv")
NSP=$(awk 'END{print NR}' "$O/species.tsv")
NREP=$(awk -F'\t' '$4=="t"' sel.tsv | awk 'END{print NR}')
check "$([ "$NSEL" = "$NCL" ] && [ "$NSEL" -gt 0 ] && echo 1)" "genomes in $GENUS" "metadata == sum of sp_clusters 'No. clustered genomes'" "$NSEL vs $NCL"
check "$([ "$NREP" = "$NSP" ] && echo 1)" "representatives" "== species clusters" "$NREP vs $NSP"
BAD=$(awk -F'\t' 'NR==FNR { n=split($10, m, ","); for (i=1;i<=n;i++) cl[m[i]]=$1; next }
  { if (cl[$2] != $3) bad++ } END { print bad+0 }' "$O/species.tsv" sel.tsv)
check "$([ "$BAD" = 0 ] && echo 1)" "membership" "metadata rep == the sp_clusters row listing it" "$BAD disagree"

T0=$(date +%s)
awk -F'\t' '{print $1, $6}' sel.tsv | xargs -P 8 -L 1 bash "$W/fetch1.sh"
echo "fetch_s $(( $(date +%s) - T0 ))" | tee -a "$O/prep-checks.txt"

cat res/*.tsv > res.tsv
NOK=$(awk -F'\t' '$2=="OK"' res.tsv | awk 'END{print NR}')
NBAD=$(awk -F'\t' '$2=="MD5FAIL"' res.tsv | awk 'END{print NR}')
check "$([ "$NBAD" = 0 ] && echo 1)" "genomes fetched" "md5 == NCBI md5checksums.txt, every one fetched" "$NOK of $NSEL ok, $NBAD md5 mismatch"
# A genome NCBI has withdrawn since the release cannot be fetched by anyone. It is dropped and listed,
# but only if NCBI itself says suppressed or no longer lists its sequence, and it is not a representative (each species keeps its rep).
awk -F'\t' 'NR==FNR { isrep[$1]=$4; sp[$1]=$5; next } $2=="MISSING" { print $1 "\t" sp[$1] "\t" isrep[$1] "\t" $4 }' sel.tsv res.tsv \
  | LC_ALL=C sort > "$O/missing.tsv"
cat "$O/missing.tsv" | tee -a "$O/prep-checks.txt"
NMISS=$(awk 'END{print NR}' "$O/missing.tsv")
NMBAD=$(awk -F'\t' '$3!="f" || ($4!="status=suppressed" && $4 !~ /^withdrawn/)' "$O/missing.tsv" | awk 'END{print NR}')
check "$([ "$NMBAD" = 0 ] && [ $((NOK + NMISS)) = "$NSEL" ] && echo 1)" "unfetchable genomes" "only non-reps NCBI suppressed or withdrew" "$NMISS dropped, $NMBAD not allowed"
awk -F'\t' 'NR==FNR { if ($2=="OK") len[$1]=$3; next }
  ($1 in len) { r=$3; sub(/^(RS|GB)_/, "", r); printf "%s.fna.gz\t%s\t%s\t%s\t%s\t%s\t%s\n", $1, $1, $5, r, $4, len[$1], $7 }' res.tsv sel.tsv \
  | LC_ALL=C sort > "$O/genomes.tsv"
NLEN=$(awk -F'\t' '$6!=$7' "$O/genomes.tsv" | awk 'END{print NR}')
check "$([ "$NLEN" = 0 ] && echo 1)" "lengths" "fetched length == GTDB genome_size, every genome" "$NLEN differ"
(cd g && ls | LC_ALL=C sort | tar -cf "$O/genomes.tar" -T -)
(cd "$O" && sha256sum genomes.tar genomes.tsv species.tsv missing.tsv) | tee "$O/staged.sha256"
du -sh "$O/genomes.tar" | tee -a "$O/prep-checks.txt"
N=$(grep -c FAIL "$O/prep-checks.txt")
echo "prep_failures $N" | tee -a "$O/prep-checks.txt"
[ "$N" = 0 ] || exit 1
echo "PREP OK"
