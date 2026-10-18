#!/usr/bin/env bash
# measurements/relion-real: the RELION 5.0 tutorial's first gold-standard 3D auto-refinement
# (Refine3D/job019: 4,452 beta-galactosidase particles, box 256 at 1.2445 A/px, D2), run end to
# end on CPU with the depositors' own flags, then compared against the depositors' own result.
#   run.sh <MPI ranks> <threads per rank> [cpu]
set +e
set -uo pipefail
NP=${1:?MPI ranks}; NT=${2:?threads per rank}
export PATH=/opt/conda/bin:/usr/local/bin:$PATH HOME=/tmp TMPDIR=/tmp
# Open MPI inside a container: no ssh agent is needed for a single-node launch, and the idle
# leader rank makes NP one more than the cores the followers fill.
export OMPI_MCA_plm_rsh_agent=false OMPI_MCA_rmaps_base_oversubscribe=yes \
       OMPI_MCA_btl_vader_single_copy_mechanism=none
cd /tmp

echo "== host =="
uname -m; nproc
lscpu 2>/dev/null | grep -E '^(Model name|NUMA node\(s\)|Thread\(s\) per core|Core\(s\) per socket)' || true

echo "== the staged project files must be the pinned bytes =="
# (no --quiet: the x86 image's BusyBox sha256sum does not take it)
( cd /tmp/Tutorial5.0 && sha256sum -c /tmp/inputs.sha256 > /tmp/inputs-check.txt ) \
  || { echo "FAIL: a staged input does not match its pin"; exit 1; }
echo "all $(wc -l < /tmp/inputs.sha256) staged files match their pins"

# A RELION project directory of our own, written by the container. The staged inputs are read
# through symlinks, so nothing the task writes lands in (or deletes from) a staged directory.
mkdir -p /tmp/proj/Refine3D /tmp/proj/PostProcess /tmp/proj/Class3D /tmp/proj/MaskCreate
cd /tmp/proj
ln -s /tmp/Tutorial5.0/Extract Extract
ln -s /tmp/Tutorial5.0/mtf_k2_200kV.star mtf_k2_200kV.star
ln -s /tmp/Tutorial5.0/Class3D/job016 Class3D/job016
ln -s /tmp/Tutorial5.0/MaskCreate/job020 MaskCreate/job020
ln -s /tmp/Tutorial5.0/Refine3D/job019 Refine3D/job019
ln -s /tmp/Tutorial5.0/PostProcess/job021 PostProcess/job021
mkdir -p Refine3D/job100 PostProcess/job101 PostProcess/job102

# RELION's vectorised CPU kernels (--cpu) are opt-in, and only the x86 build has them (the arm64
# build's --help does not list the flag). Off by default, so both chips run the same code path;
# pass "cpu" as the third argument to turn them on where the build has them.
CPUFLAG=""
relion_refine --help > /tmp/refine-help.txt 2>&1   # --help exits non-zero; read the file, not a pipe
HAS=no; grep -q -- '--cpu ' /tmp/refine-help.txt && HAS=yes
[ "${3:-}" = cpu ] && [ "$HAS" = yes ] && CPUFLAG="--cpu"
echo "accelerated CPU kernels: in build: $HAS; used: ${CPUFLAG:-no}"

CG=/sys/fs/cgroup
sampler() { : > /tmp/samples.tsv; local n=0
  while : ; do
    printf '%s\t%s\t%s\t%s\n' "$(date +%s)" "$(cat $CG/memory.current 2>/dev/null || echo 0)" \
      "$(awk '/^usage_usec/{print $2; exit}' $CG/cpu.stat 2>/dev/null || echo 0)" \
      "$(df -k /tmp 2>/dev/null | awk 'NR==2{print $3}')" >> /tmp/samples.tsv
    n=$((n+1)); [ $((n % 30)) -eq 0 ] && echo "[$(date -u +%H:%M:%S)] $(grep -E 'Auto-refine: (Iteration|Resolution)' Refine3D/job100/run.out 2>/dev/null | tail -2 | tr '\n' ' ')"
    sleep 10
  done; }
sampler & SPID=$!

