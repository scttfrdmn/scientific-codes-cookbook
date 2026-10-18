#!/usr/bin/env bash
# One NFBS subject through ANTs: the recipe's identities on a real scan, then the standard
# antsRegistrationSyNQuick.sh normalisation to MNI152 2 mm (rigid + affine + SyN).
# Called by run.sh as `subject.sh <id>`, many at once; ITK single-threaded, so the parallelism is
# across subjects. Writes one TSV row to /tmp/res/<id>.tsv -- or a FAIL row naming the step.
set +e
set -uo pipefail
id=$1
D=/tmp/nfbs/NFBS_Dataset/$id
W=/tmp/work/$id; mkdir -p "$W"; cd "$W"
MNI=/tmp/MNI152_T1_2mm_brain.nii.gz
MNIM=/tmp/MNI152_T1_2mm_brain_mask.nii.gz
out=/tmp/res/$id.tsv
fail() { printf '%s\tFAIL\t%s\n' "$id" "$1" > "$out"; echo "FAIL $id: $1" >&2
         mkdir -p /tmp/logs/$id && cp "$W"/*.log /tmp/logs/$id/ 2>/dev/null; exit 1; }
now() { date +%s.%N; }
lastnum() { awk '{for(i=NF;i>0;i--) if ($i ~ /^-?[0-9.]+(e[-+]?[0-9]+)?$/) {v=$i; break}} END{print v}'; }
total() { ImageMath 3 "$W/_t.txt" total "$1" 2>&1 | lastnum; }
dice() { ImageMath 3 "$W/_ov.nii.gz" m "$1" "$2" >/dev/null 2>&1
         awk -v o="$(total "$W/_ov.nii.gz")" -v a="$(total "$1")" -v b="$(total "$2")" 'BEGIN{printf "%.6f", 2*o/(a+b)}'; }
pearson() { ImageMath 3 "$W/_p.txt" PearsonCorrelation "$1" "$2" 2>&1 | lastnum; }
# label-1 row of ImageIntensityStatistics: Label Mean Sigma Skew Kurt Entropy Sum 5th 95th Min Max
stats1() { ImageIntensityStatistics 3 "$1" "$2" 2>/dev/null | awk '$1 == 1 {print $2, $9, $10, $11}'; }
params() { awk '/^Parameters:/{$1=""; print} /^FixedParameters:/{$1=""; print}' "$1" | tr '\n' ' '; }

br=$(ls "$D"/*_T1w_brain.nii.gz) && mk=$(ls "$D"/*_T1w_brainmask.nii.gz) || fail inputs
ThresholdImage 3 "$mk" mask.nii.gz 0.5 1e30 1 0 >/dev/null 2>&1 || fail mask
cp "$br" brain.nii.gz

# ---- 1. identity transform, linear interpolation, on the real scan --------------------------
antsApplyTransforms -d 3 -i brain.nii.gz -r brain.nii.gz -t identity -n Linear -o id.nii.gz >/dev/null 2>&1 || fail identity
ImageMath 3 _d.nii.gz - id.nii.gz brain.nii.gz >/dev/null 2>&1; ImageMath 3 _a.nii.gz abs _d.nii.gz >/dev/null 2>&1
id_absdiff=$(total _a.nii.gz)
id_rel=$(awk -v d="$id_absdiff" -v t="$(total brain.nii.gz)" 'BEGIN{printf "%.3e", d/t}')

# ---- 2. a rigid motion planted on this subject's curated brain, undone by antsRegistration ---
read -r ox oy oz <<<"$(PrintHeader brain.nii.gz 0 | tr x ' ')"
read -r sx sy sz <<<"$(PrintHeader brain.nii.gz 1 | tr x ' ')"
read -r nx ny nz <<<"$(PrintHeader brain.nii.gz 2 | tr x ' ')"
DIR=$(PrintHeader brain.nii.gz 4 | tr x ' ')
GEOM="$ox $oy $oz $sx $sy $sz $nx $ny $nz $DIR"
VOX=$(awk -v a=$sx -v b=$sy -v c=$sz 'BEGIN{m=a; if(b>m)m=b; if(c>m)m=c; print m}')
# 6 deg about the world z axis through the volume centre, plus (4.0, -2.5, 3.0) mm (recipes/fsl's motion)
echo "$GEOM" | awk '{ for (i=1;i<=3;i++) { o[i]=$i; s[i]=$(i+3); n[i]=$(i+6) } for (i=1;i<=9;i++) d[i]=$(i+9)
  for (i=1;i<=3;i++) { c[i]=o[i]; for (j=1;j<=3;j++) c[i]+=d[(i-1)*3+j]*s[j]*(n[j]-1)/2 }
  r = 6 * 3.14159265358979323846 / 180; co = cos(r); si = sin(r)
  printf "#Insight Transform File V1.0\n#Transform 0\nTransform: AffineTransform_double_3_3\n"
  printf "Parameters: %.17g %.17g 0 %.17g %.17g 0 0 0 1 4.0 -2.5 3.0\nFixedParameters: %.17g %.17g %.17g\n", co, -si, si, co, c[1], c[2], c[3] }' > planted.txt
antsApplyTransforms -d 3 -i brain.nii.gz -r brain.nii.gz -t planted.txt -n Linear -o moving.nii.gz >/dev/null 2>&1 || fail plant
t0=$(now)
antsRegistration -d 3 --float 0 -o [rg_,rg_warped.nii.gz] --random-seed 42 -m MeanSquares[brain.nii.gz,moving.nii.gz,1,1,Regular,0.25] \
  -t Rigid[0.1] -c [100x50x20,1e-8,10] -s 2x1x0vox -f 4x2x1 -n Linear > reg_rigid.log 2>&1 || fail rigid
t1=$(now)
ConvertTransformFile 3 rg_0GenericAffine.mat rg.txt >/dev/null 2>&1 || fail convert_rigid
# P(T(x)) must be x (antsApplyTransforms samples moving(x) = fixed(P(x))); the residual is its
# largest displacement over the 8 corners of the volume, and the planted motion is measured the same way
read -r planted_mm resid_mm <<<"$(echo "$GEOM $(params planted.txt) $(params rg.txt)" | awk '
  function ap(M, x, y,   i, j) { for (i=1;i<=3;i++) { y[i]=M[13+i-1]+M[9+i]; for (j=1;j<=3;j++) y[i]+=M[(i-1)*3+j]*(x[j]-M[12+j]) } }
  { for (i=1;i<=3;i++) { o[i]=$i; s[i]=$(i+3); n[i]=$(i+6) } for (i=1;i<=9;i++) d[i]=$(i+9)
    for (i=1;i<=15;i++) { P[i]=$(18+i); T[i]=$(33+i) }
    pm=0; rm=0
    for (a=0;a<=1;a++) for (b=0;b<=1;b++) for (e=0;e<=1;e++) { k[1]=a*(n[1]-1); k[2]=b*(n[2]-1); k[3]=e*(n[3]-1)
      for (i=1;i<=3;i++) { x[i]=o[i]; for (j=1;j<=3;j++) x[i]+=d[(i-1)*3+j]*s[j]*k[j] }
      ap(P, x, y); q=0; for (i=1;i<=3;i++) q+=(y[i]-x[i])^2; if (sqrt(q)>pm) pm=sqrt(q)
      ap(T, x, y); ap(P, y, z); q=0; for (i=1;i<=3;i++) q+=(z[i]-x[i])^2; if (sqrt(q)>rm) rm=sqrt(q) }
    printf "%.6f %.6f\n", pm, rm }')"
# A failed registration leaves the planted motion itself (>10 mm at the corners)

# ---- 3. the brain registered to itself must return the identity ----------------------------
antsRegistration -d 3 --float 0 -o [r0_,r0_warped.nii.gz] -m MeanSquares[brain.nii.gz,brain.nii.gz,1] \
  -t Rigid[0.1] -c [20x10,1e-8,10] -s 1x0vox -f 2x1 -n Linear > reg_self.log 2>&1 || fail self
ConvertTransformFile 3 r0_0GenericAffine.mat r0.txt >/dev/null 2>&1 || fail convert_self
self_dev=$(params r0.txt | awk '{m=0; for (i=1;i<=12;i++) { v=$i-((i==1||i==5||i==9)?1:0); if (v<0) v=-v; if (v>m) m=v } printf "%.3e", m}')

# ---- 4. the standard normalisation: antsRegistrationSyNQuick.sh to MNI152 2 mm --------------
t2=$(now)
antsRegistrationSyNQuick.sh -d 3 -f $MNI -m brain.nii.gz -t s -n 1 -e 42 -o s_ > syn.log 2>&1 || { tail -5 syn.log >&2; fail syn; }
t3=$(now)
for f in s_0GenericAffine.mat s_1Warp.nii.gz s_1InverseWarp.nii.gz s_Warped.nii.gz; do test -s $f || fail "no $f"; done
# the curated mask carried to MNI space by the affine stage alone, and by affine + SyN
antsApplyTransforms -d 3 -i mask.nii.gz -r $MNI -t s_0GenericAffine.mat -n NearestNeighbor -o m_aff.nii.gz >/dev/null 2>&1 || fail apply_aff
antsApplyTransforms -d 3 -i mask.nii.gz -r $MNI -t s_1Warp.nii.gz -t s_0GenericAffine.mat -n NearestNeighbor -o m_syn.nii.gz >/dev/null 2>&1 || fail apply_syn
antsApplyTransforms -d 3 -i brain.nii.gz -r $MNI -t s_0GenericAffine.mat -n Linear -o w_aff.nii.gz >/dev/null 2>&1 || fail warp_aff
dice_aff=$(dice m_aff.nii.gz $MNIM); dice_syn=$(dice m_syn.nii.gz $MNIM)
r_aff=$(pearson $MNI w_aff.nii.gz); r_syn=$(pearson $MNI s_Warped.nii.gz)
# SyN is diffeomorphic: the forward warp's Jacobian determinant is positive everywhere...
CreateJacobianDeterminantImage 3 s_1Warp.nii.gz jac.nii.gz 0 0 >/dev/null 2>&1 || fail jacobian
ThresholdImage 3 jac.nii.gz jneg.nii.gz -1e30 0 1 0 >/dev/null 2>&1
jac_nonpos=$(total jneg.nii.gz)
read -r jac_mean jac_p05 jac_min jac_max <<<"$(stats1 jac.nii.gz $MNIM)"
jac_min_all=$(ImageIntensityStatistics 3 jac.nii.gz 2>/dev/null | awk 'NR==2{print $10}')
# ...and its inverse undoes it: W^-1(W(x)) - x, composed by antsApplyTransforms, inside the MNI brain
antsApplyTransforms -d 3 -r $MNI -o [ic.nii.gz,1] -t s_1InverseWarp.nii.gz -t s_1Warp.nii.gz >/dev/null 2>&1 || fail compose
for c in 0 1 2; do ImageMath 3 ic$c.nii.gz ExtractVectorComponent ic.nii.gz $c >/dev/null 2>&1; ImageMath 3 ic$c.nii.gz m ic$c.nii.gz ic$c.nii.gz >/dev/null 2>&1; done
ImageMath 3 ic2s.nii.gz + ic0.nii.gz ic1.nii.gz >/dev/null 2>&1; ImageMath 3 ic2s.nii.gz + ic2s.nii.gz ic2.nii.gz >/dev/null 2>&1
ImageMath 3 icmag.nii.gz ^ ic2s.nii.gz 0.5 >/dev/null 2>&1
read -r ic_mean ic_p05 ic_min ic_max <<<"$(stats1 icmag.nii.gz $MNIM)"
read -r ic_p95 <<<"$(ImageIntensityStatistics 3 icmag.nii.gz $MNIM 2>/dev/null | awk '$1 == 1 {print $9}')"
# what the warp did: its mean displacement magnitude inside the MNI brain (reported)
for c in 0 1 2; do ImageMath 3 w$c.nii.gz ExtractVectorComponent s_1Warp.nii.gz $c >/dev/null 2>&1; ImageMath 3 w$c.nii.gz m w$c.nii.gz w$c.nii.gz >/dev/null 2>&1; done
ImageMath 3 w2s.nii.gz + w0.nii.gz w1.nii.gz >/dev/null 2>&1; ImageMath 3 w2s.nii.gz + w2s.nii.gz w2.nii.gz >/dev/null 2>&1
ImageMath 3 wmag.nii.gz ^ w2s.nii.gz 0.5 >/dev/null 2>&1
read -r warp_mean warp_p05 warp_min warp_max <<<"$(stats1 wmag.nii.gz $MNIM)"
t4=$(now)
# digests for the cross-chip comparison: the affine as text, the warp's voxel data and header
ConvertTransformFile 3 s_0GenericAffine.mat _aff.txt >/dev/null 2>&1
aff_sha=$(sha256sum < _aff.txt | cut -c1-16)
warp_sha=$(gzip -dc s_1Warp.nii.gz | sha256sum | cut -c1-16)
rg_sha=$(sha256sum < rg.txt | cut -c1-16)
aff_params=$(params _aff.txt | awk '{for(i=1;i<=12;i++) printf "%s%s", $i, (i<12?",":"")}')

printf '%s\tOK\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' \
  "$id" "$VOX" "$id_absdiff" "$planted_mm" "$resid_mm" "$self_dev" "$dice_aff" "$dice_syn" "$r_aff" "$r_syn" \
  "$jac_nonpos" "$jac_min_all" "$jac_min" "$jac_max" "$ic_mean" "$ic_p95" "$ic_max" "$warp_mean" "$warp_max" \
  "$(awk -v a=$t0 -v b=$t1 'BEGIN{printf "%.1f", b-a}')" "$(awk -v a=$t2 -v b=$t3 'BEGIN{printf "%.1f", b-a}')" \
  "$(awk -v a=$t3 -v b=$t4 'BEGIN{printf "%.1f", b-a}')" "$aff_sha" "$warp_sha" "$rg_sha" "$aff_params" "$id_rel" > "$out"
# keep the logs of the first subjects for diagnosis; everything else this subject built goes
mkdir -p /tmp/logs/$id && cp syn.log reg_rigid.log rg.txt _aff.txt /tmp/logs/$id/ 2>/dev/null
cd /tmp && rm -rf "$W"
