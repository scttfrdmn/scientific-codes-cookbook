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
# c8g is not re-run here: the headline `recipes/gatk4` call IS the c8g row -- same spec, same
# digest, same bytes, same one thread. Reusing it is the bwa-real precedent, stated in Caveats.
for fam in c6g c7g c9g; do
  python3 - "$SPEC" "$fam" "$COOKBOOK_BUCKET" <<'PY' > "/tmp/gatk-$fam.json"
import json,sys
spec,fam,bucket = sys.argv[1],sys.argv[2],sys.argv[3]
d=json.load(open(spec))
d['task_id']=f"cookbook-gatk4-gen-{fam}"
d['resources']['families']=[fam]
ttl={'c6g':'210m','c7g':'175m','c9g':'125m'}[fam]
cap={'c6g':0.50,'c7g':0.45,'c9g':0.40}[fam]
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
  for f in c6g c7g c9g; do
    echo "== $f =="
    aws s3 cp "s3://$COOKBOOK_BUCKET/measurements/gatk4-real/$f/smoke-check.txt" -
  done
EOF
