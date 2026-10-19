#!/usr/bin/env bash
# humann-real, task 1 (HUMAnN image): one whole shotgun run through HUMAnN 3.9 against the full
# ChocoPhlAn and UniRef90 databases, guided by MetaPhlAn's profile of the same reads.
#
#   bash run.sh <threads> <accession> <pairs: 0 = all> <truth.tsv> [rawids]
#
# Needs /tmp/<acc>_{1,2}.fastq.gz, /tmp/<acc>.profile.txt, /tmp/db/{chocophlan,uniref}/ and
# /tmp/db.sha256. `rawids` (the canary) also runs nucleotide-only with and without unique mate IDs.
# Runs on aarch.bio's arm64 image and bioconda's x86 one (BusyBox userland), so the glue is POSIX
# sh/awk plus python3 from the image. Writes /tmp/out/<acc>/.
set +e
set -uo pipefail
export PATH=/opt/conda/bin:/usr/local/bin:$PATH
export TMPDIR=/var/tmp HOME=/tmp
T=$1 ACC=$2 PAIRS=$3 TRUTH=$4 EXTRA=${5:-}
O=/tmp/out/$ACC W=/var/tmp/w
mkdir -p "$O" "$W"
cd /tmp

echo "== host ==" | tee "$O/host.txt"
{
  echo "uname_m $(uname -m)"
  echo "online_cpus $(grep -c '^processor' /proc/cpuinfo)"
  echo "physical_cores $(cat /sys/devices/system/cpu/cpu*/topology/thread_siblings_list | sort -u | wc -l)"
  awk -F': *' '/^model name|^CPU part|^CPU implementer/{print $1": "$2}' /proc/cpuinfo | sort | uniq -c
  echo "mem_total_kib $(awk '/^MemTotal/{print $2}' /proc/meminfo)"
  echo "humann $(humann --version 2>&1 | tail -1)"
  echo "bowtie2 $(bowtie2 --version 2>&1 | head -1)"
  echo "diamond $(diamond --version 2>&1 | head -1)"
  echo "metaphlan_version_output $(metaphlan --version 2>&1 | tr '\n' '|')"
} | tee -a "$O/host.txt"
awk '/^online_cpus/{c=$2} /^physical_cores/{p=$2} END{printf "threads_per_core %d\n", (p>0)?c/p:0}' "$O/host.txt" | tee -a "$O/host.txt"
df -k /tmp | awk 'NR==2{printf "tmpfs_used_gib %.1f of %.1f\n", $3/1048576, $2/1048576}' | tee -a "$O/host.txt"

if [ -e /tmp/db.sha256 ]; then
  echo "== the databases, file by file, against the manifest pinned in pins.sha256 =="
  python3 -u hashtree.py check /tmp/db /tmp/db.sha256 | tee "$O/db-check.txt"
  [ "${PIPESTATUS[0]}" = 0 ] || { echo "FAIL: a database file does not match db.sha256"; exit 1; }
else
  # first run (the canary): record the per-file manifest of the staged databases, to be pinned
  echo "== no db.sha256 staged: writing the manifest of the staged databases =="
  python3 -u hashtree.py write /tmp/db "$O/db.sha256" | tee "$O/db-check.txt"
fi

echo "== reads: both mates as one single-end file, mate IDs made unique =="
# fasterq-dump --split-files names both mates `@ERR2984773.1 ...`. HUMAnN keys its alignment
# scores and its unaligned-read store by the first word of the ID, so identical mate IDs are merged
# into one read (the canary measures what that does). /1 and /2 keep them apart.
T0=$(date +%s)
for m in 1 2; do
  if [ "$PAIRS" = 0 ]; then gzip -dc "${ACC}_$m.fastq.gz"; else gzip -dc "${ACC}_$m.fastq.gz" | awk -v n=$((PAIRS*4)) 'NR<=n'; fi \
    | awk -v m="$m" 'NR%4==1{sub(/ /, "/" m " "); print; next} NR%4==3{print "+"; next} {print}'
done > "$W/$ACC.fastq"
N=$(awk 'END{print NR/4}' "$W/$ACC.fastq")
T1=$(date +%s)
echo "reads $N prep_s $((T1-T0))" | tee "$O/times.txt"
awk 'NR%4==1{print $1}' "$W/$ACC.fastq" | sort | uniq -d | awk 'END{print "duplicate_read_ids " NR}' | tee -a "$O/times.txt"

# HUMAnN 3.9 parses the LAST line of `metaphlan --version`, which MetaPhlAn always fills with a
# database status line, so the check fails and only --bypass-prescreen gets past it -- but that
# flag also makes HUMAnN build its nucleotide index from ALL of ChocoPhlAn, ignoring the profile
# (prescreen.py:239). This shim answers --version with the real version line last and refuses
# anything else, so it also proves MetaPhlAn is never run: the profile below is the one used.
REAL=$(command -v metaphlan)
mkdir -p /tmp/shim
cat > /tmp/shim/metaphlan <<EOF
#!/bin/sh
if [ "\${1:-}" = "--version" ]; then v=\$($REAL --version 2>&1); echo "\$v"; echo "\$v" | grep -m1 'MetaPhlAn version'; exit 0; fi
echo "metaphlan shim: refusing '\$*' -- HUMAnN was given --taxonomic-profile" >&2; exit 1
EOF
chmod +x /tmp/shim/metaphlan

