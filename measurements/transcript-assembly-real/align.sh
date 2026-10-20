#!/usr/bin/env bash
# measurements/transcript-assembly-real task 0 (hisat2 image, Graviton4, run once): build the
# alignment every StringTie run reads. A HISAT2 index of the whole Ensembl 116 GRCh38 primary
# assembly (genome only, no annotation: junctions are discovered, as in the recipe), then the
# complete ERR188026 run aligned with --dta, the setting the HISAT2/StringTie protocol uses for
# downstream assembly. --reorder keeps SAM order independent of thread scheduling.
#   align.sh <threads>
set +e
set -uo pipefail
T=${1:?threads}
export PATH=/opt/conda/bin:$PATH HOME=/tmp TMPDIR=/tmp
O=/tmp/out; I=/tmp/idx; S=/tmp/sam; W=/var/tmp/ta
mkdir -p $O $I $S $W
fail() { echo "FAIL: $*" | tee -a $O/pins.txt; exit 1; }
{
  echo "uname_m	$(uname -m)"
  echo "nproc	$(nproc)"
  echo "threads_per_core	$(awk -F'[,-]' '{print NF}' /sys/devices/system/cpu/cpu0/topology/thread_siblings_list 2>/dev/null)"
  grep -m1 -E '^CPU part' /proc/cpuinfo | sed 's/^CPU part[^:]*: */cpu_part	/'
  echo "mem_total_kb	$(awk '/MemTotal/{print $2}' /proc/meminfo)"
  echo "hisat2	$(hisat2 --version 2>&1 | head -n 1)"
} > $O/host.txt 2>&1
cat $O/host.txt

echo "== staged inputs against their publishers' checksums =="
M1=$(md5sum /tmp/ERR188026_1.fastq.gz | cut -d' ' -f1)
M2=$(md5sum /tmp/ERR188026_2.fastq.gz | cut -d' ' -f1)
FS=$(sum /tmp/GRCh38.primary_assembly.fa.gz | awk '{print $1, $2}')
{
  echo "ERR188026_1.fastq.gz md5 $M1 want 5fd42c5e9889ac6f30259c04848c8ffb (ENA)"
  echo "ERR188026_2.fastq.gz md5 $M2 want 2f03cfb4d272170d0d6451523e37569c (ENA)"
  echo "Homo_sapiens.GRCh38.dna.primary_assembly.fa.gz sum $FS want 22450 861294 (Ensembl release-116 CHECKSUMS)"
} > $O/pins.txt
cat $O/pins.txt
[ "$M1" = 5fd42c5e9889ac6f30259c04848c8ffb ] || fail "mate 1 md5"
[ "$M2" = 2f03cfb4d272170d0d6451523e37569c ] || fail "mate 2 md5"
[ "$FS" = "22450 861294" ] || fail "genome sum"

gzip -dc /tmp/GRCh38.primary_assembly.fa.gz > $W/genome.fa || fail "gunzip genome"
echo "== hisat2-build: the whole primary assembly, $T threads =="
python3 -u /tmp/timed.py $O/timing.txt build $O/hisat2-build.log -- \
  hisat2-build -p "$T" $W/genome.fa $I/GRCh38_116 || { tail -n 30 $O/hisat2-build.log; fail "hisat2-build"; }
rm -f $W/genome.fa
( cd $I && sha256sum GRCh38_116.*.ht2 ) > $O/index.sha256
cat $O/index.sha256

echo "== hisat2 --dta: the complete run, $T threads =="
python3 -u /tmp/timed.py $O/timing.txt align $O/hisat2.log -- \
  hisat2 -p "$T" --dta --reorder -x $I/GRCh38_116 \
    -1 /tmp/ERR188026_1.fastq.gz -2 /tmp/ERR188026_2.fastq.gz \
    --rg-id ERR188026 --rg SM:NA20510 --new-summary --summary-file $O/hisat2.summary \
    -S $S/aln.sam || { tail -n 30 $O/hisat2.log; fail "hisat2"; }
cat $O/hisat2.summary
PAIRS=$(awk -F': ' '/Total pairs/{print $2+0}' $O/hisat2.summary)
echo "hisat2_total_pairs $PAIRS want 15800127 (ENA read_count)" | tee -a $O/pins.txt
[ "$PAIRS" = 15800127 ] || fail "hisat2 read $PAIRS pairs"
( cd $S && sha256sum aln.sam ) > $O/sam.sha256
cat $O/sam.sha256
echo "ALIGN OK"
