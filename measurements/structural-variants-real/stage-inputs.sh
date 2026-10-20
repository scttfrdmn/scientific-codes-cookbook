#!/usr/bin/env bash
# Stage measurements/structural-variants-real. Every byte moves S3-to-S3 or inside a spawn task;
# nothing is downloaded to this machine.
#
#  1. Server-side copies from RODA into the bucket: GIAB's HG002 SV benchmark (NIST_SV_v0.6, Tier 1),
#     the hs37d5 reference that benchmark and the BAM are on, and NHGRI's novoalign
#     HG002.hs37d5.60x.1.bam (126 GB), all from s3://giab and s3://1000genomes (us-east-1).
#  2. 00-prep (samtools, on AWS): subsamples the 60x BAM to 30x with a fixed seed and adds the read
#     group delly requires; decompresses the reference and indexes it. The sha256 of every source
#     object and of every derived file is held to pins.sha256.
#
# Tier: RODA (GIAB, 1000 Genomes). The 30x BAM is derived (build tier) and pinned by sha256 here.
#   stage-inputs.sh <bucket>
set -euo pipefail
B="${1:?pass your bucket}"
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
P="s3://$B/inputs/structural-variants-real"
GIAB=s3://giab/release/AshkenazimTrio/HG002_NA24385_son/NIST_SV_v0.6
BAMS=s3://giab/data/AshkenazimTrio/HG002_NA24385_son/NIST_HiSeq_HG002_Homogeneity-10953946/NHGRI_Illumina300X_AJtrio_novoalign_bams
REF=s3://1000genomes/technical/reference/phase2_reference_assembly_sequence

mirror() {   # mirror <src> <dst>: a server-side copy, skipped when the object is already there at its size
  local want got
  want=$(aws s3api head-object --region us-east-1 --bucket "$(echo "$1" | cut -d/ -f3)" \
    --key "$(echo "$1" | cut -d/ -f4-)" --query ContentLength --output text)
  got=$(aws s3api head-object --region us-west-2 --bucket "$B" --key "${2#s3://$B/}" \
    --query ContentLength --output text 2>/dev/null || echo 0)
  [ "$want" = "$got" ] && { echo "  have $(basename "$2") ($got bytes)"; return; }
  aws s3 cp --only-show-errors --source-region us-east-1 --region us-west-2 "$1" "$2"
  echo "  copied $(basename "$2") ($want bytes)"
}
for f in HG002_SVs_Tier1_v0.6.vcf.gz HG002_SVs_Tier1_v0.6.vcf.gz.tbi HG002_SVs_Tier1_v0.6.bed README_SV_v0.6.txt; do
  mirror "$GIAB/$f" "$P/giab/$f"
done
for f in hs37d5.fa.gz hs37d5.fa.gz.fai; do mirror "$REF/$f" "$P/ref/$f"; done
for f in HG002.hs37d5.60x.1.bam HG002.hs37d5.60x.1.bam.bai; do mirror "$BAMS/$f" "$P/giab/$f"; done

for f in prep.sh manta.sh delly.sh score.py pins.sha256 human.hg19.excl.tsv; do
  aws s3 cp --only-show-errors "$HERE/$f" "$P/$f"
done

check() {   # the prep task's sha256 of every source and derived file must equal its pin
  local got
  got=$(aws s3 cp --only-show-errors "$P/derived/inputs.sha256" - 2>/dev/null) || { echo "  no prep output yet"; return 1; }
  diff <(echo "$got" | sort -k2) <(grep -v '^#' "$HERE/pins.sha256" | sort -k2) && echo "  every staged file == pin"
}
check || {
  spawn task run --wait --spec "$(python3 "$ROOT/scripts/spec_arch.py" structural-variants arm64 "$B" "$(date +%s)" \
    "$HERE/00-prep.task.json")"
  check
}
echo "staged to $P/"
