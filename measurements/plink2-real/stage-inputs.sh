#!/usr/bin/env bash
# Stage measurements/plink2-real: 1000 Genomes Phase 3, chromosomes 1-22, all 2504 samples.
#
# THE DATA (RODA tier). The 22 autosomal genotype VCFs of the 1000 Genomes Phase 3 release
# (release/20130502, shapeit2/mvncall integrated v5a; 1000 Genomes Project Consortium 2015,
# Nature 526:68, doi:10.1038/nature15393) from s3://1000genomes, objects written 2015 and never
# replaced. They live under inputs/plink-real/ because recipes/plink's real run uses the same
# objects: one copy, so the PLINK 1.9 / PLINK 2 comparison is on identical bytes. If they are
# already there, nothing is copied. Plus the release's own sample panel (population labels).
# Pins: sha256 below, asserted again on the box before any compute.
set -euo pipefail

BUCKET="s3://${1:?pass your bucket}"
REGION="${AWS_REGION:-us-west-2}"
DIR="$(cd "$(dirname "$0")" && pwd)"
SRC=s3://1000genomes/release/20130502
VCF="$BUCKET/inputs/plink-real"
DST="$BUCKET/inputs/plink2-real"
tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT

cat > "$tmp/vcf.sha256" <<'EOF'
ae0eb80ea07c19033332d17de28fb05128a3f5778d9e16dc8eadcab7e3c21987  ALL.chr1.phase3_shapeit2_mvncall_integrated_v5a.20130502.genotypes.vcf.gz
93f046aac1c2126eed856298fb46db74970f41afef6b604458e62362a3a7ca88  ALL.chr2.phase3_shapeit2_mvncall_integrated_v5a.20130502.genotypes.vcf.gz
31123d852e4dce03bbd16de7cf4168d1c66aa2e7ab4db487e7f6329aa32b7256  ALL.chr3.phase3_shapeit2_mvncall_integrated_v5a.20130502.genotypes.vcf.gz
a95ae4318a740c3ae65762790f31c60baa93d95153377b740866eaa4c4a7d018  ALL.chr4.phase3_shapeit2_mvncall_integrated_v5a.20130502.genotypes.vcf.gz
a4a1160c7bf65d3a76f177492d5e49eac78d3636e98d3326bf69a15bdae9a441  ALL.chr5.phase3_shapeit2_mvncall_integrated_v5a.20130502.genotypes.vcf.gz
0f89d8da7d25db7dc639580785da47e49bb398ccfaf442f7406a71eab95b3912  ALL.chr6.phase3_shapeit2_mvncall_integrated_v5a.20130502.genotypes.vcf.gz
74032eb676d56f6f65bf84db3964d77fd50312f9b572b268fcbdf0bbb9de6fa5  ALL.chr7.phase3_shapeit2_mvncall_integrated_v5a.20130502.genotypes.vcf.gz
f51063a9646c6a11c19eb08ef51d53a2eba54de51075eb4e94979724bea59f0e  ALL.chr8.phase3_shapeit2_mvncall_integrated_v5a.20130502.genotypes.vcf.gz
d40bfb56fb15589842ed3e53805ff51c4e3bccaa3814a730f5c778e16a433d17  ALL.chr9.phase3_shapeit2_mvncall_integrated_v5a.20130502.genotypes.vcf.gz
98ac7174805ad0f7e07a632a673ad9e6b902a330ab3f17bdffe9ecd4e47408e1  ALL.chr10.phase3_shapeit2_mvncall_integrated_v5a.20130502.genotypes.vcf.gz
369850b812212911d12cdee9f6246927d2d7be10de56d8966df062374997fab3  ALL.chr11.phase3_shapeit2_mvncall_integrated_v5a.20130502.genotypes.vcf.gz
7946238b4986460160d437fd078c5fe2c7e73642460b873fba433b4fbb78f257  ALL.chr12.phase3_shapeit2_mvncall_integrated_v5a.20130502.genotypes.vcf.gz
5147b2158c3b5711da30d0a47f80e45e2d6553cd991629ed74a7fab0fea16948  ALL.chr13.phase3_shapeit2_mvncall_integrated_v5a.20130502.genotypes.vcf.gz
3a66cbf59a086897a93aad824991c32e8547026bed46eacf06d998f6f547e400  ALL.chr14.phase3_shapeit2_mvncall_integrated_v5a.20130502.genotypes.vcf.gz
ea8a68acd3966c91cb143811488b97b6ae28f0a40fda3473f0d6c92f0391b409  ALL.chr15.phase3_shapeit2_mvncall_integrated_v5a.20130502.genotypes.vcf.gz
59ce6190ce2f6796c3828bd2a37951a1bdb5a549226660f4ff4d81129f8f85bb  ALL.chr16.phase3_shapeit2_mvncall_integrated_v5a.20130502.genotypes.vcf.gz
a57a7153b08b8893e1cc7c490627f6344c7bbf808a4457951a27da3e48186498  ALL.chr17.phase3_shapeit2_mvncall_integrated_v5a.20130502.genotypes.vcf.gz
1133297f17f798556c03f41d0c3e4ed81fd4bdde082ac98549e26a3b5034a815  ALL.chr18.phase3_shapeit2_mvncall_integrated_v5a.20130502.genotypes.vcf.gz
153faed13243e929e06cde451eb29e3622d275406973c09fd0cfe24e56d258e9  ALL.chr19.phase3_shapeit2_mvncall_integrated_v5a.20130502.genotypes.vcf.gz
4a0b61d6d7ed46b029d4f07164c554c6b8124e40c4eb9807a45c624eb714afa8  ALL.chr20.phase3_shapeit2_mvncall_integrated_v5a.20130502.genotypes.vcf.gz
086fc3a2e13cdf256f217509477669f7ec1f609c21cdb6f58c17588bf99acc39  ALL.chr21.phase3_shapeit2_mvncall_integrated_v5a.20130502.genotypes.vcf.gz
5d52145d6e3be132dc19ff525af2ae2e792511e60c49c148cb8eee669f114c2c  ALL.chr22.phase3_shapeit2_mvncall_integrated_v5a.20130502.genotypes.vcf.gz
b4023dc6ee2d62ee89c8d4d347db4d348e65518d66d346574cdae7a4bbd76858  panel
EOF

