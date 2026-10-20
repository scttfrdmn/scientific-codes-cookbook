#!/usr/bin/env bash
# openfoam-real case: SU2's NACA 0012 897x257 grid -> OpenFOAM, then rhoSimpleFoam + Spalart-Allmaras
# at TMR's conditions (M 0.15, Re 6e6 on chord 1, T 300 K, adiabatic wall), one solve per angle,
# then every check. Called by run.sh; writes /tmp/out/.
#
#   bash case.sh <ranks> <max_iterations>      (ALPHAS="10 15" by default)
set +e
set -uo pipefail
R=$1
N=$2
ALPHAS=${ALPHAS:-"10 15"}
O=/tmp/out
W=/var/tmp/work
GRID=/tmp/data/n0012_897-257.su2
export OMPI_MCA_btl_vader_backing_directory=/tmp OMPI_MCA_btl_sm_backing_directory=/tmp OMPI_MCA_btl=self,vader
mkdir -p "$O" "$W"
FAIL=0
rec() { echo "$*" | tee -a "$O/check.txt"; }
chk() { # label ok(0/1) detail
  if [ "$2" = 1 ]; then rec "$(printf '%-26s %s  [ok]' "$1" "$3")"; else rec "$(printf '%-26s %s  [FAIL]' "$1" "$3")"; FAIL=$((FAIL+1)); fi
}
: > "$O/check.txt"
rec "OpenFOAM $WM_PROJECT_VERSION rhoSimpleFoam SA -- NACA 0012, TMR 897x257 grid; $R MPI ranks, cap $N iterations"

# ---- 1. host-relative architecture: the solver's ELF e_machine is the host's (183 aarch64, 62 x86-64)
EM=$(od -An -t u2 -j 18 -N 2 "$(command -v rhoSimpleFoam)" | tr -d ' ')
case "$(uname -m)" in aarch64) WANT=183 ;; x86_64) WANT=62 ;; *) WANT=x ;; esac
chk native "$([ "$EM" = "$WANT" ] && echo 1 || echo 0)" "rhoSimpleFoam ELF e_machine $EM on $(uname -m)"

# ---- 2. the mesh: SU2's vertices, verbatim
B=$W/base
mkdir -p "$B/constant/polyMesh" "$B/system"
P=$B/constant/polyMesh
T0=$(date +%s)
awk -v out="$P" -f /tmp/su2ToFoam.awk "$GRID" > "$O/su2ToFoam.log" 2>&1 || { cat "$O/su2ToFoam.log"; echo "FAIL: su2ToFoam"; exit 1; }
cat "$O/su2ToFoam.log"
read NPT NC NINT NF < "$P/counts"
LC_ALL=C sort -n -s -k1,1 -k2,2 "$P/internal.unsorted" > "$P/internal.sorted"
hdr() { printf 'FoamFile\n{\n    version 2.0;\n    format ascii;\n    class %s;\n    location "constant/polyMesh";\n    object %s;\n}\n' "$1" "$2"; }
{ hdr faceList faces; echo "$NF"; echo "("
  awk '{print "4(" $3 " " $4 " " $5 " " $6 ")"}' "$P/internal.sorted"; cat "$P/boundary.faces"; echo ")"; } > "$P/faces"
{ hdr labelList owner; echo "$NF"; echo "("; awk '{print $1}' "$P/internal.sorted"; cat "$P/boundary.owner"; echo ")"; } > "$P/owner"
{ hdr labelList neighbour; echo "$NINT"; echo "("; awk '{print $2}' "$P/internal.sorted"; echo ")"; } > "$P/neighbour"
rm -f "$P/internal.unsorted" "$P/internal.sorted" "$P/boundary.faces" "$P/boundary.owner" "$P/counts"
echo "mesh conversion $(( $(date +%s) - T0 )) s"
printf 'FoamFile\n{\n    version 2.0;\n    format ascii;\n    class dictionary;\n    object controlDict;\n}\napplication checkMesh;\nstartFrom startTime; startTime 0; stopAt endTime; endTime 1; deltaT 1;\nwriteControl timeStep; writeInterval 1;\n' > "$B/system/controlDict"
printf 'FoamFile { version 2.0; format ascii; class dictionary; object fvSchemes; }\nddtSchemes {} gradSchemes {} divSchemes {} laplacianSchemes {} interpolationSchemes {} snGradSchemes {}\n' > "$B/system/fvSchemes"
printf 'FoamFile { version 2.0; format ascii; class dictionary; object fvSolution; }\n' > "$B/system/fvSolution"
( cd "$B" && checkMesh > log.checkMesh 2>&1 )
cp "$B/log.checkMesh" "$O/log.checkMesh"
MC=$(awk '/^ *cells:/{print $2; exit}' "$B/log.checkMesh")
MP=$(awk '/^ *points:/{print $2; exit}' "$B/log.checkMesh")
chk mesh_size "$([ "$MC" = 229376 ] && [ "$MP" = 460672 ] && echo 1 || echo 0)" "checkMesh: $MC cells, $MP points (896x256 quads; SU2's 230336 vertices x 2 planes)"
OKS=0
for s in "Boundary definition OK." "Cell to face addressing OK." "Point usage OK." "Upper triangular ordering OK." "Face vertices OK." "Number of regions: 1 (OK)." "Boundary openness" "Face pyramids OK." "Cell volumes OK."; do
  grep -q -F "$s" "$B/log.checkMesh" && OKS=$((OKS+1))
