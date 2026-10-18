#!/usr/bin/env bash
# Sizing probe: hash every staged FASTQ, then time FastQC and the recount on ONE whole file
# (ERR188026_1, 15.8M x 75 bp) with memory sampled. Everything kept goes under /tmp/out.
set +e
set -uo pipefail
export PATH=/opt/conda/bin:/usr/local/bin:$PATH HOME=/tmp TMPDIR=/tmp
mkdir -p /tmp/out /tmp/work
cd /tmp/fq
{ echo "arch	$(uname -m)"; echo "nproc	$(nproc)"; lscpu 2>/dev/null | grep -E '^(Model name|Thread)' ; df -k /tmp | tail -1; } > /tmp/out/host.txt
T0=$(date +%s)
for f in *.fastq.gz; do ( sha256sum "$f" > /tmp/work/$f.sha256; md5sum "$f" > /tmp/work/$f.md5 ) & done
wait
cat /tmp/work/*.sha256 > /tmp/out/inputs.sha256; cat /tmp/work/*.md5 > /tmp/out/inputs.md5
echo "hash_s	$(( $(date +%s) - T0 ))" >> /tmp/out/host.txt
( cd /tmp/fq && md5sum -c /tmp/ena.md5 ) > /tmp/out/ena-md5-check.txt 2>&1; echo "ena_md5_rc	$?" >> /tmp/out/host.txt
CG=/sys/fs/cgroup
( while :; do echo "$(date +%s) $(awk '/^anon /{print $2; exit}' $CG/memory.stat)"; sleep 2; done ) > /tmp/out/mem.txt & SP=$!
T0=$(date +%s)
fastqc -t 1 --extract -o /tmp/work ERR188026_1.fastq.gz > /tmp/out/fastqc.log 2>&1; echo "fastqc_rc	$?" >> /tmp/out/host.txt
echo "fastqc_s	$(( $(date +%s) - T0 ))" >> /tmp/out/host.txt
kill $SP
echo "fastqc_peak_anon_bytes	$(awk '$2>m{m=$2} END{print m}' /tmp/out/mem.txt)" >> /tmp/out/host.txt
cp /tmp/work/ERR188026_1_fastqc/fastqc_data.txt /tmp/out/
T0=$(date +%s)
gzip -dc ERR188026_1.fastq.gz | perl /tmp/recount.pl ERR188026_1 > /tmp/out/recount.tsv
echo "recount_s	$(( $(date +%s) - T0 ))" >> /tmp/out/host.txt
cat /tmp/out/host.txt /tmp/out/recount.tsv /tmp/out/inputs.sha256
head -12 /tmp/out/fastqc_data.txt