while read -r sha f; do
  [ "$f" = panel ] && continue
  if ! aws s3 ls "$VCF/$f" --region "$REGION" >/dev/null 2>&1; then
    echo "  copying $f from RODA (server-side)"
    aws s3 cp "$SRC/$f" "$VCF/$f" --source-region us-east-1 --region "$REGION" --copy-props none --only-show-errors
  fi
done < "$tmp/vcf.sha256"
echo "  22 VCFs present under $VCF/"

aws s3 cp "$SRC/integrated_call_samples_v3.20130502.ALL.panel" "$tmp/panel" --no-sign-request --only-show-errors
have=$(shasum -a 256 "$tmp/panel" | cut -d' ' -f1)
want=$(awk '$2=="panel"{print $1}' "$tmp/vcf.sha256")
[ "$have" = "$want" ] || { echo "  panel sha256 $have, pinned $want" >&2; exit 1; }
aws s3 cp "$tmp/panel" "$DST/panel" --region "$REGION" --only-show-errors
aws s3 cp "$tmp/vcf.sha256" "$DST/vcf.sha256" --region "$REGION" --only-show-errors
for s in p2.sh p19.sh p2-task.sh p19-task.sh; do aws s3 cp "$DIR/$s" "$DST/$s" --region "$REGION" --only-show-errors; done
echo "done."
echo "  $DST/  (panel, vcf.sha256, p2.sh, p19.sh and their task bodies); the VCF sha256s are checked on the box"