done
BO=$(grep -F "Boundary openness" "$B/log.checkMesh" | grep -c -F "OK.")
chk mesh_valid "$([ $OKS = 9 ] && [ "$BO" = 1 ] && echo 1 || echo 0)" "checkMesh topology + openness + pyramids + volumes: $OKS/9 OK; flagged: $(grep -c '\*\*\*' "$B/log.checkMesh") ($(grep '\*\*\*' "$B/log.checkMesh" | sed 's/^ *//' | cut -c1-40 | tr '\n' ';'))"
rec "mesh: $(grep -E 'Total volume|non-orthogonality Max|Max aspect ratio' "$B/log.checkMesh" | sed 's/^ *//' | tr '\n' ';')"

# ---- 3. case dictionaries. Freestream from TMR's M, Re, T; gas and Sutherland constants as SU2's defaults.
read UINF RHO PINF MU NUT_T NUT ALPHAT < <(awk 'BEGIN{
  R = 287.058; g = 1.4; T = 300; M = 0.15; Re = 6e6; c = 1
  U = M * sqrt(g * R * T)
  mu = 1.716e-5 * (T / 273.15) ^ 1.5 * (273.15 + 110.4) / (T + 110.4)
  rho = Re * mu / (U * c); p = rho * R * T; nu = mu / rho
  nt = 3 * nu; chi = 3; fv1 = chi ^ 3 / (chi ^ 3 + 7.1 ^ 3)
  printf "%.10g %.10g %.10g %.10g %.10g %.10g %.10g\n", U, rho, p, mu, nt, nt * fv1, rho * nt * fv1 / 0.85 }')
rec "freestream: U $UINF m/s, rho $RHO kg/m3, p $PINF Pa, mu $MU Pa s, nuTilda $NUT_T, nut $NUT m2/s"
fh() { printf 'FoamFile\n{\n    version 2.0;\n    format ascii;\n    class %s;\n    object %s;\n}\n' "$1" "$2"; }

cat > "$B/constant/thermophysicalProperties" <<EOF
$(fh dictionary thermophysicalProperties)
thermoType { type hePsiThermo; mixture pureMixture; transport sutherland; thermo hConst;
             equationOfState perfectGas; specie specie; energy sensibleInternalEnergy; }
