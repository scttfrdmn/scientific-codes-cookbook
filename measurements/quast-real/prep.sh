#!/usr/bin/env bash
# quast-real, task 0 (sra-tools image, for curl): fetch nine published complete-genome assemblies of
# E. coli K-12 MG1655 from NCBI GenBank, and the reference's own annotation, each checked against
# NCBI's md5checksums.txt and against the sequence count and total length in NCBI's assembly report
# (assemblies.tsv). Writes /tmp/out/.
#
#   bash prep.sh assemblies.tsv
set +e
set -uo pipefail
export PATH=/opt/conda/bin:/usr/local/bin:$PATH
O=/tmp/out
mkdir -p "$O"
cd /tmp
row() { printf '%-28s %-44s %s\n' "$1" "$2" "$3" | tee -a "$O/prep-checks.txt"; }
check() { if [ "$1" = 1 ]; then row "$2" "$3" "ok   ($4)"; else row "$2" "$3" "FAIL ($4)"; fi; }
ncbi() { local a=$1; echo "https://ftp.ncbi.nlm.nih.gov/genomes/all/${a:0:3}/${a:4:3}/${a:7:3}/${a:10:3}/$a"; }
fetch() {  # <assembly> <suffix> <dest>
  local a=$1 s=$2 d=$3 u want got
  u=$(ncbi "$a")
  curl -fsS --retry 3 "$u/${a}_$s" -o "$O/$d"
  curl -fsS --retry 3 "$u/md5checksums.txt" -o "$O/$a.md5checksums.txt"
  want=$(awk -v f="./${a}_$s" '$2==f{print $1}' "$O/$a.md5checksums.txt")
  got=$(md5sum "$O/$d" | awk '{print $1}')
  check "$([ -n "$want" ] && [ "$got" = "$want" ] && echo 1)" "$d" "md5 == NCBI md5checksums" "$got"
}

fetch GCF_000005845.2_ASM584v2 genomic.gff.gz GCF_000005845.2.gff.gz
grep -v '^#' "$1" > list.tsv
while IFS=$'\t' read -r label asm tech len nseq; do
  fetch "$asm" genomic.fna.gz "$label.fna.gz"
  got=$(gzip -dc "$O/$label.fna.gz" | awk '/^>/{n++; next}{b+=length($0)}END{print n, b}')
  check "$([ "$got" = "$nseq $len" ] && echo 1)" "$label.fna.gz" "sequences, length == NCBI assembly report" "want $nseq $len, got $got"
done < list.tsv
(cd "$O" && sha256sum *.fna.gz GCF_000005845.2.gff.gz) | tee "$O/staged.sha256"
N=$(grep -c FAIL "$O/prep-checks.txt")
echo "prep_failures $N" | tee -a "$O/prep-checks.txt"
[ "$N" = 0 ] || exit 1
echo "PREP OK"
