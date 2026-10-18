#!/usr/bin/env bash
# measurements/ants-real, task 0: take FSL's MNI152 2 mm brain and brain mask out of FSL's own
# fsl-data_standard package (the bytes measurements/fsl-real registered to). The ANTs image has no
# bzip2 and no python, so this runs once in the micromamba image, which unpacks the package offline
# from an @EXPLICIT lock line carrying the package's sha256 -- the same pin recipes/fsl's lock holds.
set +e
set -uo pipefail
export TMPDIR=/tmp HOME=/tmp MAMBA_ROOT_PREFIX=/tmp/mamba
cd /tmp
PKG=fsl-data_standard-2208.0-0.tar.bz2
SHA=2faefb4d366b6427cbf4e5c3e54c63c040ec9bcfe11b1f10848f89a1571b828a
echo "$SHA  $PKG" | sha256sum -c || { echo "FAIL: $PKG does not match recipes/fsl's lock"; exit 1; }
printf '@EXPLICIT\nfile:///tmp/%s#%s\n' "$PKG" "$SHA" > std.lock
micromamba create -y -q -p /tmp/std --offline -f std.lock > /tmp/template.log 2>&1 \
  || { echo "FAIL: the package did not unpack"; tail -20 /tmp/template.log; exit 1; }
mkdir -p /tmp/mni
for f in MNI152_T1_2mm_brain.nii.gz MNI152_T1_2mm_brain_mask.nii.gz; do
  cp /tmp/std/data/standard/$f /tmp/mni/$f || { echo "FAIL: $f is not in the package"; exit 1; }
done
(cd /tmp/mni && sha256sum MNI152_T1_2mm_brain.nii.gz MNI152_T1_2mm_brain_mask.nii.gz) | tee /tmp/mni.sha256
test -s /tmp/mni/MNI152_T1_2mm_brain.nii.gz && test -s /tmp/mni/MNI152_T1_2mm_brain_mask.nii.gz || { echo "FAIL: empty"; exit 1; }
echo DONE