# The depositors' command (Refine3D/job019/note.txt) verbatim, except: --gpu is dropped, the
# rank/thread layout is this box's, and --random_seed is the seed their run recorded in
# run_optimiser.star, so the half-set split is reproducible against theirs.
echo "== relion_refine_mpi: $NP ranks x $NT threads =="
T0=$(date +%s)
mpirun --bind-to none -np "$NP" relion_refine_mpi --o Refine3D/job100/run --auto_refine --split_random_halves \
  --i Extract/job018/particles.star --ref Class3D/job016/run_it025_class002_box256.mrc --firstiter_cc \
  --ini_high 50 --dont_combine_weights_via_disc --preread_images --pool 30 --pad 1 --auto_ignore_angles \
  --auto_resol_angles --ctf --particle_diameter 200 --flatten_solvent --zero_mask --oversampling 1 \
  --healpix_order 2 --auto_local_healpix_order 4 --offset_range 5 --offset_step 2 --sym D2 \
  --low_resol_join_halves 40 --norm --scale --j "$NT" $CPUFLAG --random_seed 1697716706 \
  --pipeline_control Refine3D/job100/ > Refine3D/job100/run.out 2> Refine3D/job100/run.err
RC=$?
T1=$(date +%s)
kill $SPID 2>/dev/null; wait $SPID 2>/dev/null
echo "refine_exit	$RC"
echo "refine_wall_s	$((T1-T0))"
awk -F'\t' -v wall="$((T1-T0))" 'NR==1{cu0=$3} {if($2>mc)mc=$2; if($4>tk)tk=$4; cu=$3}
  END{printf "peak_rss_gib\t%.2f\ntmpfs_peak_gib\t%.2f\n", mc/2^30, tk/2^20; if(wall>0) printf "avg_cores\t%.2f\n", (cu-cu0)/(wall*1e6)}' /tmp/samples.tsv
for f in $CG/memory.peak; do [ -r "$f" ] && awk '{printf "cgroup_peak_gib\t%.2f\n", $1/2^30}' "$f"; done
grep -E 'Auto-refine: Iteration' Refine3D/job100/run.out | tail -1

# Post-process exactly as the depositors did (PostProcess/job021/note.txt): ours, and theirs as
# a control. --o is absolute: see recipes/relion (bioconda's missing ghostscript).
PP="--mask MaskCreate/job020/mask.mrc --angpix 1.244 --mtf mtf_k2_200kV.star --mtf_angpix 0.885 --auto_bfac --autob_lowres 10"
echo "== relion_postprocess: our half-maps, and the depositors' as a control =="
relion_postprocess $PP --i Refine3D/job100/run_half1_class001_unfil.mrc --o /tmp/proj/PostProcess/job101/postprocess \
  > PostProcess/job101/run.out 2>&1; echo "postprocess_ours_exit	$?"
relion_postprocess $PP --i Refine3D/job019/run_half1_class001_unfil.mrc --o /tmp/proj/PostProcess/job102/postprocess \
  > PostProcess/job102/run.out 2>&1; echo "postprocess_control_exit	$?"

# Map-to-map FSC of our final map against the depositors' (reported, not asserted).
relion_image_handler --i Refine3D/job100/run_class001.mrc --fsc Refine3D/job019/run_class001.mrc \
  --angpix 1.244531 > /tmp/xfsc.txt 2>&1; echo "cross_fsc_exit	$?"

python3 -u /tmp/check.py "$NP" "$NT" 2>&1 | tee /tmp/check.txt
CHK=${PIPESTATUS[0]}

cd /tmp/proj
echo "== output digests (for run-to-run and cross-chip comparison) =="
sha256sum Refine3D/job100/run_half1_class001_unfil.mrc Refine3D/job100/run_half2_class001_unfil.mrc \
  Refine3D/job100/run_class001.mrc Refine3D/job100/run_data.star PostProcess/job101/postprocess.star 2>&1
tar -cf /tmp/relion-real-out.tar Refine3D/job100/run.out Refine3D/job100/run.err Refine3D/job100/run_model.star \
  Refine3D/job100/run_data.star Refine3D/job100/run_optimiser.star PostProcess/job101/postprocess.star \
  PostProcess/job101/run.out PostProcess/job102/postprocess.star PostProcess/job102/run.out 2>/dev/null
cp Refine3D/job100/run_half1_class001_unfil.mrc /tmp/run_half1_class001_unfil.mrc 2>/dev/null
cp Refine3D/job100/run_half2_class001_unfil.mrc /tmp/run_half2_class001_unfil.mrc 2>/dev/null
cp Refine3D/job100/run_class001.mrc /tmp/run_class001.mrc 2>/dev/null
[ "$RC" = 0 ] && [ "$CHK" = 0 ] && echo "ALL CHECKS PASSED" || { echo "FAIL: refine rc=$RC check rc=$CHK"; exit 1; }
