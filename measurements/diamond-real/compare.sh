#!/usr/bin/env bash
# measurements/diamond-real task 2: compare the three chips' hit tables (and Graviton4's two runs),
# which are too big to bring home. Reads each run's hits.tsv.gz and self.txt (staged as
# /tmp/<run>.hits.tsv.gz, /tmp/<run>.self.txt) and reports, for each pair: (query, subject) pairs in both and in
# one only, queries whose subject set differs, and, over the shared pairs, rows whose every value
# (pident, length, qlen, slen, evalue, bitscore) is identical.
#   compare.sh
set +e
set -uo pipefail
export PATH=/usr/local/bin:/opt/conda/bin:$PATH HOME=/tmp TMPDIR=/var/tmp
O=/tmp/out; W=/var/tmp/cmp
mkdir -p $O $W
for c in arm64 arm64run1 x86_64 amd; do
  gzip -dc /tmp/$c.hits.tsv.gz | LC_ALL=C sort > $W/$c.tsv
  cut -f1,2 $W/$c.tsv > $W/$c.pairs
  echo "$c rows $(wc -l < $W/$c.tsv | tr -d ' ') sha256 $(gzip -dc /tmp/$c.hits.tsv.gz | sha256sum | cut -d' ' -f1)"
done | tee $O/compare.txt
pair() {
  a=$1; b=$2
  both=$(LC_ALL=C comm -12 $W/$a.pairs $W/$b.pairs | wc -l | tr -d ' ')
  oa=$(LC_ALL=C comm -23 $W/$a.pairs $W/$b.pairs | wc -l | tr -d ' ')
  ob=$(LC_ALL=C comm -13 $W/$a.pairs $W/$b.pairs | wc -l | tr -d ' ')
  qd=$( { LC_ALL=C comm -23 $W/$a.pairs $W/$b.pairs; LC_ALL=C comm -13 $W/$a.pairs $W/$b.pairs; } | cut -f1 | LC_ALL=C sort -u | wc -l | tr -d ' ')
  same=$(LC_ALL=C comm -12 $W/$a.tsv $W/$b.tsv | wc -l | tr -d ' ')
  sa=$(LC_ALL=C comm -23 $W/$a.self.s $W/$b.self.s | wc -l | tr -d ' ')
  sb=$(LC_ALL=C comm -13 $W/$a.self.s $W/$b.self.s | wc -l | tr -d ' ')
  echo "$a-vs-$b pairs_both $both only_$a $oa only_$b $ob queries_differing $qd identical_rows $same self_only_$a $sa self_only_$b $sb"
}
for c in arm64 arm64run1 x86_64 amd; do LC_ALL=C sort /tmp/$c.self.txt > $W/$c.self.s; done
{ pair arm64 arm64run1; pair arm64 x86_64; pair arm64 amd; pair x86_64 amd; } | tee -a $O/compare.txt
echo "COMPARE DONE" | tee -a $O/compare.txt
