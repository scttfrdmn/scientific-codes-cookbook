#!/usr/bin/env bash
# measurements/structural-variants-real 00-prep, in the recipe's samtools image (arm64, once).
# Derives the one BAM both callers read, on every chip:
#   - NHGRI's HG002.hs37d5.60x.1.bam subsampled to 30x with a fixed seed (`-s 42.5` hashes the read
#     name, so mates stay together and the subsample is the same on every run);
#   - the read group delly requires (the published BAM has none) and SO:coordinate in @HD (the
#     published header says `unsorted`; `samtools index` succeeding is what shows it is sorted);
# and the reference: hs37d5.fa.gz (RAZF, which samtools faidx cannot index) decompressed and indexed.
# Writes the sha256 of every source and every derived file to derived/inputs.sha256.
#   prep.sh <threads>
set +e
set -uo pipefail
T=${1:?threads}
export PATH=/opt/conda/bin:$PATH HOME=/tmp TMPDIR=/var/tmp
O=/tmp/out; D=/var/tmp/derived
mkdir -p $O $D
fail() { echo "FAIL: $*" | tee -a $O/prep-check.txt; exit 1; }
cd /tmp
{
  echo "uname_m	$(uname -m)"; echo "nproc	$(nproc)"
  echo "mem_total_kb	$(awk '/^MemTotal/{print $2}' /proc/meminfo)"
  echo "samtools	$(samtools --version | head -n1)"
} > $O/host.txt
cat $O/host.txt

echo "== sha256 of every source (in the background, while the reference decompresses) =="
SRC="HG002.hs37d5.60x.1.bam HG002.hs37d5.60x.1.bam.bai hs37d5.fa.gz hs37d5.fa.gz.fai
  HG002_SVs_Tier1_v0.6.vcf.gz HG002_SVs_Tier1_v0.6.vcf.gz.tbi HG002_SVs_Tier1_v0.6.bed human.hg19.excl.tsv"
( for f in $SRC; do sha256sum $f; done > $O/sources.sha256 ) &
SHAPID=$!

echo "== reference =="
t0=$(date +%s)
# RAZF ends in its own index, which gzip reports as "trailing garbage" with exit 2; the fai check
# below is what proves the sequence is whole.
gzip -dc hs37d5.fa.gz > $D/hs37d5.fa
GZ=$?
[ "$GZ" = 0 ] || [ "$GZ" = 2 ] || fail "hs37d5.fa.gz does not decompress (gzip exit $GZ)"
samtools faidx $D/hs37d5.fa || fail "faidx"
# Names and lengths must equal the fai 1000 Genomes publishes beside the file (offsets differ:
# theirs indexes the RAZF).
cut -f1,2 $D/hs37d5.fa.fai > ours.tsv; cut -f1,2 hs37d5.fa.gz.fai > theirs.tsv
cmp -s ours.tsv theirs.tsv || fail "hs37d5.fa names/lengths differ from the published .fai"
NSEQ=$(wc -l < ours.tsv | tr -d ' '); GLEN=$(awk '{s+=$2} END{print s}' ours.tsv)
# The BAM was aligned to this reference: its @SQ names and lengths must be the same 86 contigs.
samtools view -H HG002.hs37d5.60x.1.bam > hdr60.sam || fail "BAM header"
awk -F'\t' '$1=="@SQ"{n="";l=""; for(i=2;i<=NF;i++){if($i~/^SN:/)n=substr($i,4); if($i~/^LN:/)l=substr($i,4)} print n "\t" l}' \
  hdr60.sam > bamsq.tsv
cmp -s bamsq.tsv ours.tsv || fail "BAM @SQ differs from hs37d5.fa"
echo "ref_s	$(( $(date +%s) - t0 ))" >> $O/timing.txt

