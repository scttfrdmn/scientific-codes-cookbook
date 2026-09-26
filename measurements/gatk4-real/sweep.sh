#!/usr/bin/env bash
# GATK4 HaplotypeCaller across Graviton generations, whole chr20, NA12878 at 36x.
#
# Why generation is the ONLY axis here: on AArch64 the Intel GKL native library is x86-64
# only, so the AVX PairHMM and AVX SmithWaterman never load and GATK runs the Java
# implementations single-threaded. `--native-pair-hmm-threads` is therefore inert --
# measured on a 2 Mb slice, 77 s at 1 thread and 78 s at 8, with identical 4093 variants.
# There is no core-count knee to find, so the sweep varies the chip and nothing else.
#
# Every run is the same container digest, the same staged bytes, the same one thread.
set -euo pipefail
: "${COOKBOOK_BUCKET:?set COOKBOOK_BUCKET}"
export AWS_PROFILE="${AWS_PROFILE:-aws}"
SPEC="$(dirname "$0")/../../recipes/gatk4/01-call.task.json"
REGION=us-west-2

# 4 vCPU / 8 GiB on each generation -- the smallest size that holds GATK's heap comfortably.
#
# TTL per generation, from the MEASURED c8g whole-chr20 wall (112 min, read off GATK's own
# ProgressMeter) scaled by bwa's measured generation ratios. A first attempt sized these from a
# 2 Mb canary slice and every run died at TTL: chr20:1-3Mb ran at 1.56 Mb/min but the
# whole-chromosome average is 0.573 Mb/min, a 2.7x miss, because the slice opens on a telomere
# and HaplotypeCaller's rate tracks local complexity. Size from a run that is REPRESENTATIVE of
# the whole, not merely measured.
# All four generations run here, including c8g: the headline recipe run covers the WHOLE
# chromosome while this sweep covers a 10 Mb interval, so they are different workloads and
# c8g's sweep row cannot be borrowed from it.
for fam in c6g c7g c8g c9g; do
  python3 - "$SPEC" "$fam" "$COOKBOOK_BUCKET" <<'PY' > "/tmp/gatk-$fam.json"
import json,sys
spec,fam,bucket = sys.argv[1],sys.argv[2],sys.argv[3]
d=json.load(open(spec))
d['task_id']=f"cookbook-gatk4-gen-{fam}"
d['resources']['families']=[fam]
# The sweep interval is chr20:1,000,000-3,000,000 -- the ONLY interval with a COMPLETED
# timing (77 s on c8g, canary.task.json). Two larger choices were tried and both blew their
# TTLs, because HaplotypeCaller's rate on chr20 varies ~50x with local complexity: the p-arm
# runs ~9,480 regions/min while pericentromeric 30-31 Mb runs 168 regions/min (measured:
# 1.07 Mb in 35.4 min). An AVERAGE over a heterogeneous stretch does not license picking a
# sub-window of it -- that is exactly how chr20:30-40Mb looked defensible and was the worst
# available choice. So sweep only what has been clocked end to end.
#
# The metric is wall_s, timed around HaplotypeCaller ALONE by the spec. Boot plus staging
# 921 MB of BAM would otherwise dominate an 80 s run and make this a network comparison.
d['command'][2]=d['command'][2].replace('-L chr20 ','-L chr20:1000000-3000000 ').replace('test "$TOTAL" -gt 50000','test "$TOTAL" -gt 1000')
ttl='25m'
cap={'c6g':0.06,'c7g':0.07,'c8g':0.07,'c9g':0.08}[fam]
d['lifecycle']['ttl']=ttl
d['lifecycle']['cost_limit']=cap
# each generation writes its own prefix so four runs don't overwrite one another
for o in d['outputs']:
    o['destination']=o['destination'].replace('/runs/gatk4/r1/',f'/measurements/gatk4-real/{fam}/')
s=json.dumps(d).replace('${COOKBOOK_BUCKET}',bucket)
print(s)
PY
  echo "== $fam =="
  spawn task run --spec "/tmp/gatk-$fam.json" --region "$REGION" 2>&1 | grep -E 'launched|Instance|TTL|Max cost'
done

cat <<'EOF'

Four tasks launched, each self-terminating. Collect with:
  for f in c6g c7g c8g c9g; do
    echo "== $f =="
    aws s3 cp "s3://$COOKBOOK_BUCKET/measurements/gatk4-real/$f/smoke-check.txt" -
  done
EOF
