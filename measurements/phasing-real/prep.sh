#!/usr/bin/env bash
# Task 00 (samtools image, once): cut chr20 out of HG002's GIAB reads and benchmark VCFs.
#   reads   PacBio HiFi Revio 48x (s3://giab, RODA) and Illumina 2x250 (NCBI's GIAB FTP: the RODA
#           copy of this BAM is the pre-February-2019 file with merged-in duplicate reads), both
#           GRCh38, read over HTTPS through the publisher's own .bai, md5-checked before it is used
#   VCFs    the v4.2.1 benchmark (the genotypes to phase) and its two phased supplements (truth),
#           server-side copies in the bucket
# Everything written here is sha256-pinned in pins.sha256 for the phase task.
set +e
set -uo pipefail
cd /tmp
. ./lib.sh
HIFI=https://s3.amazonaws.com/giab/data/AshkenazimTrio/HG002_NA24385_son/PacBio_HiFi-Revio_20231031/HG002_PacBio-HiFi-Revio_20231031_48x_GRCh38-GIABv3.bam
ILMN=https://ftp-trace.ncbi.nlm.nih.gov/ReferenceSamples/giab/data/AshkenazimTrio/HG002_NA24385_son/NIST_Illumina_2x250bps/novoalign_bams/HG002.GRCh38.2x250.bam
machine
rec samtools_version "$(samtools --version | head -1)"

# the publisher's index, fetched by htslib itself (idxstats reads only the index), then md5-checked
# against README_HG002-PacBio-Revio.md and HG002_corrected_md5sums.feb19upload.txt
samtools idxstats "$HIFI" > hifi.idxstats; mv "${HIFI##*/}.bai" hifi.bai
samtools idxstats "$ILMN" > ilmn.idxstats; mv "${ILMN##*/}.bai" ilmn.bai
chk "HiFi .bai md5 == GIAB's" "$(md5 < hifi.bai)" 9d0218df76ab404cf01e87b453f19458
chk "Illumina .bai md5 == GIAB's" "$(md5 < ilmn.bai)" a3c2b449df6509ca83fbd3fea22b9aee
cp hifi.idxstats ilmn.idxstats "$O/"
# evidence for the source choice: the RODA copy of the Illumina BAM, index only
mkdir -p roda && ( cd roda && samtools idxstats https://s3.amazonaws.com/giab/data/AshkenazimTrio/HG002_NA24385_son/NIST_Illumina_2x250bps/novoalign_bams/HG002.GRCh38.2x250.bam > "$O/ilmn.roda.idxstats"
  rec ilmn_roda_bai_md5 "$(md5 < HG002.GRCh38.2x250.bam.bai)" )
rec ilmn_chr20_records_ncbi "$(awk '$1=="chr20"{print $3+$4}' ilmn.idxstats)"
rec ilmn_chr20_records_roda "$(awk '$1=="chr20"{print $3+$4}' "$O/ilmn.roda.idxstats")"
# NISTv4.2.1/md5.in
chk "hifiasm phase-transfer VCF md5 == GIAB's" "$(md5 < hifiasm.vcf.gz)" 3b94721685d4a487ff3789eb950ebc4d
chk "hifiasm phase-transfer .tbi md5 == GIAB's" "$(md5 < hifiasm.vcf.gz.tbi)" 2c576da6a51f529a6700bfe91c8b660b
for f in bench sst hifiasm; do rec "${f}_vcf_sha256" "$(sha $f.vcf.gz)"; done

# the reference must be GRCh38's chr20, which is what both aligners used (the M5 is the one every
# GRCh38 analysis set carries; GIABv3's masking touches chr7/13/16/17/21/X, not chr20)
samtools faidx chr20.fa && cp chr20.fa.fai "$O/"
chk "chr20.fa M5 == GRCh38 chr20" "$(samtools dict chr20.fa | awk '/SN:chr20/{for(i=1;i<=NF;i++) if($i~/^M5:/) print substr($i,4)}')" b18e6c531b0bd70e949a7fc20859cb01
chk "chr20.fa length" "$(awk '$1=="chr20"{print $2}' chr20.fa.fai)" 64444167

for f in bench sst hifiasm; do
  tabix -h $f.vcf.gz chr20 | bgzip > "$O/$f.chr20.vcf.gz"
  rec "${f}_chr20_records" "$(gzip -dc "$O/$f.chr20.vcf.gz" | grep -vc '^#')"
  rec "${f}_chr20_sha256" "$(sha "$O/$f.chr20.vcf.gz")"
done

slice(){ # name url
  tic
  samtools view -@ 8 -b -o "$O/$1.chr20.bam" -X "$2" "$1.bai" chr20
  chk "$1 slice exit" "$?" 0
  toc "slice_$1"
  samtools index -@ 8 "$O/$1.chr20.bam"
  # conservation: the slice holds every record the publisher's index places on chr20
  chk "$1 slice records == GIAB index's chr20 count" \
    "$(samtools view -@ 8 -c "$O/$1.chr20.bam")" \
    "$(awk '$1=="chr20"{print $3+$4}' "$1.idxstats")"
  chk "$1 header chr20 length" "$(samtools view -H "$O/$1.chr20.bam" | awk '$1=="@SQ" && $2=="SN:chr20"{print substr($3,4)}')" 64444167
  rec "${1}_bam_bytes" "$(wc -c < "$O/$1.chr20.bam" | tr -d ' ')"
  rec "${1}_bam_sha256" "$(sha "$O/$1.chr20.bam")"
  samtools coverage -r chr20 "$O/$1.chr20.bam" > "$O/$1.coverage.tsv"
  rec "${1}_meandepth" "$(awk 'NR==2{print $7}' "$O/$1.coverage.tsv")"
  samtools stats -@ 8 "$O/$1.chr20.bam" | grep '^SN' > "$O/$1.stats.txt"
  rec "${1}_reads" "$(awk -F'\t' '$2=="raw total sequences:"{print $3}' "$O/$1.stats.txt")"
  rec "${1}_mean_read_length" "$(awk -F'\t' '$2=="average length:"{print $3}' "$O/$1.stats.txt")"
}
slice hifi "$HIFI"
slice ilmn "$ILMN"
{ ( cd "$O" && sha256sum *.chr20.bam *.chr20.bam.bai *.chr20.vcf.gz chr20.fa.fai ); sha256sum chr20.fa; } > "$W/prep.sha256"
mv "$W/prep.sha256" "$O/prep.sha256"
cat "$O/prep.sha256"
finish "PREP"
