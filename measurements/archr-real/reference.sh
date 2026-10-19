#!/usr/bin/env bash
# Runs on AWS (01-reference.task.json), never on a laptop. Fetches the two annotation files
# signac-real did not need (GRCh38 chrom sizes, ENCODE blacklist v2), checks them, and runs the
# Go recount (recount/) over signac-real's staged fragments -- once for the whole genome (ref/),
# once for chr21 + chr22 only (ref-canary/, a cheap wiring run of the same ArchR task).
set +e
set -uo pipefail
export PATH=/opt/conda/bin:/usr/local/bin:$PATH TMPDIR=/tmp HOME=/tmp LC_ALL=C
cd /tmp
mkdir -p ref ref-canary
fetch() {   # url dest -- curl if the image has it, R's libcurl otherwise
  if command -v curl >/dev/null; then curl -fsSL --retry 3 -o "$2" "$1"
  else Rscript -e 'a <- commandArgs(TRUE); options(timeout = 600); download.file(a[1], a[2], mode = "wb", method = "libcurl", quiet = TRUE)' "$1" "$2"
  fi
}
fetch https://hgdownload.soe.ucsc.edu/goldenPath/hg38/bigZips/hg38.chrom.sizes hg38.chrom.sizes || { echo "FAIL: fetch chrom sizes"; exit 1; }
fetch https://raw.githubusercontent.com/Boyle-Lab/Blacklist/v2.0/lists/hg38-blacklist.v2.bed.gz blacklist.bed.gz || { echo "FAIL: fetch blacklist"; exit 1; }
# The blacklist's git blob id at tag v2.0 is a4ec8581... (5867 B): sha1("blob 5867\0" + bytes).
if command -v sha1sum >/dev/null; then
  BL=$( { printf 'blob %d\0' "$(wc -c < blacklist.bed.gz)"; cat blacklist.bed.gz; } | sha1sum | cut -c1-40)
  [ "$BL" = a4ec858104cbe907e9056e873cec278cee11066a ] || { echo "FAIL: blacklist blob $BL != Boyle-Lab/Blacklist v2.0"; exit 1; }
  echo "blacklist matches Boyle-Lab/Blacklist v2.0 (git blob a4ec8581)"
fi
sha256sum hg38.chrom.sizes blacklist.bed.gz | tee fetched.sha256
if grep -q "^[0-9a-f]\{64\} " pins.sha256 2>/dev/null; then
  sha256sum -c pins.sha256 || { echo "FAIL: the fetched files do not match their pins"; exit 1; }
fi
# Cell Ranger's GRCh38-2020-A names: chr1..chr22, chrX, chrY, chrM. Other contigs are not read.
for c in $(seq 1 22) X Y M; do awk -v c="chr$c" '$1 == c { print $1 "\t" $2 }' hg38.chrom.sizes; done > ref/chrom.sizes
[ "$(wc -l < ref/chrom.sizes)" = 25 ] && grep -q "^chr1	248956422$" ref/chrom.sizes || { echo "FAIL: chrom sizes"; cat ref/chrom.sizes; exit 1; }
awk '$1 == "chr21" || $1 == "chr22"' ref/chrom.sizes > ref-canary/chrom.sizes
chmod +x recount
uname -m; ./recount -h 2>&1 | head -1
for d in ref ref-canary; do
  T0=$(date +%s)
  ./recount -frag fragments.tsv.gz -cells cells.txt -sizes $d/chrom.sizes -black blacklist.bed.gz \
    -genes genes.tsv -peaks peaks.bed -out $d/ > $d.log 2>&1; RC=$?
  cat $d.log; echo "recount $d rc $RC in $(( $(date +%s) - T0 )) s"
  [ "$RC" = 0 ] && grep -q '^RECOUNT OK' $d.log || { echo "FAIL: recount $d"; exit 1; }
  cp cells.txt blacklist.bed.gz $d/
done
(cd ref && sha256sum * > ../ref.sha256) && cat ref.sha256
(cd ref-canary && sha256sum * > ../ref-canary.sha256) && cat ref-canary.sha256
echo "REFERENCE OK"
