#!/usr/bin/env bash
# foldseek-real cross-chip comparison, run on AWS by compare.task.json (the alignment tables are
# 0.8 GB each uncompressed, so they are compared where they are, not downloaded).
#
#   bash compare.sh <ref.tsv.gz> <other.tsv.gz> <tag>     # both sorted by real.sh (LC_ALL=C sort)
#
# Writes /tmp/out/<tag>.txt: rows in each, rows byte-identical, pairs found by only one side (with
# their E-values: the -e 10 cutoff is where a last-digit E-value difference decides membership),
# and, for pairs both found, how many differ in each column.
set +e
set -uo pipefail
export LC_ALL=C TMPDIR=/var/tmp
W=/var/tmp/cmp; O=/tmp/out
mkdir -p "$W" "$O"
A=$W/a.tsv; X=$W/x.tsv
gzip -dc "$1" > "$A"; gzip -dc "$2" > "$X"
R=$O/$3.txt
{
  echo "ref   $1 rows $(wc -l < "$A" | tr -d ' ')"
  echo "other $2 rows $(wc -l < "$X" | tr -d ' ')"
  echo "rows byte-identical $(comm -12 "$A" "$X" | wc -l | tr -d ' ')"
  comm -3 "$A" "$X" > "$W/d.tsv"
  echo "rows differing (either side) $(wc -l < "$W/d.tsv" | tr -d ' ')"
  # comm -3 prefixes the second file's lines with a tab: column 1 empty marks "other"
  awk -F'\t' -v OFS='\t' '
    { if ($1 == "") { side="x"; $0=substr($0,2) } else side="a"
      split($0, f, "\t"); p=f[1] "\t" f[2]
      row[side, p]=$0; has[side, p]=1; pairs[p]=1 }
    END {
      split("query target fident alnlen mismatch gapopen qstart qend tstart tend qlen tlen evalue bits alntmscore qtmscore ttmscore lddt prob", name, " ")
      for (p in pairs) {
        if (has["a", p] && has["x", p]) {
          both++; split(row["a", p], fa, "\t"); split(row["x", p], fx, "\t")
          for (c = 3; c <= 19; c++) if (fa[c] != fx[c]) diff[c]++
        } else if (has["a", p]) { split(row["a", p], fa, "\t"); onlya++; print "only_ref", row["a", p] > "/tmp/out/'"$3"'.only.tsv"; if (fa[13]+0 < mina || mina == "") mina = fa[13]+0 }
        else { split(row["x", p], fx, "\t"); onlyx++; print "only_other", row["x", p] > "/tmp/out/'"$3"'.only.tsv"; if (fx[13]+0 < minx || minx == "") minx = fx[13]+0 }
      }
      printf "pairs found by both, differing in some column %d\n", both
      printf "pairs found only by ref %d (smallest E-value %s)\n", onlya, mina
      printf "pairs found only by other %d (smallest E-value %s)\n", onlyx, minx
      for (c = 3; c <= 19; c++) printf "  column %-10s differs in %d\n", name[c], diff[c] + 0
    }' "$W/d.tsv"
} | tee "$R"
rm -rf "$W"
