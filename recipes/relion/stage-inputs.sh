#!/usr/bin/env bash
# Stage this recipe's four inputs: the two unfiltered half-maps and the solvent mask
# from a real RELION 5 Refine3D job, plus that project's own PostProcess result to
# compare against.
#
# Every byte comes from a RODA bucket — s3://cryoem-spa-workflow-records-public, the
# KEK SBRC "Cryo-EM SPA Workflow Records" dataset, which archives complete RELION and
# cryoSPARC project directories. The objects are copied byte for byte; nothing is
# derived here, so the sha256 sums below are pins on the upstream objects themselves.
#
# The source bucket is in ap-northeast-1 and the cookbook's bucket is in us-east-1, so
# this is a cross-region copy: it is done once here, at staging time, not on the box.
set -euo pipefail

BUCKET="${1:?pass your bucket -- make stage RECIPE=NAME does this}"
SRC="s3://cryoem-spa-workflow-records-public/ArXiv/EMPIAR/EMPIAR10581/260123_tmoriya_relion5o0o0_res2o75_run05_nosplit_FSx9600"
SRC_REGION="ap-northeast-1"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
cd "$WORK"

# Refine3D/job044 is the final refinement of that project (0.968 A/px, box 440); its
# PostProcess counterpart is job045. The mask was imported in job022. RELION derives
# the half2 filename from half1 by substituting "half1" -> "half2", so these two
# names must not be changed.
fetch() {  # fetch <src-key> <local-name>
  aws s3 cp "$SRC/$1" "$2" --no-sign-request --region "$SRC_REGION" --only-show-errors
}
fetch Refine3D/job044/run_half1_class001_unfil.mrc     run_half1_class001_unfil.mrc
fetch Refine3D/job044/run_half2_class001_unfil.mrc     run_half2_class001_unfil.mrc
fetch Import/job022/mask3d_emd_0731_apix0o968_d440.mrc mask3d_emd_0731_apix0o968_d440.mrc
fetch PostProcess/job045/postprocess.star              reference_postprocess.star

# Verify the objects are what the recipe pins, before anything is uploaded. A silent
# change upstream must fail here rather than on the box.
cat > pins.sha256 <<'EOF'
8fe4881f98b31d0acb070229afc91d53fa9b43efaf114baa7548aebfe7a6c76e  run_half1_class001_unfil.mrc
69bdea0c8e1d204bc420cbc6e3d44639490f59eb5202a65fc0870e291a644617  run_half2_class001_unfil.mrc
07939784952a5f4592af1daeb1c6b6ca2ad514f252edca62fc883680b834166d  mask3d_emd_0731_apix0o968_d440.mrc
b0611c8ef5a082fb350f78f1bd9cac17eaa84367b9828840d11b53df7c1b6f71  reference_postprocess.star
EOF
sha256sum -c pins.sha256

# Independent re-check of the MRC headers, so a truncated or wrong-box map cannot pass.
# An MRC file carries nx,ny,nz as the first three int32 and the literal "MAP " at byte
# 208; mode 2 is float32. All three maps must agree on the box, or the FSC is meaningless.
python3 - <<'PY'
import struct, sys
want = None
for name in ("run_half1_class001_unfil.mrc", "run_half2_class001_unfil.mrc",
             "mask3d_emd_0731_apix0o968_d440.mrc"):
    with open(name, "rb") as fh:
        head = fh.read(1024)
    nx, ny, nz, mode = struct.unpack("<4i", head[0:16])
    magic = head[208:212].decode("latin-1")
    if magic != "MAP ":
        sys.exit(f"{name}: not an MRC file (magic {magic!r})")
    if mode != 2:
        sys.exit(f"{name}: mode {mode}, expected 2 (float32)")
    if not nx == ny == nz:
        sys.exit(f"{name}: non-cubic box {nx}x{ny}x{nz}")
    if want is None:
        want = nx
    elif nx != want:
        sys.exit(f"{name}: box {nx}, expected {want} to match the half-maps")
    print(f"{name}: box {nx}^3, mode {mode}")
print(f"all three maps agree on box {want}^3")
PY

# The reference result must be a RELION postprocess star with a full FSC table, or the
# in-task reproduction check has nothing to compare against.
grep -q '^# RELION postprocess' reference_postprocess.star \
  || { echo "reference_postprocess.star is not a RELION postprocess file" >&2; exit 1; }
# The file holds a data_guinier block after data_fsc, so stop counting at the next
# data_ header rather than running the two tables together.
[ "$(awk '/^data_fsc/{f=1;next} f&&/^data_/{f=0} f&&/^ *[0-9]/{n++} END{print n+0}' reference_postprocess.star)" -eq 221 ] \
  || { echo "reference FSC table is not 221 shells" >&2; exit 1; }

for f in run_half1_class001_unfil.mrc run_half2_class001_unfil.mrc \
         mask3d_emd_0731_apix0o968_d440.mrc reference_postprocess.star; do
  aws s3 cp "$f" "s3://$BUCKET/inputs/relion/$f" --only-show-errors
done

echo "--- pins (recorded in README.md):"
cat pins.sha256
