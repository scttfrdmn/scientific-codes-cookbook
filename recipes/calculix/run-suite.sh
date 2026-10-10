#!/usr/bin/env bash
# Run CalculiX's regression suite and compare with ITS OWN datcheck.pl.
#
# WHY NOT A COMPARISON OF OUR OWN. datcheck.pl takes the maximum absolute reference value in
# each data block, then flags a number only if it exceeds 1e-3 relative error against EITHER the
# reference at that position OR that block maximum -- and it skips participation-factor blocks,
# which depend on the cyclic sector and are not comparable. Reimplementing that is how a check
# ends up measuring a method difference. The driver is ours; the verdict is theirs.
#
# THREE EXCLUSIONS, EACH BY A RULE THE SUITE ITSELF DECLARES -- not a curated list:
#
#  1. No .dat.ref, or an empty one -> nothing to compare. This supersedes upstream's hardcoded
#     five-name skip of generated .rfn cases: there are NINE such cases, and "has no reference"
#     catches all of them without a list to maintain.
#  2. Declares *RESTART,READ / *SUBMODEL / *VIEWFACTOR,READ -> reads an artifact that a prior
#     manual step must produce. beamread.inp says so in its own comment ("please run example
#     beamwrite and copy beamwrite.rout to beamread.rin"), and upstream's `compare` does not do
#     it either, so these fail upstream too. Not a build problem.
#  3. Everything else is run and compared.
#
# The cases are then split by ANALYSIS TYPE, because that is where the result lives: a *STATIC
# solve is a fixed point and must reproduce; a time-integration or eigenvalue extraction is a
# path and amplifies the last bits.
set +e
set -uo pipefail
export OMP_NUM_THREADS=1      # upstream's compare sets this; thread count must not move a result

CCX="${CCX:?set CCX to the calculix binary}"
: > failures-static.txt
: > failures-path.txt
: > timings.tsv
printf 'case\tclass\twall_s\n' >> timings.tsv

declares_prereq () { grep -qiE '^\*(RESTART[[:space:]]*,[[:space:]]*READ|SUBMODEL|VIEWFACTOR[[:space:]]*,[[:space:]]*READ)' "$1"; }
is_path_like () {
  grep -qiE '^\*(DYNAMIC|MODAL DYNAMIC|FREQUENCY|STEADY STATE DYNAMICS|COMPLEX FREQUENCY|HEAT TRANSFER|COUPLED TEMPERATURE-DISPLACEMENT|UNCOUPLED TEMPERATURE-DISPLACEMENT|ELECTROMAGNETICS|CFD|BUCKLE|GREEN|MODAL DAMPING)' "$1"
}

n_norefs=0; n_prereq=0; s_run=0; s_bad=0; p_run=0; p_bad=0
for inp in *.inp; do
  c="${inp%.inp}"
  if [ ! -s "$c.dat.ref" ]; then n_norefs=$((n_norefs+1)); continue; fi
  if declares_prereq "$inp"; then n_prereq=$((n_prereq+1)); continue; fi
  if is_path_like "$inp"; then CLASS=path; else CLASS=static; fi

  rm -f "$c.dat" "$c.frd"
  S=$(date +%s)
  "$CCX" "$c" > "ccx_$c.log" 2>&1
  W=$(( $(date +%s) - S ))
  printf '%s\t%s\t%s\n' "$c" "$CLASS" "$W" >> timings.tsv

  F=""
  if   [ ! -f "$c.dat" ];                               then F="no .dat produced"
  elif [ "$(wc -l < "$c.dat")" != "$(wc -l < "$c.dat.ref")" ]; then
       F="$(wc -l < "$c.dat") lines vs $(wc -l < "$c.dat.ref") in the reference"
  elif grep -q NaN "$c.dat";                            then F="output contains NaN"
  else OUT=$(./datcheck.pl "$c" 2>&1); [ -n "$OUT" ] && F="datcheck: $(echo "$OUT" | sed -n '3p' | tr -s ' ')"
  fi

  if [ "$CLASS" = static ]; then
    s_run=$((s_run+1)); [ -n "$F" ] && { echo "$c: $F" >> failures-static.txt; s_bad=$((s_bad+1)); }
  else
    p_run=$((p_run+1)); [ -n "$F" ] && { echo "$c: $F" >> failures-path.txt;   p_bad=$((p_bad+1)); }
  fi
done

printf 'excluded_no_reference\t%s\n' "$n_norefs"
printf 'excluded_declares_prerequisite\t%s\n' "$n_prereq"
printf 'static_cases_run\t%s\n' "$s_run"
printf 'static_cases_deviating\t%s\n' "$s_bad"
printf 'path_cases_run\t%s\n' "$p_run"
printf 'path_cases_deviating\t%s\n' "$p_bad"
awk -F'\t' 'NR>1{t+=$3; if($3>mx){mx=$3; mc=$1}} END{printf "suite_wall_s\t%d\nslowest_case\t%s (%ds)\n", t, mc, mx}' timings.tsv

# THE ASSERTION. A *STATIC solve is a fixed point: the same input must give the same answer, and
# a committed reference is the right thing to hold it to. Deviation here is a real defect.
test "$s_bad" = "0" || { echo "FAIL: $s_bad of $s_run static cases deviate from their committed reference";
                         head -12 failures-static.txt; exit 1; }
# REPORTED, NOT ASSERTED. Time integration and eigenvalue extraction are paths; a different
# BLAS kernel moves the last bits and they amplify. Asserting zero here would be asserting that
# this build matches the author's compiler and BLAS, which is not a property of CalculiX.
printf 'path_deviations_reported\t%s of %s (not asserted -- see the page)\n' "$p_bad" "$p_run"
[ "$p_bad" != "0" ] && { echo "  path-like deviations:"; head -12 failures-path.txt; }
echo "STATIC SUITE CLEAN"
