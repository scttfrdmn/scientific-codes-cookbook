#!/usr/bin/env bash
# Task 02 (beagle image): Beagle 5.5 phases the 250 unphased array genotypes against the panel and
# imputes every panel site. Defaults throughout (seed -99999, 1 cM/Mb with no map); 16 threads.
#   bash beagle.sh <heap>
set +e
set -uo pipefail
cd /tmp
. ./lib.sh
HEAP=${1:?heap, e.g. 20g}
machine
rec java_version "$(java -version 2>&1 | head -1)"
rec threads "$T"
rec heap "$HEAP"
hwm_start java
tic
beagle "-Xmx$HEAP" gt=target.vcf.gz ref=ref.vcf.gz out="$O/beagle" nthreads="$T" > "$O/beagle.stdout" 2>&1
RC=$?
toc beagle
chk "beagle exit status" "$RC" 0
tail -25 "$O/beagle.log" 2>/dev/null
rec beagle_version "$(awk 'NR<=3 && /beagle\./' "$O/beagle.log" | head -1)"
rec beagle_records "$(gzip -dc "$O/beagle.vcf.gz" | grep -vc '^#')"
rec beagle_body_md5 "$(body_md5 "$O/beagle.vcf.gz")"
hwm_stop
finish "BEAGLE"
