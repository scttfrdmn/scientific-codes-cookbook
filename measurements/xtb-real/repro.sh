#!/usr/bin/env bash
# Clean-room reproduction of the x86 gradient fault with upstream's own release binary, for an upstream
# report. GMTKN55 ACONF/B_G (gauche butane, neutral singlet: no .CHRG/.UHF in that entry). Every xtb call
# runs with --norestart in its own fresh `mktemp -d` holding only `coord`.
#   repro.sh            (in the x86 micromamba image; network for the release download)
set +e
set -uo pipefail
export TMPDIR=/tmp HOME=/tmp LC_ALL=C OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1
O=/tmp/out; mkdir -p $O /tmp/g
exec > >(tee $O/repro.txt) 2>&1
( cd /tmp/in && sha256sum -c gmtkn55-v1.sha256 ) || { echo "FAIL: archive sha256"; exit 1; }
tar -xzf /tmp/in/gmtkn55-v1.tar.gz -C /tmp/g --strip-components=1
S=ACONF/B_G
ls -a /tmp/g/$S
# a python only to download and unpack (the image has neither curl nor xz)
micromamba create -y -q -p /tmp/py -c conda-forge python=3.12 > /tmp/solve.log 2>&1 || { tail /tmp/solve.log; exit 1; }
cd /tmp && /tmp/py/bin/python3 - <<'PY'
import hashlib, tarfile, urllib.request as u
b = "https://github.com/grimme-lab/xtb/releases/download/v6.7.1/xtb-6.7.1-linux-x86_64.tar.xz"
u.urlretrieve(b, "x.tar.xz")
h = hashlib.sha256(open("x.tar.xz", "rb").read()).hexdigest()
print("release sha256", h)
assert h == "62a8d18778286e815292ee53d76ce447daf460a4dea3782c0f25cbac7019b5df"
t = tarfile.open("x.tar.xz")
for m in t.getmembers():
    m.name = m.name.split("/", 1)[1] if "/" in m.name else "."
    t.extract(m, "/tmp/up", filter="tar")
PY
X=/tmp/up/bin/xtb
export XTBPATH=/tmp/up/share/xtb
$X --version 2>&1 | grep -E 'version|compiled'
lscpu | grep -E '^Model name'
echo "== input: $S/coord (Bohr)"; cat /tmp/g/$S/coord
CHRG=(); [ -f /tmp/g/$S/.CHRG ] && CHRG=(--chrg "$(cat /tmp/g/$S/.CHRG)")
UHF=();  [ -f /tmp/g/$S/.UHF ]  && UHF=(--uhf "$(cat /tmp/g/$S/.UHF)")

energy() { awk '/TOTAL ENERGY/{e=$4} END{print e}' "$1"; }
# torque sum_i (r_i - centroid) x g_i, and the gradient rows, from a Turbomole `gradient` file
torque() {
  awk '/^\$grad/{on=1;next} /^\$end/{on=0} on&&NF==4{n++;x[n]=$1;y[n]=$2;z[n]=$3} on&&NF==3{gsub(/[Dd]/,"e");m++;gx[m]=$1;gy[m]=$2;gz[m]=$3}
    END{for(i=1;i<=n;i++){cx+=x[i]/n;cy+=y[i]/n;cz+=z[i]/n}
        for(i=1;i<=n;i++){X=x[i]-cx;Y=y[i]-cy;Z=z[i]-cz;tx+=Y*gz[i]-Z*gy[i];ty+=Z*gx[i]-X*gz[i];tz+=X*gy[i]-Y*gx[i];sx+=gx[i];sy+=gy[i];sz+=gz[i]}
        printf "torque  %.6e %.6e %.6e\nsum_g   %.6e %.6e %.6e\n",tx,ty,tz,sx,sy,sz}' "$1"
}
# coord with atom $2 (1-based), axis $3 (1..3) shifted by $4 Bohr
shift_coord() {
  awk -v a="$2" -v c="$3" -v h="$4" '/^\$coord/{on=1;print;next} /^\$/{on=0} on{k++; if(k==a){$c=sprintf("%.14f",$c+h)} print; next} {print}' "$1"
}

for MODE in native tblite; do
  F=(); [ $MODE = tblite ] && F=(--tblite)
  echo; echo "################ $MODE"
  for ACC in 1.0 0.0001; do
    d=$(mktemp -d); cp /tmp/g/$S/coord $d/coord
    echo "+ cd $d && xtb coord --gfn 2 --grad --norestart --acc $ACC ${CHRG[*]} ${UHF[*]} ${F[*]}"
    ( cd $d && $X coord --gfn 2 --grad --norestart --acc $ACC "${CHRG[@]}" "${UHF[@]}" "${F[@]}" > out.txt 2>&1; echo "rc $?" )
    grep -E 'TOTAL ENERGY|GRADIENT NORM' $d/out.txt
    ls $d | tr '\n' ' '; echo
    if [ -s $d/gradient ]; then torque $d/gradient; cp $d/gradient $O/gradient.$MODE.acc$ACC; fi
    [ $ACC = 0.0001 ] && G=$d/gradient
  done
  [ -s "${G:-}" ] || { echo "no gradient from $MODE"; continue; }
  # analytic gradient of atom 1 (the worst atom in the measurement), against a central difference of
  # the energy, h = 1e-3 Bohr, each displaced point --norestart in its own fresh dir, --acc 0.0001
  for c in 1 2 3; do
    ga=$(awk '/^\$grad/{on=1;next} /^\$end/{on=0} on&&NF==3{gsub(/[Dd]/,"e");m++; if(m==1) print $'$c'}' $G)
    for s in -1 1; do
      d=$(mktemp -d); shift_coord /tmp/g/$S/coord 1 $c $(awk -v s=$s 'BEGIN{printf "%.4f", s*0.001}') > $d/coord
      ( cd $d && $X coord --gfn 2 --norestart --acc 0.0001 "${CHRG[@]}" "${UHF[@]}" "${F[@]}" > out.txt 2>&1 )
      if [ $s = 1 ]; then EP=$(energy $d/out.txt); else EM=$(energy $d/out.txt); fi
    done
    awk -v ep="$EP" -v em="$EM" -v ga="$ga" -v c=$c 'BEGIN{fd=(ep-em)/0.002; printf "atom 1 axis %d  E(+h) %.12f  E(-h) %.12f  FD %.9e  analytic %.9e  diff %.3e\n", c, ep, em, fd, ga, ga-fd}'
  done
done
echo "== exact displaced-coord check: first atom line of a +h x-displaced file"
shift_coord /tmp/g/$S/coord 1 1 0.0010 | sed -n 2p
