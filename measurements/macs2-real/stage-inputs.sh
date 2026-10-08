#!/usr/bin/env bash
# Cache the FULL ENCODE CTCF ChIP-seq experiment into our bucket, whole genome.
#
# WHAT CHANGES FROM THE SHIPPED RECIPE. recipes/macs2 calls peaks on a chr20 subset of these
# exact two files. This is the same experiment at full scale -- so the measurement is a
# scale-up of a known workload, not a new fixture. The chr20 assertion (1390 peaks) does NOT
# transfer: a different input is a different number, and this run derives its own.
#
# WHY A SERVER-SIDE COPY AND NOT A TASK. `encode-public` is in us-west-2, the same region as
# our bucket, so `aws s3 cp` between them is a server-side copy: the 5.4 GB never transits
# this laptop and there is no cross-region transfer to justify. That is the only reason this
# one is safe to run locally -- the metaphlan/bwa-mem2 precedent (fetch on a box) exists
# because those sources were remote HTTP, which is a different problem.
#
# THE BYTES ARE CHECKABLE AT SOURCE. ENCODE publishes an md5 per file in its metadata API,
# so this is the same sourcing tier as metaphlan's database: verified against the depositors'
# own checksum rather than only against ourselves. The assert runs ON THE BOX, in the task,
# because that is where the bytes actually get used.
set -euo pipefail

BUCKET="s3://${1:?pass your bucket}"
REGION="${AWS_REGION:-us-west-2}"
DST="$BUCKET/inputs/macs2-real"

# ENCODE CTCF ChIP-seq in HCT116, GRCh38 alignments, and its matched input control.
# md5 and byte size are ENCODE's own published values (www.encodeproject.org/files/<acc>/).
CTCF_SRC="s3://encode-public/2022/07/20/fd5c1f5e-6c89-409e-9e70-66e8725b135b/ENCFF933NSJ.bam"
CTRL_SRC="s3://encode-public/2022/07/09/92eae575-421f-41a5-b0d8-b5273c47cc5f/ENCFF768XTH.bam"
CTCF_MD5=48f06f46ac59b93e6ae3110de9730a3e
CTRL_MD5=ef4683318e7e0fede279fc5717116ccb
CTCF_SIZE=2057454374
CTRL_SIZE=3365735271

echo "== ENCODE CTCF (treatment) and its matched control, full genome =="
for pair in "ENCFF933NSJ.bam $CTCF_SRC $CTCF_SIZE" "ENCFF768XTH.bam $CTRL_SRC $CTRL_SIZE"; do
  set -- $pair
  name=$1; src=$2; want=$3
  if aws s3 ls "$DST/$name" --region "$REGION" >/dev/null 2>&1; then
    echo "  $name already cached"
  else
    echo "  copying $name server-side (same region, no local transfer)"
    aws s3 cp "$src" "$DST/$name" --region "$REGION" --only-show-errors
  fi
  got=$(aws s3api head-object --bucket "${BUCKET#s3://}" --key "inputs/macs2-real/$name" \
          --region "$REGION" --query ContentLength --output text)
  test "$got" = "$want" || { echo "  $name is $got bytes, ENCODE publishes $want" >&2; exit 1; }
  echo "    $got bytes matches ENCODE's published size"
done

# Written where the task can read them, so the md5 gate runs on the box next to the bytes.
tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT
cat > "$tmp/encode.md5" <<EOF
$CTCF_MD5  ENCFF933NSJ.bam
$CTRL_MD5  ENCFF768XTH.bam
EOF
aws s3 cp "$tmp/encode.md5" "$DST/encode.md5" --region "$REGION" --only-show-errors

echo "done."
echo "  $DST/ENCFF933NSJ.bam   (1.92 GiB, CTCF ChIP, HCT116, GRCh38)"
echo "  $DST/ENCFF768XTH.bam   (3.13 GiB, matched input control)"
echo "  $DST/encode.md5        (ENCODE's published md5s -- asserted in the task)"
