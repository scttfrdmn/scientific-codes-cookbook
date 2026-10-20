#!/usr/bin/env bash
# Is the wrong gradient the chip, the conda-forge build, or xtb's own code?  The same structures and the
# same script (diag.py: analytic gradient against a full 5-point finite difference of the same code's
# energies), against builds solved fresh from conda-forge:
#   b4    xtb 6.7.1 build 4 (2025-09: dftd4 3.7, tblite 0.4, multicharge 0.3)
#   b5    xtb 6.7.1 build 5 (2026-06: dftd4 4.2, tblite 0.6, multicharge 0.5), what comp-chem ships
#   b5mc1 build 5 with multicharge 0.5.0 build 1, the one it was compiled against (xtb#1422)
# and, in b4, tblite 0.4's own CLI (an independent GFN2 implementation) and xtb --tblite.
#   diag.sh <workers>       (in a micromamba image; network needed for the solves)
set +e
set -uo pipefail
NW=${1:?workers}
export TMPDIR=/tmp HOME=/tmp LC_ALL=C OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1
O=/tmp/out; mkdir -p $O /tmp/g
( cd /tmp/in && sha256sum -c gmtkn55-v1.sha256 ) || { echo "FAIL: archive sha256" | tee $O/diag.txt; exit 1; }
tar -xzf /tmp/in/gmtkn55-v1.tar.gz -C /tmp/g --strip-components=1
{ uname -m; lscpu | grep -E '^(Model name|Thread)'; } > $O/diag.txt
solve() {   # solve <name> <specs...>
  local n=$1; shift
  micromamba create -y -q -p /tmp/e_$n -c conda-forge "$@" "python=3.12" numpy > $O/solve_$n.log 2>&1 \
    || { echo "solve $n failed" >> $O/diag.txt; tail -20 $O/solve_$n.log >> $O/diag.txt; return 1; }
  micromamba list -p /tmp/e_$n 2>/dev/null | grep -E ' (xtb|tblite|dftd4|multicharge|mctc-lib) ' | sed "s/^/$n  /" >> $O/diag.txt
}
diag() {    # diag <env> <label> [xtb flags...]
  local e=$1 l=$2; shift 2
  PATH=/tmp/e_$e/bin:$PATH python3 -u /tmp/diag.py /tmp/g "$l" $O/diag.tsv "$NW" "$@" 2>&1 | tail -30 >> $O/diag.txt
}
if [ "${2:-}" = upstream ]; then
  # x86_64 only: upstream's own release binary of 6.7.1 (not conda-forge's), and conda-forge's 6.6.1
  solve py "xtb=6.6.1" && diag py cf-6.6.1-xtb
  cd /tmp && /tmp/e_py/bin/python3 -c "
import urllib.request as u
b = 'https://github.com/grimme-lab/xtb/releases/download/v6.7.1/xtb-6.7.1-linux-x86_64.tar.xz'
u.urlretrieve(b, 'x.tar.xz'); u.urlretrieve(b + '.sha256', 'x.sha256')" >> $O/diag.txt 2>&1
  echo "upstream release sha256 $(sha256sum x.tar.xz | cut -c1-64) published $(cut -c1-64 x.sha256)" >> $O/diag.txt
  [ "$(sha256sum x.tar.xz | cut -c1-64)" = "$(cut -c1-64 x.sha256)" ] && mkdir -p /tmp/up && /tmp/e_py/bin/python3 -c "
import tarfile
t = tarfile.open('x.tar.xz')
for m in t.getmembers():
    m.name = m.name.split('/', 1)[1] if '/' in m.name else '.'
    t.extract(m, '/tmp/up')" >> $O/diag.txt 2>&1 && ls /tmp/up/bin >> $O/diag.txt && {
    export XTBPATH=/tmp/up/share/xtb
    PATH=/tmp/up/bin:/tmp/e_py/bin:$PATH xtb --version 2>&1 | grep -E 'version|compiled' | head -2 >> $O/diag.txt
    PATH=/tmp/up/bin:/tmp/e_py/bin:$PATH python3 -u /tmp/diag.py /tmp/g upstream-6.7.1-xtb $O/diag.tsv "$NW" 2>&1 | tail -30 >> $O/diag.txt
  }
  cat $O/diag.txt
  exit 0
fi
solve b4 "xtb=6.7.1=*_4" && {
  diag b4 b4-xtb
  DIAG_ENGINE=tblite diag b4 b4-tblite-cli
  diag b4 b4-xtb--tblite --tblite
}
solve b5 "xtb=6.7.1=*_5" && {
  diag b5 b5-xtb
  DIAG_ENGINE=tblite diag b5 b5-tblite-cli
}
solve b5mc1 "xtb=6.7.1=*_5" "multicharge=0.5.0=*_1" && {
  diag b5mc1 b5mc1-xtb
  DIAG_ENGINE=tblite diag b5mc1 b5mc1-tblite-cli
}
cat $O/diag.txt
