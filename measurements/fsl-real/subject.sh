#!/usr/bin/env bash
# One NFBS subject through BET, a planted-motion FLIRT, and FLIRT+FNIRT to MNI152 2 mm.
# Called by run.sh as `subject.sh <id>`, many at once; single-threaded, so the parallelism is
# across subjects. Writes one TSV row to /tmp/res/<id>.tsv -- or a FAIL row naming the step.
set +e
set -uo pipefail
id=$1
D=/tmp/nfbs/NFBS_Dataset/$id
W=/tmp/work/$id; mkdir -p "$W"; cd "$W"
S=$FSLDIR/data/standard
out=/tmp/res/$id.tsv
fail() { printf '%s\tFAIL\t%s\n' "$id" "$1" > "$out"; echo "FAIL $id: $1" >&2; exit 1; }
now() { date +%s.%N; }
vol() { fslstats "$1" -V | awk '{print $1}'; }
dice() { fslmaths "$1" -bin -mul "$2" -bin ov_tmp >/dev/null 2>&1; local o a b
         o=$(vol ov_tmp); a=$(vol "$1"); b=$(vol "$2"); awk -v o=$o -v a=$a -v b=$b 'BEGIN{printf "%.6f", 2*o/(a+b)}'; }

t1=$(ls "$D"/*_T1w.nii.gz) && mk=$(ls "$D"/*_T1w_brainmask.nii.gz) && br=$(ls "$D"/*_T1w_brain.nii.gz) || fail inputs
# FSL's documented first step: put the image in the MNI152 orientation. Applied identically to the
# T1, the curated brain and the curated mask, which share one header.
fslreorient2std "$t1" t1 && fslreorient2std "$mk" mask && fslreorient2std "$br" brain || fail reorient
fslmaths mask -bin mask

# ---- 1. BET against the curated (manually corrected) brain mask -------------------------------
REF=$(vol mask)
# (a) BET straight on the whole-head scan, neck included -- reported, the naive call
bet t1 betraw -m -f 0.5 >/dev/null 2>&1 || fail betraw
fslmaths betraw_mask -mul mask ovr; raw_dice=$(awk -v o=$(vol ovr) -v a=$(vol betraw_mask) -v r=$REF 'BEGIN{printf "%.6f", 2*o/(a+r)}')
# (b) FSL's own remedy for a scan with neck: robustfov crops to the head, BET runs there, and the
# mask is put back on the full grid (roi2full is a whole-voxel shift, so nearest-neighbour is exact)
t0=$(now)
robustfov -i t1 -r t1fov -m roi2full.mat >/dev/null 2>&1 || fail robustfov
bet t1fov betfov -m -f 0.5 >/dev/null 2>&1 || fail bet
flirt -in betfov_mask -ref t1 -applyxfm -init roi2full.mat -interp nearestneighbour -out bet_mask >/dev/null 2>&1 || fail uncrop
fslmaths t1 -mas bet_mask bet
t1s=$(now)
A=$(vol bet_mask)
fslmaths bet_mask -mul mask ovb; O=$(vol ovb)
bet_dice=$(awk -v o=$O -v a=$A -v r=$REF 'BEGIN{printf "%.6f", 2*o/(a+r)}')
bet_contain=$(awk -v o=$O -v r=$REF 'BEGIN{printf "%.6f", o/r}')
bet_sha=$(zcat bet_mask.nii.gz | sha256sum | cut -c1-16)   # the voxel data and header, not gzip framing

# ---- 2. FLIRT undoes a motion planted on this subject's own curated brain ----------------------
read nx ny nz <<<"$(fslval brain dim1) $(fslval brain dim2) $(fslval brain dim3)"
read dx dy dz <<<"$(fslval brain pixdim1) $(fslval brain pixdim2) $(fslval brain pixdim3)"
X=$(awk -v n=$nx -v d=$dx 'BEGIN{print (n-1)*d}'); Y=$(awk -v n=$ny -v d=$dy 'BEGIN{print (n-1)*d}'); Z=$(awk -v n=$nz -v d=$dz 'BEGIN{print (n-1)*d}')
VOX=$(awk -v a=$dx -v b=$dy -v c=$dz 'BEGIN{m=a; if(b>m)m=b; if(c>m)m=c; print m}')
# 6 deg about z through the volume centre, plus (4.0, -2.5, 3.0) mm -- recipes/fsl's motion, FSL mm coordinates
awk -v th=6 -v cx=$(awk -v x=$X 'BEGIN{print x/2}') -v cy=$(awk -v y=$Y 'BEGIN{print y/2}') -v tx=4.0 -v ty=-2.5 -v tz=3.0 'BEGIN{
  r = th * 3.14159265358979323846 / 180; c = cos(r); s = sin(r)
  ox = cx - (c*cx - s*cy) + tx; oy = cy - (s*cx + c*cy) + ty; oz = tz
  printf "%.12f %.12f 0 %.12f\n%.12f %.12f 0 %.12f\n0 0 1 %.12f\n0 0 0 1\n", c, -s, ox, s, c, oy, oz }' > planted.mat
flirt -in brain -ref brain -applyxfm -init planted.mat -interp trilinear -out moving >/dev/null 2>&1 || fail plant
t2=$(now)
flirt -in moving -ref brain -dof 6 -omat recovered.mat >/dev/null 2>&1 || fail flirt6
t2e=$(now)
convert_xfm -omat residual.mat -concat recovered.mat planted.mat || fail concat
disp() { awk -v X=$X -v Y=$Y -v Z=$Z '
  NF>=4 { i++; for (j=1;j<=4;j++) M[i,j]=$j }
  END { best=0
    for (a=0;a<=1;a++) for (b=0;b<=1;b++) for (c=0;c<=1;c++) { x=a*X; y=b*Y; z=c*Z
      ex=M[1,1]*x+M[1,2]*y+M[1,3]*z+M[1,4]-x; ey=M[2,1]*x+M[2,2]*y+M[2,3]*z+M[2,4]-y; ez=M[3,1]*x+M[3,2]*y+M[3,3]*z+M[3,4]-z
      d=sqrt(ex*ex+ey*ey+ez*ez); if (d>best) best=d }
    printf "%.6f", best }' "$1"; }
planted_mm=$(disp planted.mat); resid_mm=$(disp residual.mat)

# ---- 3. BET brain -> MNI152: FLIRT 12-DOF, then FNIRT with FSL's own T1 config ----------------
t3=$(now)
flirt -in bet -ref $S/MNI152_T1_2mm_brain -dof 12 -omat aff.mat >/dev/null 2>&1 || fail flirt12
t3e=$(now)
fnirt --in=t1 --aff=aff.mat --config=T1_2_MNI152_2mm --cout=warp > fnirt.log 2>&1 || { tail -5 fnirt.log >&2; fail fnirt; }
t4=$(now)
# The curated mask, carried into MNI space by each registration, against MNI152's own brain mask.
flirt -in mask -ref $S/MNI152_T1_2mm -applyxfm -init aff.mat -interp nearestneighbour -out mask_aff >/dev/null 2>&1 || fail applyaff
applywarp --ref=$S/MNI152_T1_2mm --in=mask --warp=warp --interp=nn --out=mask_nl || fail applywarp
dice_aff=$(dice mask_aff $S/MNI152_T1_2mm_brain_mask)
dice_nl=$(dice mask_nl $S/MNI152_T1_2mm_brain_mask)

awk -v a=$t0 -v b=$t1s -v c=$t2 -v d=$t2e -v e=$t3 -v f=$t3e -v g=$t4 -v id=$id \
    -v bd=$bet_dice -v bc=$bet_contain -v bv=$A -v rv=$REF -v sha=$bet_sha -v vox=$VOX \
    -v rawd=$raw_dice -v pm=$planted_mm -v rm=$resid_mm -v da=$dice_aff -v dn=$dice_nl 'BEGIN{
  printf "%s\tOK\t%s\t%s\t%d\t%d\t%s\t%s\t%s\t%s\t%s\t%s\t%.1f\t%.1f\t%.1f\t%.1f\t%s\n", id, bd, bc, bv, rv, sha, vox, pm, rm, da, dn,
    b-a, d-c, f-e, g-f, rawd }' > "$out"
# keep the BET mask for the cross-chip comparison; everything else this subject built goes
cp bet_mask.nii.gz /tmp/masks/$id.nii.gz
cd /tmp && rm -rf "$W"
