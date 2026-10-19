#!/usr/bin/env bash
# sander single points on the Amber20 suite's JAC and STMV, the cross-code reference for OpenMM.
# Runs in the ambertools recipe's pinned image (aarch.science `md`), staged and pinned by sha256.
#   bash sander.sh
set +e
set -uo pipefail
P="${CONDA_PREFIX:-/opt/conda}"
export PATH=$P/bin:$PATH TMPDIR=/tmp HOME=/tmp LC_ALL=C
cd /tmp
mkdir -p out
sha256sum -c inputs.sha256 2>&1 | grep -v ': OK$'
sha256sum -c inputs.sha256 >/dev/null 2>&1 || { echo "FAIL: staged suite does not match its pin" | tee out/sander-check.txt; exit 1; }
tar -xzf Amber20_Benchmark_Suite.tar.gz PME/Topologies/JAC.prmtop PME/Coordinates/JAC.inpcrd \
  PME/Topologies/STMV.prmtop PME/Coordinates/STMV.inpcrd || { echo "FAIL: extract"; exit 1; }
{ echo "host $(uname -m) $(nproc) cpus"; lscpu | grep -E 'Model name|Thread\(s\) per core'; } > out/sander-host.txt
# A single point of the restart geometry, by the suite's own production settings (cut = 9 A,
# PME, ntf=2: bonds to H omitted, as SHAKE does). order = 5 because OpenMM's PME uses 5th-order
# B-splines; sander's default is 4, and matching the method is what makes the energies comparable.
# OpenMM is then given sander's own Ewald coefficient and grid (read from this mdout).
cat > sp.in <<'EOF'
single point, Amber20 suite production settings
 &cntrl
  imin=1, maxcyc=1, ncyc=0, ntx=1, ntb=1, cut=9.0, ntc=1, ntf=2, ntpr=1, igb=0,
 /
 &ewald
  order=5,
 /
EOF
# The same single point with the long-range vdW correction off (vdwmeth=0): sander's default
# correction is wrong for STMV (see the README), so the like-for-like comparison is without it on
# both sides, and the correction is checked separately against its closed form.
sed 's/order=5,/order=5, vdwmeth=0,/' sp.in > sp0.in
rc=0
for S in JAC STMV; do
  for V in "" -novdwcorr; do
    I=sp.in; [ -n "$V" ] && I=sp0.in
    T0=$(date +%s)
    sander -O -i $I -p PME/Topologies/$S.prmtop -c PME/Coordinates/$S.inpcrd -o out/sander-$S$V.mdout -r sp-$S.rst \
      > out/sander-$S$V.log 2>&1 || { echo "FAIL: sander $S$V"; tail -30 out/sander-$S$V.log; tail -30 out/sander-$S$V.mdout; rc=1; }
    echo "sander_${S}${V}_wall_s $(( $(date +%s) - T0 ))" >> out/sander-host.txt
    grep -E 'vdwmeth|Ewald Coefficient|NFFT|Interpolation order|^ BOND|^ VDWAALS|^ 1-4 NB' out/sander-$S$V.mdout | tail -8
  done
done
sander --version 2>&1 | head -2 >> out/sander-host.txt
cat out/sander-host.txt
exit $rc
