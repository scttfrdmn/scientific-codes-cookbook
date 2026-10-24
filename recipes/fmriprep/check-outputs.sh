#!/usr/bin/env bash
# fMRIPrep's own CI output check for the ds005 fasttrack run (.circleci/config.yml check_outputs at
# 25.2.7), then exact identities on what the listed files contain. Runs on /tmp/out as staged from S3.
set +e
cd /tmp
OUT=/tmp/out
: > smoke-check.txt
say()  { printf '%s\t%s\n' "$1" "$2" | tee -a smoke-check.txt; }
fail() { echo "FAIL: $*" | tee -a smoke-check.txt; exit 1; }

# 1. CI: find <out> prune figures/log/sourcedata, paths relative to <out>, sorted, diff vs the list
find "$OUT" \( -path '*/figures' -o -path '*/log' -o -path '*/sourcedata' \) -prune -o -name "*" -print \
  | cut -d/ -f 4- | LC_ALL=C sort > outputs.out
LC_ALL=C sort ds005_fasttrack_outputs.txt > fasttrack.out
diff fasttrack.out outputs.out > outputs.diff; RET=$?
say expected_paths "$(wc -l < fasttrack.out)"
say produced_paths "$(wc -l < outputs.out)"
say diff_lines "$(wc -l < outputs.diff)"
[ "$RET" = 0 ] || { cat outputs.diff | tee -a smoke-check.txt; fail "outputs differ from fMRIPrep's committed CI list"; }

# 2. the run says what it is, and its report says it finished clean
ver=$(grep -o '"Version": *"[^"]*"' "$OUT/dataset_description.json" | head -n 1 | cut -d'"' -f4)
say fmriprep_version "$ver"
[ "$ver" = "25.2.7" ] || fail "dataset_description.json reports fMRIPrep $ver, not 25.2.7"
say report_no_errors "$(grep -c 'No errors to report' "$OUT/sub-01.html")"
grep -q 'No errors to report' "$OUT/sub-01.html" || fail "sub-01.html does not report a clean run"

# GIFTI header helpers: every <DataArray ...> open tag on one line
arrays() { tr '\n' ' ' < "$1" | grep -o '<DataArray[^>]*>'; }
dim0()   { grep -o 'Dim0="[0-9]*"' | cut -d'"' -f2; }
F=$OUT/sub-01/func; A=$OUT/sub-01/anat
for run in 01 02; do
  b=sub-01_task-mixedgamblestask_run-$run
  # 3. timepoints: the input BOLD's dim[4] == confounds rows == DataArrays in every surface series
  nii=/tmp/func/${b}_bold.nii.gz
  [ "$(gzip -dc "$nii" | od -An -t d4 -N 4 | tr -d ' ')" = 348 ] || fail "$nii: not a little-endian NIfTI-1 header"
  T=$(gzip -dc "$nii" | od -An -t d2 -j 48 -N 2 | tr -d ' ')
  rows=$(awk 'END{print NR-1}' "$F/${b}_desc-confounds_timeseries.tsv")
  say "run-${run}_input_volumes" "$T"
  say "run-${run}_confounds_rows" "$rows"
  [ "$T" -gt 0 ] && [ "$rows" = "$T" ] || fail "run-$run: $rows confounds rows for $T volumes"
  for h in L R; do
    for sp in fsaverage5 fsnative; do
      g=$F/${b}_hemi-${h}_space-${sp}_bold.func.gii
      n=$(arrays "$g" | wc -l | tr -d ' ')
      v=$(arrays "$g" | dim0 | sort -u)
      say "run-${run}_${h}_${sp}" "${n} timepoints x ${v} vertices"
      [ "$n" = "$T" ] || fail "$g: $n timepoints, the BOLD has $T"
      # 4. fsaverage5 is ico5: exactly 10*4^5+2 = 10242 vertices per hemisphere
      [ "$sp" = fsaverage5 ] && { [ "$v" = 10242 ] || fail "$g: $v vertices, fsaverage5 has 10242"; }
      # 5. fsnative is the subject's own mesh: its vertex count is the white surface's
      if [ "$sp" = fsnative ]; then
        w=$(arrays "$A/sub-01_hemi-${h}_desc-preproc_white.surf.gii" | grep 'NIFTI_INTENT_POINTSET' | dim0)
        [ -n "$w" ] && [ "$v" = "$w" ] || fail "$g: $v vertices, the subject's white surface has $w"
      fi
    done
  done
done
echo "FMRIPREP OK: all $(wc -l < fasttrack.out) paths of fMRIPrep's own CI list, nothing extra; identities hold" | tee -a smoke-check.txt
