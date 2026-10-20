#!/usr/bin/env bash
# 00-intervals, in the recipe's GATK image: Broad's hg38 calling regions on chr20, under the chr20
# reference's own dictionary, scattered the way Broad's production pipelines scatter them (warp's
# Utilities.ScatterIntervalList: IntervalListTools, balanced without subdivision, 1 Mb bands).
#   scatter-16/     the workload: all of chr20's calling regions, 16 shards
#   scatter-probe/  the sizing probe: the same regions clipped to chr20:1-2,000,000, 4 shards
set +e
export PATH=/opt/conda/bin:/usr/local/bin:$PATH HOME=/tmp TMPDIR=/tmp
cd /tmp && mkdir -p /tmp/out work
L=/tmp/out/intervals.log; : > $L
{ cat chr20.dict; awk -F'\t' '!/^@/ && $1=="chr20"' wgs_calling_regions.hg38.interval_list; } > work/chr20.interval_list
{ cat chr20.dict; awk -F'\t' -v OFS='\t' '!/^@/ && $1=="chr20" && $2<=2000000 {if($3>2000000)$3=2000000; print}' \
    wgs_calling_regions.hg38.interval_list; } > work/probe.interval_list
scatter() {   # <in> <count> <outdir>
  gatk IntervalListTools --SCATTER_COUNT "$2" \
    --SUBDIVISION_MODE BALANCING_WITHOUT_INTERVAL_SUBDIVISION_WITH_OVERFLOW \
    --UNIQUE true --SORT true --BREAK_BANDS_AT_MULTIPLES_OF 1000000 \
    -I "$1" -O "$3" >> $L 2>&1
}
scatter work/chr20.interval_list 16 /tmp/out/scatter-16
scatter work/probe.interval_list 4 /tmp/out/scatter-probe
bp() { cat "$@" | awk -F'\t' '!/^@/{s+=$3-$2+1} END{print s+0}'; }
FAILS=0
for s in 16 probe; do
  src=work/$([ $s = 16 ] && echo chr20 || echo probe).interval_list
  n=$(ls /tmp/out/scatter-$s/*/scattered.interval_list | wc -l | tr -d ' ')
  a=$(bp $src); b=$(bp /tmp/out/scatter-$s/*/scattered.interval_list)
  echo "scatter_$s shards $n source_bp $a scattered_bp $b" | tee -a /tmp/out/intervals.txt
  [ "$a" = "$b" ] && [ "$n" -gt 1 ] || FAILS=$((FAILS+1))
done
( cd /tmp/out && sha256sum scatter-*/*/scattered.interval_list ) >> /tmp/out/intervals.txt
cat /tmp/out/intervals.txt
[ $FAILS = 0 ] && echo "INTERVALS OK" || { echo "FAIL: scatter does not conserve the calling regions"; exit 1; }
