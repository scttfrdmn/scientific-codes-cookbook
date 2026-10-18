#!/usr/bin/env bash
# Runs on AWS (01-reference.task.json), never on a laptop. Fetches the dataset into the task,
# checks it against its pins, derives the cell list, annotation table and gene windows, and
# recounts everything the Signac task asserts -- in POSIX awk, with no Signac, R or htslib --
# so each chip's Signac run is compared with numbers that do not come from Signac.
set +e
set -uo pipefail
export PATH=/opt/conda/bin:/usr/local/bin:$PATH TMPDIR=/tmp HOME=/tmp LC_ALL=C
cd /tmp
mkdir -p /tmp/ref
X=https://cf.10xgenomics.com/samples/cell-atac/2.1.0/10k_pbmc_ATACv2_nextgem_Chromium_Controller/10k_pbmc_ATACv2_nextgem_Chromium_Controller
G=https://ftp.ensembl.org/pub/release-98/gtf/homo_sapiens/Homo_sapiens.GRCh38.98.gtf.gz
echo "arch $(uname -m)   awk: $(command -v awk) $(readlink -f "$(command -v awk)" 2>/dev/null)"

fetch() {   # url dest -- curl if the image has it, R's libcurl otherwise
  if command -v curl >/dev/null; then curl -fsSL --retry 3 -o "$2" "$1"
  else Rscript -e 'a <- commandArgs(TRUE); options(timeout = 7200); download.file(a[1], a[2], mode = "wb", method = "libcurl", quiet = TRUE)' "$1" "$2"
  fi
}
T0=$(date +%s)
for f in fragments.tsv.gz fragments.tsv.gz.tbi singlecell.csv peaks.bed; do
  fetch "${X}_$f" "/tmp/$f" || { echo "FAIL: fetch $f"; exit 1; }
done
fetch "$G" /tmp/genes.gtf.gz || { echo "FAIL: fetch the GTF"; exit 1; }
echo "fetch_s $(( $(date +%s) - T0 ))"
ls -l fragments.tsv.gz fragments.tsv.gz.tbi singlecell.csv peaks.bed genes.gtf.gz

# Ensembl's own CHECKSUMS file gives the GTF as BSD sum "47563 45618"; sha256 pins all five.
if command -v sum >/dev/null; then
  BSD=$(sum genes.gtf.gz | awk '{print $1, $2}')
  [ "$BSD" = "47563 45618" ] || { echo "FAIL: GTF BSD sum $BSD != Ensembl CHECKSUMS 47563 45618"; exit 1; }
  echo "GTF matches Ensembl release-98 CHECKSUMS (47563 45618)"
fi
sha256sum fragments.tsv.gz fragments.tsv.gz.tbi singlecell.csv peaks.bed genes.gtf.gz | tee data.sha256
if grep -q "^[0-9a-f]\{64\} " /tmp/pins.sha256 2>/dev/null; then
  sha256sum -c /tmp/pins.sha256 || { echo "FAIL: the fetched data does not match its pins"; exit 1; }
fi

# Cells: Cell Ranger ATAC's own call (is__cell_barcode == 1), sorted -- the order fixes v_j.
awk -F, 'NR == 1 { for (i = 1; i <= NF; i++) if ($i == "is__cell_barcode") c = i; next }
         $c == 1 { print $1 }' singlecell.csv | sort > cells.txt
NC=$(wc -l < cells.txt); N=$(( NC * 5000 ))
echo "cells $NC   NucleosomeSignal reads the first $N records"
gzip -dc genes.gtf.gz | awk -f gtf.awk > genes.tsv
awk -f windows.awk genes.tsv > windows.bed
awk 'BEGIN { OFS = "\t" } !/^#/ { print $1, $2, $3, ++i }' peaks.bed | sort -k1,1 -k2,2n > peaks.sorted.bed
cut -f1-4 windows.bed | sort -k1,1 -k2,2n > windows.sorted.bed
echo "annotation $(( $(wc -l < genes.tsv) - 1 )) exon rows; $(wc -l < windows.bed) gene windows; $(wc -l < peaks.sorted.bed) peaks"

# Two independent streaming passes, concurrently.
T1=$(date +%s)
( gzip -dc fragments.tsv.gz | awk -v N="$N" -v OUT=/tmp/ref/ -f bins.awk cells.txt - > bins.log 2>&1; echo "rc ${PIPESTATUS[*]}" >> bins.log ) &
( gzip -dc fragments.tsv.gz | awk -v OUT=/tmp/ref/ -f overlap.awk cells.txt peaks.sorted.bed windows.sorted.bed - > overlap.log 2>&1; echo "rc ${PIPESTATUS[*]}" >> overlap.log ) &
wait
cat bins.log overlap.log
echo "recount_s $(( $(date +%s) - T1 ))"
grep -q "^rc 0 0$" bins.log && grep -q "^rc 0 0$" overlap.log || { echo "FAIL: a recount pass failed"; exit 1; }
for f in barcodes.tsv nfirst.tsv rows_1.tsv cols_1.tsv rows_2.tsv cols_2.tsv stats.tsv peakfrag.tsv; do
  test -s "ref/$f" || { echo "FAIL: ref/$f is empty"; exit 1; }
done
cat ref/stats.tsv

# Cell Ranger's own per-barcode numbers, against the recount (reported; the task decides nothing
# from them, the Signac task's assertions are against the recount).
awk -F'[,\t]' 'FNR == 1 { fi++ }
  fi == 1 { rec[$1] = $2; next }
  fi == 2 { pf[$1] = $2; next }
  FNR == 1 { for (i = 1; i <= NF; i++) h[$i] = i; next }
  $1 != "NO_BARCODE" { nb++
    if (rec[$1] + 0 == $h["passed_filters"]) a++
    if (pf[$1] + 0 == $h["peak_region_fragments"]) b++
    if ($h["is__cell_barcode"] == 1) { nc++; if (rec[$1] + 0 == $h["passed_filters"]) ca++; if (pf[$1] + 0 == $h["peak_region_fragments"]) cb++ } }
  END { printf "cellranger\tbarcodes %d: records == passed_filters %d, peak fragments == peak_region_fragments %d\n", nb, a, b
        printf "cellranger\tcells %d: records == passed_filters %d, peak fragments == peak_region_fragments %d\n", nc, ca, cb }' \
  ref/barcodes.tsv ref/peakfrag.tsv singlecell.csv
cp cells.txt genes.tsv windows.bed data.sha256 ref/
(cd ref && sha256sum * > ../ref.sha256) && cat ref.sha256
echo "REFERENCE OK"
