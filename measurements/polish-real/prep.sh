set +e
# spawn runs task commands under bash -e; `set -uo pipefail` does not clear it (spawn#707).
set -uo pipefail
export PATH=/opt/conda/bin:/usr/local/bin:$PATH
# The reads: ERR12813217 ("ecoli_dorado053", Sanderson et al. 2024, PRJEB51164), E. coli CFT073 on
# R10.4.1 / Kit 14, Dorado 0.5.3 dna_r10.4.1_e8.2_400bps_sup@v4.3.0. The .sra is the AWS Open Data
# SRA mirror's (s3://sra-pub-run-odp), copied server-side by stage-inputs.sh; the dump is tied back
# to the archive by ENA's own counts, so a truncated dump cannot pass.
ACC=ERR12813217 WANT_READS=397033 WANT_BASES=2544407444
O=/var/tmp/out; mkdir -p $O; W=/var/tmp/w; mkdir -p $W
cd /tmp
sha256sum $ACC.sra | tee $O/sra.sha256
fasterq-dump --version 2>&1 | grep -m1 . | tee $O/prep-versions.txt
T0=$(date +%s)
fasterq-dump --threads 4 --temp $W/tmp -O $W ./$ACC.sra > $O/fasterq-dump.log 2>&1
RC=$?; T1=$(date +%s)
ls -l $W | tee $O/prep-files.txt
FQ=$(ls $W/*.fastq | head -1)
read -r R B Q <<<"$(awk 'NR%4==2{b+=length($0)} NR%4==0 && NR<=400000{for(i=1;i<=length($0);i++) q[substr($0,i,1)]=1}
  END{n=0; for(k in q) n++; printf "%.0f %.0f %d", NR/4, b, n}' "$FQ")"
gzip -c "$FQ" > $O/$ACC.fastq.gz
gzip -t $O/$ACC.fastq.gz; GZ=$?
{
  echo "fasterq_dump_rc $RC wall_s $((T1-T0))"
  echo "reads $R want $WANT_READS"
  echo "bases $B want $WANT_BASES"
  echo "quality_symbols_first100k $Q"
  echo "gzip_test_rc $GZ"
} | tee $O/prep-checks.txt
(cd $O && sha256sum $ACC.fastq.gz) | tee $O/fastq.sha256
rm -rf $W
[ "$RC" = 0 ] && [ "$R" = "$WANT_READS" ] && [ "$B" = "$WANT_BASES" ] && [ "$Q" -gt 1 ] && [ "$GZ" = 0 ] \
  || { echo "FAIL: the dump does not match ENA's read/base counts"; exit 1; }
echo "PREP OK"