mixture
{
    specie         { molWeight $(awk 'BEGIN{printf "%.10g", 8314.46261815324/287.058}'); }
    thermodynamics { Cp $(awk 'BEGIN{printf "%.10g", 3.5*287.058}'); Hf 0; }
    transport      { As $(awk 'BEGIN{printf "%.10g", 1.716e-5*(273.15+110.4)/273.15^1.5}'); Ts 110.4; }
}
EOF
cat > "$B/constant/turbulenceProperties" <<EOF
$(fh dictionary turbulenceProperties)
simulationType RAS;
RAS { RASModel SpalartAllmaras; turbulence on; printCoeffs on; }
EOF
cat > "$B/system/fvSchemes" <<EOF
$(fh dictionary fvSchemes)
ddtSchemes      { default steadyState; }
gradSchemes     { default Gauss linear; }
divSchemes
{
    default         none;
    div(phi,U)      bounded Gauss linearUpwind grad(U);
    div(phi,e)      bounded Gauss linearUpwind grad(e);
    div(phi,K)      bounded Gauss linearUpwind grad(K);
    div(phi,Ekp)    bounded Gauss linearUpwind grad(Ekp);
    div(phi,nuTilda) bounded Gauss linearUpwind grad(nuTilda);
    div(phid,p)     Gauss upwind;
    div((phi|interpolate(rho)),p) bounded Gauss upwind;
    div(((rho*nuEff)*dev2(T(grad(U))))) Gauss linear;
}
laplacianSchemes     { default Gauss linear corrected; }
interpolationSchemes { default linear; }
snGradSchemes        { default corrected; }
wallDist             { method meshWave; }
EOF
# Two stages, one fvSolution each. Stage 1 starts impulsively from free stream with the relaxation of
# OpenFOAM's own rhoSimpleFoam aerofoilNACA0012 tutorial (p 0.7, rho 0.01; U 0.5 here) -- an aggressive
# start diverges within five iterations on this grid. Stage 2 (SIMPLEC) continues from stage 1 to
# iteration N with $S2 relaxation. GAMG's maxIter is capped at 100: once p's initial residual
# reaches ~2e-9 its relTol 0.01 is below the attainable floor, and the default cap of 1000 made each
# SIMPLE iteration ~13x slower.
fvsol() { # file consistent p rho eqns
  cat > "$1" <<EOF
$(fh dictionary fvSolution)
solvers
{
    p        { solver GAMG; smoother GaussSeidel; tolerance 1e-14; relTol 0.01; maxIter 100; }
    "(U|e|nuTilda)" { solver PBiCGStab; preconditioner DILU; tolerance 1e-14; relTol 0.1; }
}
SIMPLE
{
    consistent      $2;
    nNonOrthogonalCorrectors 0;
    pMinFactor      0.5;
    pMaxFactor      2;
}
relaxationFactors
{
    fields    { p $3; rho $4; }
    equations { U $5; e $5; nuTilda $5; }
}
EOF
}
# No residualControl: every chip runs exactly N iterations, so the cross-chip comparison is at equal work.
N1=${N1:-500}
S1=${S1:-"no 0.7 0.01 0.5"}
S2=${S2:-"yes 1 1 0.9"}
rec "stage 1: $N1 iterations, consistent/p/rho/eqn relaxation $S1; stage 2: to iteration $N, $S2"
cat > "$B/system/decomposeParDict" <<EOF
$(fh dictionary decomposeParDict)
numberOfSubdomains $R;
method hierarchical;   // geometric, so every chip gets the same partition (scotch gave a different one on each box)
coeffs { n (4 4 1); }
EOF

