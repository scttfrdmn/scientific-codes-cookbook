#!/usr/bin/env bash
# Stage macs2's inputs: a CTCF ChIP-seq treatment + its matched input control, each
# subset to chr20. DERIVED from an immutable source, so reproducible.
#
# Source: ENCODE on AWS Open Data (s3://encode-public, no-sign) — RODA-tier, immutable
# object URIs. The chr20 subset is `samtools view` of the full BAM; it is byte-pinned, so
# the subset runs in the SAME pinned samtools the fixture was made with (a host samtools of
# a different version can reorder/rewrite headers and shift the sha256). Full BAMs are ~2
# and ~3.4 GB — staging downloads them once; the recipe only ever sees the chr20 slices.
set -euo pipefail

BUCKET="${1:?pass your bucket -- make stage RECIPE=NAME does this}"
CTCF="s3://encode-public/2022/07/20/fd5c1f5e-6c89-409e-9e70-66e8725b135b/ENCFF933NSJ.bam"    # CTCF ChIP, HCT116, GRCh38
INPUT="s3://encode-public/2022/07/09/92eae575-421f-41a5-b0d8-b5273c47cc5f/ENCFF768XTH.bam"   # matched input control
SIMG="quay.io/aarchbio/samtools@sha256:1191739637fb6f46ef97c02b28f693b25ca3ca61f90e1337f349b7b7cc0be4f7"
WORK="$(mktemp -d)"; trap 'rm -rf "$WORK"' EXIT; cd "$WORK"

echo "== fetch the full ENCODE BAMs from AWS Open Data (no-sign; ~5.4 GB total) =="
aws s3 cp --no-sign-request "$CTCF"  ctcf.bam   --only-show-errors
aws s3 cp --no-sign-request "$INPUT" input.bam  --only-show-errors

echo "== subset chr20 in the pinned samtools (byte-fidelity) =="
docker run --rm --platform linux/arm64 --user "$(id -u):$(id -g)" -v "$WORK:/d" -w /d \
  --entrypoint bash "$SIMG" -c '
    for s in ctcf input; do
      samtools index "$s.bam"
      samtools view -b "$s.bam" chr20 -o "${s}_chr20.bam"
      printf "%s reads: " "$s"; samtools view -c "${s}_chr20.bam"
    done'

echo "== verify pins (the read counts above must be 864347 / 1297910 — the science check) =="
# Pinned to this samtools' serialization: an earlier version produced different bytes from the
# same chr20 reads. The read counts (identical) are the science check — same reads in, so macs2
# (deterministic) still calls exactly 1390 peaks; only the serialization was repinned.
cat > pins.sha256 <<'EOF'
0522950dc6eb31d978c6e6d8f5e46d7944e5e5a5ba29005c0021ee5bfaafcb0c  ctcf_chr20.bam
ddacb169b4c8b94d7716f463d7343d7caec43aa2b800e79bd5d5a54c27c0f0d2  input_chr20.bam
EOF
shasum -a 256 -c pins.sha256 2>/dev/null || sha256sum -c pins.sha256

aws s3 cp ctcf_chr20.bam  "s3://$BUCKET/inputs/macs2/ctcf_chr20.bam"  --only-show-errors
aws s3 cp input_chr20.bam "s3://$BUCKET/inputs/macs2/input_chr20.bam" --only-show-errors
echo "staged inputs/macs2/ (ENCODE chr20 subsets, sha256-verified)"
