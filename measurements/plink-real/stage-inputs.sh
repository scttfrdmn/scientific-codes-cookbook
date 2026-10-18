#!/usr/bin/env bash
# Stage the 1000 Genomes Phase 3 autosomes (all 22 genotype VCFs, 2,504 samples) and the panel,
# plus the pinned phenotype noise and the workload script, under inputs/plink-real/.
#
# Tier: RODA. s3://1000genomes/release/20130502/ is the consortium's final Phase 3 release
# (files dated 2015, unchanged since); the copy is server-side and every byte is checked against
# pins.sha256 inside the task. plink2-real reads the same prefix, so both tools see the same bytes.
set -euo pipefail

BUCKET="s3://${1:?pass your bucket}"
REGION="${AWS_REGION:-us-west-2}"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PREFIX="$BUCKET/inputs/plink-real"
SRC=s3://1000genomes/release/20130502

for c in $(seq 1 22); do
  f=ALL.chr$c.phase3_shapeit2_mvncall_integrated_v5a.20130502.genotypes.vcf.gz
  # already staged (same size)? the task's sha256 check is what proves the bytes
  have=$(aws s3 ls --region "$REGION" "$PREFIX/$f" | awk -v f="$f" '$4==f{print $3}')
  want=$(aws s3 ls "$SRC/$f" | awk -v f="$f" '$4==f{print $3}')
  [ -n "$have" ] && [ "$have" = "$want" ] && continue
  aws s3 cp --region "$REGION" --only-show-errors "$SRC/$f" "$PREFIX/$f"
done
aws s3 cp --region "$REGION" --only-show-errors "$SRC/integrated_call_samples_v3.20130502.ALL.panel" "$PREFIX/"

tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT
cd "$tmp"
aws s3 cp --region "$REGION" --only-show-errors "$PREFIX/integrated_call_samples_v3.20130502.ALL.panel" .
# phenotype noise: one N(0,1) draw per sample, Python's own Mersenne Twister at a fixed seed, so
# the phenotype is the same bytes on every chip (awk's rand() differs between images)
python3 -I - <<'EOF'
import random
r = random.Random(20130502)
ids = [l.split()[0] for l in open("integrated_call_samples_v3.20130502.ALL.panel").read().splitlines()[1:]]
with open("noise.txt", "w") as f:
    for i in ids:
        f.write("%s %s %.6f\n" % (i, i, r.gauss(0, 1)))
EOF
cp "$SCRIPT_DIR/real.sh" .
cp "$SCRIPT_DIR/vcf.sha256" pins.sha256
cat >> pins.sha256 <<'PINS'
b4023dc6ee2d62ee89c8d4d347db4d348e65518d66d346574cdae7a4bbd76858  integrated_call_samples_v3.20130502.ALL.panel
0414aadde6fe1c2740817c9a2335d86f740ae74d7f803257ad2be75d1fbbccb0  noise.txt
PINS
shasum -a 256 real.sh >> pins.sha256
shasum -a 256 -c <(grep -E 'panel|noise|real.sh' pins.sha256)
for f in noise.txt real.sh pins.sha256; do
  aws s3 cp --region "$REGION" --only-show-errors "$f" "$PREFIX/$f"
done
cat pins.sha256
echo "staged to $PREFIX/"
