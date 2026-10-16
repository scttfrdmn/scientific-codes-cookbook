#!/usr/bin/env bash
# Stage ANTs' fixture: a smooth asymmetric volume and an exactly-shifted copy of it, written
# as NIfTI-1 here and pinned by sha256. The ANTs image ships no python, so nothing can be
# synthesised in the task; generating it once also makes the bytes identical for every run.
#
# The shifted copy is the reference for an EXACT check, so it is made here with numpy -- by
# array indexing, not by ANTs -- and the task then asks ANTs to produce the same thing.
set -euo pipefail

BUCKET="s3://${1:?pass your bucket -- make stage RECIPE=ants does this}"
REGION="${AWS_REGION:-us-west-2}"

tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT
cd "$tmp"

python3 - <<'PY'
import struct, numpy as np

N = 64

def write_nifti(path, arr):
    """A minimal single-file NIfTI-1 (float32, 1 mm isotropic, voxel 31.5 at the origin).

    Written by hand because nibabel is not assumed on the staging host. qform and sform are
    both the same identity-plus-offset, so there is one unambiguous voxel->world mapping."""
    a = np.asarray(arr, dtype="<f4")
    hdr = bytearray(348)
    struct.pack_into("<i", hdr, 0, 348)                                   # sizeof_hdr
    struct.pack_into("<8h", hdr, 40, 3, N, N, N, 1, 1, 1, 1)            # dim
    struct.pack_into("<h", hdr, 70, 16)                                   # datatype: float32
    struct.pack_into("<h", hdr, 72, 32)                                   # bitpix
    struct.pack_into("<8f", hdr, 76, 1.0, 1.0, 1.0, 1.0, 1, 1, 1, 1)     # pixdim (qfac=1)
    struct.pack_into("<f", hdr, 108, 352.0)                               # vox_offset
    struct.pack_into("<f", hdr, 112, 1.0)                                 # scl_slope
    struct.pack_into("<h", hdr, 252, 1); struct.pack_into("<h", hdr, 254, 1)  # qform, sform codes
    struct.pack_into("<6f", hdr, 256, 0, 0, 0, -31.5, -31.5, -31.5)     # quatern b,c,d + offsets
    for r, row in enumerate(((1, 0, 0, -31.5), (0, 1, 0, -31.5), (0, 0, 1, -31.5))):
        struct.pack_into("<4f", hdr, 280 + 16 * r, *row)                  # srow_x/y/z
    hdr[344:348] = b"n+1\x00"                                             # magic
    with open(path, "wb") as f:
        f.write(hdr); f.write(b"\x00\x00\x00\x00")                       # empty extension
        f.write(np.asfortranarray(a).tobytes(order="F"))                  # NIfTI is x-fastest

i, j, k = np.meshgrid(np.arange(N), np.arange(N), np.arange(N), indexing="ij")
def blob(c, s, w):
    return w * np.exp(-(((i - c[0]) / s[0]) ** 2 + ((j - c[1]) / s[1]) ** 2 + ((k - c[2]) / s[2]) ** 2) / 2)
# Deliberately asymmetric -- different sizes, weights and elongations off-centre -- so a
# rotation or translation of it is identifiable and cannot be satisfied by a symmetry.
vol = (blob((24, 30, 32), (6, 9, 7), 100) + blob((40, 22, 28), (4, 4, 8), 70)
       + blob((34, 42, 38), (8, 5, 5), 55) + blob((20, 44, 22), (3, 6, 4), 40))
write_nifti("fixed.nii", vol)

# The exact reference: shifted by 2 voxels along array axis i, zero-filled. In ITK's LPS world
# frame x = -(i - 31.5), so a +2 mm ITK x-translation samples i - 2 -- this is that image.
shifted = np.zeros_like(vol); shifted[2:] = vol[:-2]
write_nifti("shift2.nii", shifted)

print("  fixed.nii   64^3 float32, 1 mm, range [%.3f, %.3f], sum %.6f" % (vol.min(), vol.max(), vol.sum()))
print("  shift2.nii  same, shifted 2 voxels along i (the exact nearest-neighbour reference)")
# the fixture must not be symmetric under the planted rotation, or recovering it is vacuous
rot = np.rot90(vol, axes=(0, 1))
print("  asymmetry: corr(vol, rot90(vol)) = %.3f (well below 1, so orientation is identifiable)"
      % np.corrcoef(vol.ravel(), rot.ravel())[0, 1])
PY

sha256sum fixed.nii shift2.nii > fixture.sha256 2>/dev/null || shasum -a 256 fixed.nii shift2.nii > fixture.sha256
for f in fixed.nii shift2.nii fixture.sha256; do
  aws s3 cp --region "$REGION" --only-show-errors "$f" "$BUCKET/inputs/ants/$f"
done
cat fixture.sha256
echo "done."
echo "  $BUCKET/inputs/ants/  (a synthetic volume and its exact 2-voxel shift)"
