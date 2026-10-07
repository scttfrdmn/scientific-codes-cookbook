#!/usr/bin/env bash
# Stage what MetaPhlAn needs: a marker database, its bowtie2 index, a real mock-community
# metagenome, and the composition the manufacturer says is in the tube.
#
# THE DATABASE IS TWO ARTIFACTS, NOT ONE. `mpa_vJun23_CHOCOPhlAnSGB_202403.tar` (3.32 GB) is
# the MARKER SET -- a .pkl of metadata plus SGB/VSG marker FASTAs. MetaPhlAn normally runs
# bowtie2-build over it on first use, which is hours of CPU. The depositors also publish the
# built index (22.99 GB) with its own md5, so this recipe fetches THEIRS: the bytes are
# checkable at the source, and a locally built index would be unverifiable against anything.
# Both downloads happen on a box, not through your laptop (the bwa-mem2/gatk4 precedent).
#
# WHY THIS SAMPLE. MetaPhlAn answers "what is in this sample, and how much", so the honest
# check needs a sample whose answer is known before sequencing. ZymoBIOMICS is a defined
# mixture and the manufacturer publishes its composition, so the recipe asserts a *published*
# truth rather than its own internal consistency.
#
# AND IT MUST BE SHOTGUN. MetaPhlAn maps to clade-specific markers, so an AMPLICON library
# hits almost none of them. An earlier version of this recipe used ERR12736123 -- same Zymo
# standard, same platform, selected on sample_title -- which is `library_strategy=AMPLICON`,
# `library_selection=PCR`. MetaPhlAn processed all 2,514,728 reads, exited 0, and reported
# "No species were detected". That was the tool being correct about the wrong input. So the
# strategy is now ASSERTED below rather than assumed, because the failure looks exactly like
# a broken database.
set -euo pipefail

BUCKET="s3://${1:?pass your bucket -- make stage RECIPE=metaphlan does this}"
REGION="${AWS_REGION:-us-west-2}"
HERE="$(cd "$(dirname "$0")" && pwd)"
ACC=ERR15105294
IDX=mpa_vJun23_CHOCOPhlAnSGB_202403

tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT

# ---------------------------------------------------------------- 1. marker set (3.32 GB)
if aws s3 ls "$BUCKET/inputs/metaphlan/$IDX.tar" --region "$REGION" >/dev/null 2>&1; then
  echo "marker set already staged: $IDX.tar"
else
  echo "== fetching the 3.32 GB marker set on a box (~3 min, self-terminating) =="
  sed "s|\${COOKBOOK_BUCKET}|${BUCKET#s3://}|g" "$HERE/00-fetch-db.task.json" > "$tmp/db.json"
  spawn task run --spec "$tmp/db.json" --region "$REGION" --wait
fi

# ------------------------------------------------------------ 2. built bowtie2 index (23 GB)
if aws s3 ls "$BUCKET/inputs/metaphlan/${IDX}_bt2.tar" --region "$REGION" >/dev/null 2>&1; then
  echo "bowtie2 index already staged: ${IDX}_bt2.tar"
else
  echo "== fetching the 22.99 GB prebuilt bowtie2 index on a box (~20 min, ~\$0.24) =="
  sed "s|\${COOKBOOK_BUCKET}|${BUCKET#s3://}|g" "$HERE/01-fetch-bt2.task.json" > "$tmp/bt2.json"
  spawn task run --spec "$tmp/bt2.json" --region "$REGION" --wait
fi

# ------------------------------------------------------------------- 3. the reads (355 MB)
echo "== the mock-community metagenome (117 MB) =="
# ON A BOX, not here. A laptop managed ~1.5 MB/s against EBI and lost a 1.4 GB transfer to a
# truncation -- which the md5 check caught, but only after 35 wasted minutes. The same box
# that fetched the database got 18.2 MB/s. Anything multi-GB belongs in a task.
#
# The task also asserts library_strategy=WGS and selection!=PCR, because MetaPhlAn maps to
# clade-specific markers: an AMPLICON library of the same Zymo standard processes every read,
# exits 0, and reports no species. That is what an earlier version of this recipe did.
if aws s3 ls "$BUCKET/inputs/metaphlan/${ACC}_2.fastq.gz" --region "$REGION" >/dev/null 2>&1; then
  echo "  reads already staged: ${ACC}_{1,2}.fastq.gz"
else
  sed "s|\${COOKBOOK_BUCKET}|${BUCKET#s3://}|g" "$HERE/02-fetch-reads.task.json" > "$tmp/reads.json"
  spawn task run --spec "$tmp/reads.json" --region "$REGION" --wait
fi

# -------------------------------------------------------------- 4. the manufacturer's truth
echo "== the composition ZymoBIOMICS publishes for D6300 =="
# Theoretical composition by GENOMIC DNA, from the ZymoBIOMICS Microbial Community Standard
# (D6300) product documentation. Eight bacteria at 12% and two yeasts at 2%.
#
# TWO TAXONOMY TRAPS, written down because each would look like a tool failure:
#   * Lactobacillus fermentum was reclassified Limosilactobacillus fermentum (2020), and
#     MetaPhlAn 4's SGB taxonomy uses the current name.
#   * Zymo's own datasheet revisions call the Bacillus either B. subtilis or B. spizizenii.
# So the check matches on genus+species with both names accepted, not on a literal string.
cat > "$tmp/zymo_d6300.tsv" <<'EOF'
species	dna_pct	kingdom	alt_name
Listeria_monocytogenes	12.0	Bacteria	-
Pseudomonas_aeruginosa	12.0	Bacteria	-
Bacillus_subtilis	12.0	Bacteria	Bacillus_spizizenii
Escherichia_coli	12.0	Bacteria	-
Salmonella_enterica	12.0	Bacteria	-
Limosilactobacillus_fermentum	12.0	Bacteria	Lactobacillus_fermentum
Enterococcus_faecalis	12.0	Bacteria	-
Staphylococcus_aureus	12.0	Bacteria	-
Saccharomyces_cerevisiae	2.0	Eukaryota	-
Cryptococcus_neoformans	2.0	Eukaryota	-
EOF
TOT=$(awk -F'\t' 'NR>1{s+=$2} END{printf "%.1f", s}' "$tmp/zymo_d6300.tsv")
test "$TOT" = "100.0" || { echo "the truth table does not sum to 100 ($TOT)" >&2; exit 1; }
BACT=$(awk -F'\t' 'NR>1 && $3=="Bacteria"{n++} END{print n}' "$tmp/zymo_d6300.tsv")
echo "  $BACT bacterial species at 12% each, 2 yeasts at 2%, sums to $TOT"
aws s3 cp "$tmp/zymo_d6300.tsv" "$BUCKET/inputs/metaphlan/zymo_d6300.tsv" --region "$REGION" --only-show-errors

echo "done."
echo "  $BUCKET/inputs/metaphlan/$IDX.tar            (3.32 GB markers)"
echo "  $BUCKET/inputs/metaphlan/${IDX}_bt2.tar      (22.99 GB bowtie2 index)"
echo "  $BUCKET/inputs/metaphlan/${ACC}_{1,2}.fastq.gz  (117 MB, 1,588,558 shotgun reads)"
echo "  $BUCKET/inputs/metaphlan/zymo_d6300.tsv      (the published composition)"
