#!/usr/bin/env bash
# Stage 20 complete RefSeq bacterial genomes: 10 species, 2 strains each.
#
# WHY THIS SET. mash's job is "how similar are these genomes", so the honest check is
# whether its distances recover a relationship established independently. Two strains
# per species gives exactly that: for each genome, the nearest of the other 19 must be
# its own species' second strain. 20 independent tests, no band, and the truth comes
# from NCBI's own records rather than from mash.
#
# The 10 species are from 10 different genera, well separated. Close pairs (E. coli /
# Shigella, the Klebsiella complex) would make the nearest-neighbour test a coin flip on
# real biology rather than a check on the tool -- a flaky check teaches people to ignore
# failures, so the input is chosen to make the assertion mean something.
#
# THE SPECIES LABEL COMES FROM THE GENOME, NOT FROM THIS FILE. An earlier version of this
# script carried hand-written labels next to each accession, and two of twenty were wrong:
# GCF_000008465.1 is Idiomarina loihiensis, not Helicobacter pylori J99, and
# GCF_000215705.1 is Ramlibacter tataouinensis, not a second Listeria. Both would have
# produced a nearest-neighbour test that failed for a reason having nothing to do with
# mash. So the label is parsed from the FASTA header -- the genome states its own
# organism -- and the two-strains-per-species invariant is asserted here, at staging
# time, where a wrong accession is a loud error instead of a poisoned science check.
#
# PINNING. A RefSeq assembly accession is versioned and immutable (GCF_000005845.2 will
# always be that assembly), so the accession is the durable id. The directory name under
# genomes/all/ also carries the assembly NAME, which is resolved from NCBI's index rather
# than hardcoded -- guessing "ASM584v2" is how a staging script rots.
set -euo pipefail

BUCKET="${1:?pass your bucket -- make stage RECIPE=NAME does this}"
PREFIX="inputs/genomes20"
BASE="https://ftp.ncbi.nlm.nih.gov/genomes/all"

ACCESSIONS="
GCF_000005845.2 GCF_000008865.2
GCF_000006945.2 GCF_000195995.1
GCF_000006765.1 GCF_000014625.1
GCF_000009045.1 GCF_000186745.1
GCF_000013425.1 GCF_000013465.1
GCF_000196035.1 GCF_000093125.2
GCF_000009085.1 GCF_000011865.1
GCF_000008525.1 GCF_000008785.1
GCF_000006745.1 GCF_000022585.1
GCF_000195955.2 GCF_000008585.1
"

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
cd "$WORK"
: > labels.tsv

for acc in $ACCESSIONS; do
  # genomes/all/GCF/000/005/845/  <- the accession's digits in groups of three
  digits="${acc#GCF_}"; digits="${digits%%.*}"
  dir="$BASE/GCF/${digits:0:3}/${digits:3:3}/${digits:6:3}"
  # `| head -1` would SIGPIPE curl under `pipefail` (exit 141); awk reads to EOF.
  asm=$(curl -sSf "$dir/" | grep -oE "${acc}_[A-Za-z0-9._-]+/" | awk 'NR==1{print}' | tr -d '/')
  [ -n "$asm" ] || { echo "could not resolve a directory for $acc under $dir" >&2; exit 1; }
  curl -sSf -o "$acc.fna.gz" "$dir/$asm/${asm}_genomic.fna.gz"

  # Genus + species from the first defline (">NC_000913.3 Escherichia coli str. K-12 ...")
  # and the base count, in ONE pass that reads to EOF. An `exit` after NR==1 would
  # SIGPIPE gzip, and under `pipefail` that is exit 141 -- a staging script killed by
  # its own optimisation.
  read -r species bp <<<"$(gzip -dc "$acc.fna.gz" \
    | awk 'NR==1{s=$2"_"$3} !/^>/{n+=length($0)} END{printf "%s %d", s, n}')"
  # A genome that arrived truncated would still sketch and still give a plausible distance.
  [ "$bp" -gt 1000000 ] || { echo "$acc is only $bp bp -- truncated?" >&2; exit 1; }
  printf '%s\t%s\t%s\t%s\n' "$acc" "$species" "$asm" "$bp" >> labels.tsv
  echo "  $acc  $species  $asm  ${bp} bp"
done

N=$(awk 'END{print NR}' labels.tsv)
SPECIES=$(awk -F'\t' '{print $2}' labels.tsv | sort -u | wc -l | tr -d ' ')
[ "$N" -eq 20 ] || { echo "got $N genomes, wanted 20" >&2; exit 1; }
[ "$SPECIES" -eq 10 ] || { echo "got $SPECIES distinct species, wanted 10" >&2; exit 1; }
# Exactly two strains per species is what makes the nearest-neighbour test unambiguous.
awk -F'\t' '{c[$2]++} END{bad=0; for (s in c) if (c[s] != 2) { printf "%s has %d strain(s), wanted 2\n", s, c[s] > "/dev/stderr"; bad=1 } exit bad}' labels.tsv

tar -cf genomes20.tar labels.tsv ./*.fna.gz
aws s3 cp genomes20.tar "s3://$BUCKET/$PREFIX/genomes20.tar" --only-show-errors
echo "--- pin (record in README.md):"
sha256sum genomes20.tar
echo "--- $N genomes, $SPECIES species, 2 strains each"
awk -F'\t' '{print "    "$2"\t"$1}' labels.tsv | sort