cat > /tmp/rss.py <<'PY'
import resource, subprocess, sys, time
t0 = time.time(); rc = subprocess.call(sys.argv[2:]); wall = time.time() - t0
ru = resource.getrusage(resource.RUSAGE_CHILDREN)
open(sys.argv[1], "w").write("rc %d\nwall_s %.1f\nmaxrss_kib %d\ncpu_s %.1f\n" % (rc, wall, ru.ru_maxrss, ru.ru_utime + ru.ru_stime))
sys.exit(rc)
PY

run_humann() {   # <input fastq> <out dir> <tag> [extra humann args]
  local in=$1 out=$2 tag=$3; shift 3
  python3 -u /tmp/rss.py "$O/$tag.rss.txt" humann --input "$in" --output "$out" --output-basename "$ACC" \
    --threads "$T" --taxonomic-profile "/tmp/$ACC.profile.txt" --metaphlan /tmp/shim \
    --nucleotide-database /tmp/db/chocophlan --protein-database /tmp/db/uniref \
    --o-log "$O/$tag.log" "$@" > "$O/$tag.stdout" 2>&1
}

echo "== HUMAnN, $T threads =="
cat /sys/fs/cgroup/memory.peak > /dev/null 2>&1 && echo "cgroup_memory_peak_available yes" | tee -a "$O/times.txt"
run_humann "$W/$ACC.fastq" "$W/hout" humann
RC=$?
cat "$O/humann.rss.txt" | tee -a "$O/times.txt"
echo "cgroup_memory_peak_bytes $(cat /sys/fs/cgroup/memory.peak 2>/dev/null)" | tee -a "$O/times.txt"
grep -a "TIMESTAMP" "$O/humann.log" | awk -F'\t' '{gsub(/^ +| +$/, "", $2); gsub(/ /, "_", $2); print "step " $2 " " $4}' | tee -a "$O/times.txt"
df -k /var/tmp | awk 'NR==2{printf "disk_used_gib %.1f\n", $3/1048576}' | tee -a "$O/times.txt"
if [ "$RC" != 0 ]; then tail -40 "$O/humann.stdout"; echo "FAIL: humann exit $RC"; exit 1; fi
for t in genefamilies pathabundance pathcoverage; do cp "$W/hout/${ACC}_$t.tsv" "$O/"; done
grep -a '^>' "$W/hout/${ACC}_humann_temp/${ACC}_custom_chocophlan_database.ffn" \
  | grep -o 'g__[^|]*\.s__[^|]*' | sort | uniq -c > "$O/custom_db_species.txt"

FAILS=0
echo "== identities =="
python3 -u check.py "$O" "$N" "$TRUTH" || FAILS=$((FAILS+1))

if [ "$EXTRA" = rawids ]; then
  echo "== canary: nucleotide-only, unique mate IDs vs the IDs fasterq-dump wrote =="
  for m in 1 2; do gzip -dc "${ACC}_$m.fastq.gz" | awk -v n=$((PAIRS*4)) 'NR<=n'; done > "$W/raw.fastq"
  run_humann "$W/$ACC.fastq" "$W/nuc-unique" nuc-unique --bypass-translated-search
  run_humann "$W/raw.fastq" "$W/nuc-raw" nuc-raw --bypass-translated-search
  for v in nuc-unique nuc-raw; do
    f="$W/$v/${ACC}_genefamilies.tsv"; cp "$f" "$O/$v.genefamilies.tsv"
    awk -F'\t' -v v="$v" '$1=="UNMAPPED"{u=$2} $1!~/^#/ && $1!="UNMAPPED" && $1!~/\|/{s+=$2; n++}
      END{printf "%s UNMAPPED %s community_rpk_sum %.4f families %d\n", v, u, s, n}' "$f" | tee -a "$O/rawids.txt"
    grep -a "Unaligned reads after nucleotide" "$O/$v.stdout" | sed "s/^/$v /" | tee -a "$O/rawids.txt"
  done
fi

echo "== fingerprints for the cross-chip comparison =="
for t in genefamilies pathabundance pathcoverage; do
  grep -v '^#' "$O/${ACC}_$t.tsv" | sort | sha256sum | awk -v t="$t" '{print t "_sorted_body_sha256 " $1}'
done | tee "$O/fingerprints.txt"
echo "failures $FAILS" | tee -a "$O/times.txt"
[ "$FAILS" = 0 ] || exit 1
echo "HUMANN-REAL OK"