echo "== 30x: subsample, read group, header =="
grep -c '^@RG' hdr60.sam > rg60.txt
sed '1s/SO:unsorted/SO:coordinate/' hdr60.sam > hdr30.sam
head -n1 hdr60.sam | grep -q 'SO:unsorted' || fail "the published @HD no longer says SO:unsorted"
printf '@RG\tID:HG002\tSM:HG002\tPL:ILLUMINA\tDS:HG002.hs37d5.60x.1.bam subsampled -s 42.5\n' >> hdr30.sam
BAM=$D/HG002.hs37d5.30x.bam
t0=$(date +%s)
samtools view -@ $T -u -s 42.5 HG002.hs37d5.60x.1.bam \
  | samtools reheader hdr30.sam - \
  | samtools addreplacerg -@ $T -R HG002 -O BAM -o $BAM -
RC="${PIPESTATUS[*]}"
echo "subsample_s	$(( $(date +%s) - t0 ))" >> $O/timing.txt
[ "$RC" = "0 0 0" ] || fail "subsample pipeline exit codes $RC"
t0=$(date +%s)
samtools index -@ $T $BAM || fail "samtools index: the 30x BAM is not coordinate-sorted"
echo "index_s	$(( $(date +%s) - t0 ))" >> $O/timing.txt

echo "== counts =="
samtools idxstats HG002.hs37d5.60x.1.bam > $O/idxstats60.tsv
samtools idxstats $BAM > $O/idxstats30.tsv
M60=$(awk '{s+=$3} END{print s}' $O/idxstats60.tsv); U60=$(awk '{s+=$4} END{print s}' $O/idxstats60.tsv)
M30=$(awk '{s+=$3} END{print s}' $O/idxstats30.tsv); U30=$(awk '{s+=$4} END{print s}' $O/idxstats30.tsv)
RL=$(samtools view $BAM 1:10000000-10100000 | awk 'NR<=100000{s+=length($10); n++} END{printf "%.1f", s/n}')
RGN=$(samtools view -H $BAM | grep -c '^@RG')
SO=$(samtools view -H $BAM | awk 'NR==1' | cut -f3)
NOTAG=$(samtools view $BAM 20:30000000-30100000 | grep -vc 'RG:Z:HG002')
wait $SHAPID
( cd $D && sha256sum HG002.hs37d5.30x.bam HG002.hs37d5.30x.bam.bai hs37d5.fa hs37d5.fa.fai ) > $O/derived.sha256
cat $O/sources.sha256 $O/derived.sha256 > $D/inputs.sha256
{
  echo "ref_sequences	$NSEQ"
  echo "ref_length	$GLEN"
  echo "bam60_read_groups	$(cat rg60.txt)"
  echo "bam60_mapped	$M60"
  echo "bam60_unmapped	$U60"
  echo "bam30_mapped	$M30"
  echo "bam30_unmapped	$U30"
  echo "kept_fraction	$(awk -v a=$M30 -v b=$U30 -v c=$M60 -v d=$U60 'BEGIN{printf "%.6f", (a+b)/(c+d)}')"
  echo "read_length_mean	$RL"
  echo "bam30_mean_depth	$(awk -v m=$M30 -v r=$RL -v g=$GLEN 'BEGIN{printf "%.2f", m*r/g}')"
  echo "bam30_read_groups	$RGN"
  echo "bam30_sort_order	$SO"
  echo "bam30_reads_without_rg_20:30.0-30.1M	$NOTAG"
  echo "bam30_bytes	$(stat -c%s $BAM)"
} >> $O/prep-check.txt
cat $O/prep-check.txt $D/inputs.sha256
[ "$NSEQ" = 86 ] || fail "hs37d5 has $NSEQ sequences, not 86"
[ "$RGN" = 1 ] && [ "$SO" = "SO:coordinate" ] && [ "$NOTAG" = 0 ] || fail "read group / sort order"
# -s keeps each read NAME with probability 0.5 by hash, over ~1e9 records: sd ~2e-5, so 0.005 is 250 sd.
awk -v a=$M30 -v b=$U30 -v c=$M60 -v d=$U60 'BEGIN{f=(a+b)/(c+d); exit !(f>0.495 && f<0.505)}' \
  || fail "subsample kept a fraction other than one half"
grep -q '^[0-9a-f]\{64\}  HG002.hs37d5.60x.1.bam$' $O/sources.sha256 || fail "source sha256 missing"
cp $D/inputs.sha256 $O/
echo "PREP CHECK PASSED" | tee -a $O/prep-check.txt
