# mash-real, called by prep.sh: fetch one genome from NCBI, verify it against NCBI's own md5, record
# its length. Writes /var/tmp/w/g/<accession>.fna.gz and /var/tmp/w/res/<accession>.tsv.
#
#   bash fetch1.sh <versioned accession> <NCBI assembly name, already made path-safe>
a=$1 n=$2 W=/var/tmp/w
d="https://ftp.ncbi.nlm.nih.gov/genomes/all/${a:0:3}/${a:4:3}/${a:7:3}/${a:10:3}"
asm="${a}_$n"
# A genome that cannot be fetched records NCBI's own word for why: assembly_status.txt if there is
# one, else whether the directory's md5checksums.txt still lists a genomic.fna.gz at all.
miss() { st=$(curl -fsS --retry 5 "$d/$asm/assembly_status.txt" 2>/dev/null | tr -d '\r\n')
  if [ -z "$st" ] && [ -s "$W/res/$a.md5" ] && ! grep -q "_genomic.fna.gz$" "$W/res/$a.md5"; then
    st="withdrawn: no genomic.fna.gz in NCBI's md5checksums.txt"; fi
  printf '%s\tMISSING\t0\t%s\n' "$a" "${st:-unknown}" > "$W/res/$a.tsv"; rm -f "$W/g/$a.fna.gz"; exit 0; }
if ! curl -fsS --retry 5 "$d/$asm/md5checksums.txt" -o "$W/res/$a.md5" 2>/dev/null; then
  # the directory name did not follow the usual rule: resolve it from NCBI's listing instead
  asm=$(curl -fsS --retry 5 "$d/" | grep -oE "${a}_[^/\"]+/" | awk 'NR==1' | tr -d '/')
  [ -n "$asm" ] || miss
  curl -fsS --retry 5 "$d/$asm/md5checksums.txt" -o "$W/res/$a.md5" || miss
fi
curl -fsS --retry 5 "$d/$asm/${asm}_genomic.fna.gz" -o "$W/g/$a.fna.gz" || miss
want=$(awk -v f="./${asm}_genomic.fna.gz" '$2==f{print $1}' "$W/res/$a.md5")
got=$(md5sum "$W/g/$a.fna.gz" | awk '{print $1}')
len=$(gzip -dc "$W/g/$a.fna.gz" | awk '!/^>/{b+=length($0)} END{print b+0}')
if [ -n "$want" ] && [ "$got" = "$want" ]; then s=OK; else s=MD5FAIL; fi
printf '%s\t%s\t%s\n' "$a" "$s" "$len" > "$W/res/$a.tsv"