ALL_WALL=0
: > "$O/results.tsv"
printf 'alpha\twall_s\titers\tCL\tCD\tCM\tdCL_1000\tdCL_prev1000\tdCD_1000\tdCD_prev1000\terrCL_rel\terrCD_rel\tfarfield_net_over_abs\typlus_max\tres_Ux\tres_p\tres_e\tres_nuTilda\n' > "$O/results.tsv"
for A in $ALPHAS; do
  C=$W/a$A
  rm -rf "$C"; mkdir -p "$C/0"
  cp -r "$B/constant" "$B/system" "$C/"
  read CA SA < <(awk -v a="$A" 'BEGIN{r = a * atan2(0, -1) / 180; printf "%.15g %.15g\n", cos(r), sin(r)}')
  U3="($(awk -v u="$UINF" -v c="$CA" 'BEGIN{printf "%.15g", u*c}') $(awk -v u="$UINF" -v s="$SA" 'BEGIN{printf "%.15g", u*s}') 0)"
  ff() { # file class dims internal farfield-bc wall-bc
    cat > "$C/0/$1" <<EOF
$(fh "$2" "$1")
dimensions $3;
internalField uniform $4;
boundaryField
{
    farfield { $5 }
    airfoil  { $6 }
    frontAndBack { type empty; }
}
EOF
  }
  ff U volVectorField "[0 1 -1 0 0 0 0]" "$U3" "type freestreamVelocity; freestreamValue uniform $U3; value uniform $U3;" "type noSlip;"
  ff p volScalarField "[1 -1 -2 0 0 0 0]" "$PINF" "type freestreamPressure; freestreamValue uniform $PINF; value uniform $PINF;" "type zeroGradient;"
  ff T volScalarField "[0 0 0 1 0 0 0]" "300" "type inletOutlet; inletValue uniform 300; value uniform 300;" "type zeroGradient;"
  ff nuTilda volScalarField "[0 2 -1 0 0 0 0]" "$NUT_T" "type freestream; freestreamValue uniform $NUT_T; value uniform $NUT_T;" "type fixedValue; value uniform 0;"
  ff nut volScalarField "[0 2 -1 0 0 0 0]" "$NUT" "type calculated; value uniform $NUT;" "type nutLowReWallFunction; value uniform 0;"
  ff alphat volScalarField "[1 -1 -1 0 0 0 0]" "$ALPHAT" "type calculated; value uniform $ALPHAT;" "type compressible::alphatWallFunction; value uniform 0;"
  ctl() { # endTime
  cat > "$C/system/controlDict" <<EOF
$(fh dictionary controlDict)
application rhoSimpleFoam;
startFrom latestTime; startTime 0; stopAt endTime; endTime $1; deltaT 1;
writeControl timeStep; writeInterval $1; purgeWrite 0;
writeFormat ascii; writePrecision 12; writeCompression off; timeFormat general; timePrecision 6;
runTimeModifiable false;
functions
{
    coeffs
    {
        type forceCoeffs; libs (forces); patches (airfoil);
        rho rho; rhoInf $RHO; pRef $PINF; magUInf $UINF; lRef 1; Aref 1;
        CofR (0.25 0 0); dragDir ($CA $SA 0); liftDir ($(awk -v s="$SA" 'BEGIN{printf "%.15g", -s}') $CA 0); pitchAxis (0 0 1);
        writeControl timeStep; writeInterval 1;
    }
    netFlux  { type surfaceFieldValue; libs (fieldFunctionObjects); regionType patch; name farfield;
               operation sum;    fields (phi); writeFields false; writeControl timeStep; writeInterval 1; }
    absFlux  { type surfaceFieldValue; libs (fieldFunctionObjects); regionType patch; name farfield;
               operation sumMag; fields (phi); writeFields false; writeControl timeStep; writeInterval 1; }
    yPlus    { type yPlus; libs (fieldFunctionObjects); writeControl writeTime; }
}
EOF
  }
  ctl "$N1"
  fvsol "$C/system/fvSolution" $S1
  ( cd "$C" && decomposePar -force > log.decomposePar 2>&1 ) || { tail -30 "$C/log.decomposePar"; echo "FAIL: decomposePar a$A"; exit 1; }
  awk '/^Processor [0-9]+$/{p=$2} /Number of cells = /&&p!=""{print p, $NF; p=""}' "$C/log.decomposePar" > "$O/a$A.partition.txt"
  T_0=$(date +%s)
  ( cd "$C" && mpirun --oversubscribe -np "$R" rhoSimpleFoam -parallel > log.rhoSimpleFoam.1 2>&1 )
  SRC1=$?
  ctl "$N"
  fvsol "$C/system/fvSolution" $S2
  SRC=1
  if [ $SRC1 = 0 ]; then
    ( cd "$C" && mpirun --oversubscribe -np "$R" rhoSimpleFoam -parallel > log.rhoSimpleFoam 2>&1 )
    SRC=$?
  fi
  T_1=$(date +%s); WALL=$((T_1-T_0)); ALL_WALL=$((ALL_WALL+WALL))
  touch "$C/log.rhoSimpleFoam"
  cp "$C/log.rhoSimpleFoam.1" "$O/a$A.log.rhoSimpleFoam.1"; cp "$C/log.rhoSimpleFoam" "$O/a$A.log.rhoSimpleFoam"; cp "$C/log.decomposePar" "$O/a$A.log.decomposePar"
  # each stage writes its own postProcessing/<fo>/<startTime>/ file: join them in time order
  for fo in coeffs netFlux absFlux yPlus; do
    ls -d "$C/postProcessing/$fo"/*/ 2>/dev/null | sort -t/ -k1,1 | awk -F/ '{print $(NF-1), $0}' | sort -n | while read -r _ d; do
      cat "$d"*.dat
    done > "$O/a$A.$fo.dat"
  done
  CF=$O/a$A.coeffs.dat; NF1=$O/a$A.netFlux.dat; NF2=$O/a$A.absFlux.dat; YP=$O/a$A.yPlus.dat
  rec "a$A rhoSimpleFoam exit $SRC1 (stage 1) / $SRC (stage 2), wall $WALL s"
  # ranks: OpenFOAM's own header, `nProcs : R`
  NP=$(awk '/^nProcs *:/{print $3; exit}' "$C/log.rhoSimpleFoam")
  chk "a${A}_mpi" "$([ "$NP" = "$R" ] && echo 1 || echo 0)" "rhoSimpleFoam reports nProcs $NP, launched $R"
  grep -q '^End' "$C/log.rhoSimpleFoam"; chk "a${A}_completed" "$([ $? = 0 ] && [ $SRC = 0 ] && echo 1 || echo 0)" "End sentinel, exit $SRC"
  # final-iteration initial residuals, reported
  RES=$(awk '/Solving for (Ux|p|e|nuTilda),/{split($0, a, "Initial residual = "); split(a[2], b, ","); r[$4] = b[1]}
             END{printf "%s\t%s\t%s\t%s", r["Ux,"], r["p,"], r["e,"], r["nuTilda,"]}' "$C/log.rhoSimpleFoam")
  # coefficients: columns found by name from the header. Iterative error: the change over the last
  # 1000 iterations (d1) and the 1000 before (d0) give the contraction ratio q = d1/d0, and the
  # remaining distance to the fixed point is about d1*q/(1-q).
  awk -v A="$A" -v W="$WALL" -v res="$RES" -v nf1="$NF1" -v nf2="$NF2" -v yp="$YP" -v tsv="$O/results.tsv" '
    function ab(x) { return x < 0 ? -x : x }
    function err(d1, d0, x) { q = (d0 > 0) ? d1 / d0 : 1; return (q < 1) ? d1 * q / (1 - q) / ab(x) : 1 }
    /^#/ { for (i = 2; i <= NF; i++) { h[$i] = i - 1 } next }
    { n++; t[n] = $1; cd[n] = $(h["Cd"]); cl[n] = $(h["Cl"]); cm[n] = $(h["CmPitch"]) }
    END {
      k = n - 1000; j = n - 2000; if (j < 1) { k = 1; j = 1 }
      l1 = ab(cl[n] - cl[k]); l0 = ab(cl[k] - cl[j]); d1 = ab(cd[n] - cd[k]); d0 = ab(cd[k] - cd[j])
      while ((getline l < nf1) > 0) if (l !~ /^#/) { split(l, a); net = a[2] }
      while ((getline l < nf2) > 0) if (l !~ /^#/) { split(l, a); mag = a[2] }
      while ((getline l < yp) > 0) if (l !~ /^#/ && l ~ /airfoil/) { split(l, a); ypmax = a[4] }
      r = (mag > 0) ? net / mag : 1
      printf "%s\t%d\t%d\t%.10f\t%.10f\t%.10f\t%.2e\t%.2e\t%.2e\t%.2e\t%.2e\t%.2e\t%.3e\t%s\t%s\n", A, W, t[n], cl[n], cd[n], cm[n], l1, l0, d1, d0, err(l1, l0, cl[n]), err(d1, d0, cd[n]), r, ypmax, res >> tsv
    }' "$CF"
done
echo "solve_wall_s $ALL_WALL" | tee "$O/solve_wall.txt"

# ---- 4. checks over the converged pair
# NASA TMR, "2D NACA 0012 Airfoil Validation Case -- SA results", seven codes on this same grid
# (CL10 CL15 CD10 CD15; https://tmbwg.github.io/turbmodels/naca0012_val_sa.html, as in su2-real).
# SU2 8.5.0 on the same vertices: measurements/su2-real/results/arm64/results.tsv (identical on 3 chips).
awk -v check="$O/check.txt" -v nit="$N" '
  function rec(s) { print s; print s >> check }
  function chk(l, ok, d) { rec(sprintf("%-26s %s  [%s]", l, d, ok ? "ok" : "FAIL")); if (!ok) fail++ }
  function ab(x) { return x < 0 ? -x : x }
  BEGIN {
    split("1.0909 1.0983 1.0891 1.0918 1.0904 1.1000 1.0941", c10); split("1.5461 1.5547 1.5461 1.5490 1.5446 1.5642 1.5576", c15)
    split("0.01231 0.01242 0.01243 0.01245 0.01233 0.01230 0.01225", d10); split("0.02124 0.02159 0.02105 0.02148 0.02141 0.02140 0.02073", d15)
    for (i = 1; i <= 7; i++) { m["CL10"] += c10[i] / 7; m["CL15"] += c15[i] / 7; m["CD10"] += d10[i] / 7; m["CD15"] += d15[i] / 7 }
    su["CL10"] = 1.0966662140; su["CD10"] = 0.0125969500; su["CL15"] = 1.5557882960; su["CD15"] = 0.0218698675
    tol["CL"] = 0.01; tol["CD"] = 0.04
  }
  NR == 1 { next }
  { a = $1; v["CL" a] = $4; v["CD" a] = $5; it[a] = $3; l1[a] = $7; l0[a] = $8; d1[a] = $9; d0[a] = $10; eL[a] = $11; eD[a] = $12
    flux[a] = $13; yp[a] = $14; res[a] = $15 " " $16 " " $17 " " $18; seen[a] = 1 }
  END {
    for (a in seen) {
      # iterative error at least 10x inside the TMR code-to-code band (1% lift, 4% drag), from a contracting history
      chk("a" a "_converged", it[a] == nit && l1[a] < l0[a] && d1[a] < d0[a] && eL[a] < tol["CL"] / 10 && eD[a] < tol["CD"] / 10,
          sprintf("%d iterations; last two 1000-iteration changes CL %s, %s and CD %s, %s (contracting); remaining iterative error ~ CL %s, CD %s relative, want < 1e-3 and 4e-3", it[a], l1[a], l0[a], d1[a], d0[a], eL[a], eD[a]))
      rec(sprintf("%-26s final initial residuals Ux p e nuTilda: %s", "a" a "_residuals", res[a]))
      chk("a" a "_mass", ab(flux[a]) < 1e-9, sprintf("net farfield mass flux / sum|flux| = %s, want < 1e-9 (the wall carries none)", flux[a]))
      rec(sprintf("%-26s max y+ on the airfoil %s", "a" a "_yplus", yp[a]))
      for (q = 1; q <= 2; q++) {
        k = ((q == 1) ? "CL" : "CD") a
        if (!(k in m)) continue
        rel = (v[k] - m[k]) / m[k]
        chk("tmr_" k, ab(rel) <= tol[substr(k, 1, 2)], sprintf("OpenFOAM %.5f vs TMR 7-code mean %.5f: %+.2f%%, bound +-%g%%", v[k], m[k], 100 * rel, 100 * tol[substr(k, 1, 2)]))
        rs = (v[k] - su[k]) / su[k]
        chk("su2_" k, ab(rs) <= tol[substr(k, 1, 2)], sprintf("OpenFOAM %.6f vs SU2 %.6f, same vertices: %+.2f%%, bound +-%g%% (TMR code-to-code)", v[k], su[k], 100 * rs, 100 * tol[substr(k, 1, 2)]))
      }
    }
    exit (fail > 0)
  }' "$O/results.tsv"
[ $? = 0 ] || FAIL=$((FAIL+1))
cat "$O/results.tsv"
if [ "$FAIL" = 0 ]; then echo "OPENFOAM REAL OK"; else echo "FAIL: $FAIL check(s)"; exit 1; fi
