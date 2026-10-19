#!/usr/bin/env python3
"""su2-real: SU2 8.5.0 on NASA TMR's NACA 0012 SA validation case (M 0.15, Re 6e6), on the
897x257 grid the TMR comparison table was computed on, plus SU2's own regression case and
free-stream preservation on the same grid.

    python3 -u real.py <data dir> <out dir> <ranks>

Writes <out>/check.txt, results.tsv and every SU2 log + history.csv; prints SU2 REAL OK only if
every assertion holds.
"""
import csv
import os
import re
import shutil
import subprocess
import sys
import time

DATA, OUT, RANKS = sys.argv[1], sys.argv[2], int(sys.argv[3])
os.makedirs(OUT, exist_ok=True)
SU2 = shutil.which("SU2_CFD")
BASE_CFG = open(os.path.join(DATA, "turb_NACA0012_sa.cfg")).read()   # upstream's own, v8.5.0
ALPHAS = [10.0, 15.0]   # alpha 0 is not in the converged set: see the README
ITER_CAP = 8000

# NASA TMR, "2D NACA 0012 Airfoil Validation Case -- SA results"
# (tmbwg.github.io/turbmodels/naca0012_val_sa.html, "Summary of CFD SA Results"): six codes on this
# same 897x257 grid plus GGNS (grid-adaptive). TMR: "max 1% difference in lift, 4% in drag".
TMR = {  # code: CL10, CL15, CD0, CD10, CD15
    "CFL3D": (1.0909, 1.5461, 0.00819, 0.01231, 0.02124),
    "FUN3D": (1.0983, 1.5547, 0.00812, 0.01242, 0.02159),
    "NTS":   (1.0891, 1.5461, 0.00813, 0.01243, 0.02105),
    "JOE":   (1.0918, 1.5490, 0.00812, 0.01245, 0.02148),
    "SUMB":  (1.0904, 1.5446, 0.00813, 0.01233, 0.02141),
    "TURNS": (1.1000, 1.5642, 0.00830, 0.01230, 0.02140),
    "GGNS":  (1.0941, 1.5576, 0.00817, 0.01225, 0.02073),
}
TMR_TOL = {"CL": 0.01, "CD": 0.04}
# Upstream's regression, TestCases/parallel_regression.py @ v8.5.0: turb_naca0012_sa, `mpirun -n 2`,
# iteration 5 of a restart from the committed converged solution. Screen columns:
REG_COLS = ["rms[Rho]", "rms[nu]", "CL", "CD", "LinSolIter", "LinSolRes", "LinSolIterTurb", "LinSolResTurb", "HeatFlux"]
REG_X86 = [-12.037518, -16.376951, 1.080346, 0.018385, 20, -1.564108, 20, -4.180957, 0]
REG_ARM = [-12.037489, -16.376949, 1.080346, 0.018385, 20.000000, -1.564143, 20.000000, -4.180945, 0.000000]
# Fixed-point columns (both upstream lists agree): asserted to the printed digit. Residual columns
# are the noise floor of a converged restart -- upstream's own two lists differ there by up to
# 3.5e-5, beyond upstream's 1e-5 tolerance, so they are build bits: reported, not asserted.
REG_FIXED = [2, 3, 4, 6, 8]

lines, fail = [], []


def rec(s):
    s = str(s)
    print(s, flush=True)
    lines.append(s)


def chk(label, ok, detail):
    ok = bool(ok)
    rec("%-24s %s  [%s]" % (label, detail, "ok" if ok else "FAIL"))
    if not ok:
        fail.append(label)


def cfg(over, drop=(), base=BASE_CFG):
    """Upstream's cfg with keys replaced (or appended) and `drop` keys removed."""
    out, seen = [], set()
    for line in base.splitlines():
        k = line.split("=")[0].strip()
        if line.lstrip().startswith("%"):
            out.append(line)
        elif k in drop:
            continue
        elif k in over:
            out.append("%s= %s" % (k, over[k]))
            seen.add(k)
        else:
            out.append(line)
    out += ["%s= %s" % (k, v) for k, v in over.items() if k not in seen]
    return "\n".join(out) + "\n"


def env():
    e = dict(os.environ)
    e.update(OMPI_ALLOW_RUN_AS_ROOT="1", OMPI_ALLOW_RUN_AS_ROOT_CONFIRM="1", OMP_NUM_THREADS="1",
             OPENBLAS_NUM_THREADS="1", OMPI_MCA_rmaps_base_oversubscribe="yes", OMPI_MCA_osc="^ucx",
             OMPI_MCA_btl_sm_backing_directory="/tmp")
    return e


def run(name, text, files, ranks):
    d = os.path.join("/tmp/work", name)
    os.makedirs(d, exist_ok=True)
    open(os.path.join(d, "case.cfg"), "w").write(text)
    shutil.copy(os.path.join(d, "case.cfg"), os.path.join(OUT, name + ".cfg"))
    for f in files:
        dst = os.path.join(d, f)
        if not os.path.exists(dst):
            os.symlink(os.path.join(DATA, f), dst)
    argv = ["mpirun", "--oversubscribe", "-n", str(ranks), SU2, "case.cfg"]
    t0 = time.time()
    with open(os.path.join(OUT, name + ".log"), "w") as log:
        rc = subprocess.run(argv, cwd=d, stdout=log, stderr=subprocess.STDOUT, env=env()).returncode
    wall = time.time() - t0
    if os.path.exists(os.path.join(d, "history.csv")):
        shutil.copy(os.path.join(d, "history.csv"), os.path.join(OUT, name + ".history.csv"))
    text = open(os.path.join(OUT, name + ".log"), errors="replace").read()
    rec("%-24s exit %d, wall %.1f s" % (name, rc, wall))
    return rc, wall, text, d


def rows(text):
    """Screen rows `| it| v| v|...|` after 'Begin Solver'."""
    out, on = [], False
    for line in text.splitlines():
        if "Begin Solver" in line:
            on = True
            continue
        s = line.strip()
        if on and s.startswith("|") and s.endswith("|"):
            f = [x.strip() for x in s[1:-1].split("|")]
            try:
                out.append([int(f[0])] + [float(x) for x in f[1:]])
            except ValueError:
                continue
    return out


def history(d):
    with open(os.path.join(d, "history.csv")) as fh:
        r = list(csv.reader(fh))
    head = [h.strip().strip('"') for h in r[0]]
    return [{k: float(v) for k, v in zip(head, row)} for row in r[1:] if row]


def cores(text):
    m = re.search(r"Cores:\s+(\d+)", text)
    return int(m.group(1)) if m else None


rec("SU2 8.5.0 -- NACA 0012 SA, TMR 897x257 grid; %d MPI ranks" % RANKS)
h = open(os.path.realpath(SU2), "rb").read(20)
em, host = int.from_bytes(h[18:20], "little"), os.uname().machine
chk("su2_native", em == {"aarch64": 183, "x86_64": 62}.get(host), "SU2_CFD ELF e_machine %d on %s" % (em, host))

# 1. SU2's own regression case, as parallel_regression.py runs it (cfg with ITER = test_iter + 1).
rc, wall, text, d = run("regression", cfg({"ITER": "6"}), ["n0012_225-65.su2", "solution_flow_sa.dat"], 2)
got = [r for r in rows(text) if r[0] == 5]
if got:
    vals = got[0][-len(REG_X86):]
    for name, ref in (("aarch64", REG_ARM), ("x86_64", REG_X86)):
        rec("regression vs upstream %-7s list: %s" % (name, ", ".join(
            "%s %+.1e" % (c, v - w) for c, v, w in zip(REG_COLS, vals, ref))))
    dfix = max(abs(vals[j] - REG_ARM[j]) for j in REG_FIXED)
    chk("regression_fixed", rc == 0 and dfix <= 5e-7,
        "iter 5 CL %.6f CD %.6f, linear iters %d/%d, heat flux %g == upstream's, max |d| %.1e"
        % (vals[2], vals[3], vals[4], vals[6], vals[8], dfix))
else:
    chk("regression_fixed", False, "no iteration-5 row (exit %d)" % rc)

# 2. Free-stream preservation on the real, highly stretched grid: every boundary far-field.
fs = cfg({"SOLVER": "EULER", "MESH_FILENAME": "n0012_897-257.su2", "RESTART_SOL": "NO",
          "MARKER_FAR": "( farfield, airfoil )", "MARKER_MONITORING": "NONE", "MARKER_PLOTTING": "NONE",
          "ITER": "200", "CONV_STARTITER": "100", "CONV_RESIDUAL_MINVAL": "-20", "MUSCL_FLOW": "NO",
          "CFL_NUMBER": "50.0", "OUTPUT_FILES": "(RESTART)", "SCREEN_WRT_FREQ_INNER": "20",
          "HISTORY_OUTPUT": "(ITER, RMS_RES)", "SCREEN_OUTPUT": "(INNER_ITER, RMS_DENSITY, RMS_ENERGY)",
          "WRT_PERFORMANCE": "YES"},
         drop=("KIND_TURB_MODEL", "SA_OPTIONS", "MARKER_HEATFLUX"))
rc, wall, text, d = run("freestream", fs, ["n0012_897-257.su2"], RANKS)
hist = history(d) if rc == 0 else []
worst = max(r["rms[Rho]"] for r in hist) if hist else 1.0
chk("freestream_897x257", rc == 0 and len(hist) >= 100 and worst <= -12,
    "Euler, uniform M 0.15 at 10 deg, all boundaries far-field: max log10(rms rho) over %d iters %.2f <= -12"
    % (len(hist), worst))

# 3. The validation case: upstream's SA config, from free stream, on the 897x257 grid, to upstream's
#    own convergence criterion (rms rho < 1e-12).
res = {}
for a in ALPHAS:
    name = "rans_a%+03d" % int(a)
    over = {"MESH_FILENAME": "n0012_897-257.su2", "RESTART_SOL": "NO", "AOA": "%.1f" % a,
            "ITER": str(ITER_CAP), "CONV_FIELD": "RMS_DENSITY", "OUTPUT_FILES": "(RESTART)",
            "OUTPUT_WRT_FREQ": "100000", "SCREEN_WRT_FREQ_INNER": "200", "WRT_PERFORMANCE": "YES",
            "HISTORY_OUTPUT": "(ITER, RMS_RES, AERO_COEFF, LINSOL)",
            "SCREEN_OUTPUT": "(INNER_ITER, RMS_DENSITY, RMS_NU_TILDE, LIFT, DRAG)"}
    rc, wall, text, d = run(name, cfg(over), ["n0012_897-257.su2"], RANKS)
    hist = history(d) if rc == 0 else []
    if not hist:
        chk(name + "_converged", False, "exit %d, no history" % rc)
        continue
    last, back = hist[-1], hist[max(0, len(hist) - 101)]
    r = dict(alpha=a, wall=wall, iters=len(hist), rho=last["rms[Rho]"], CL=last["CL"], CD=last["CD"],
             CMz=last["CMz"], dCL100=abs(last["CL"] - back["CL"]), dCD100=abs(last["CD"] - back["CD"]),
             cores=cores(text), parmetis="Calling ParMETIS" in text and "partitioning complete" in text)
    res[a] = r
    rec("%-24s %d iters, rms rho %.3f, CL %.10f CD %.10f CMz %.10f; last-100-iter change CL %.1e CD %.1e"
        % (name, r["iters"], r["rho"], r["CL"], r["CD"], r["CMz"], r["dCL100"], r["dCD100"]))
    chk(name + "_converged", "All convergence criteria satisfied" in text and r["rho"] <= -12 and r["iters"] < ITER_CAP,
        "rms rho %.3f <= -12 in %d iterations (cap %d)" % (r["rho"], r["iters"], ITER_CAP))
    chk(name + "_mpi", r["cores"] == RANKS and r["parmetis"],
        "SU2 reports Cores: %s, launched %d; ParMETIS partitioned=%s" % (r["cores"], RANKS, r["parmetis"]))

with open(os.path.join(OUT, "results.tsv"), "w") as fh:
    fh.write("alpha\twall_s\titers\trms_rho\tCL\tCD\tCMz\tdCL_last100\tdCD_last100\n")
    for a in ALPHAS:
        if a in res:
            r = res[a]
            fh.write("%g\t%.1f\t%d\t%.6f\t%.10f\t%.10f\t%.10f\t%.2e\t%.2e\n" % (
                a, r["wall"], r["iters"], r["rho"], r["CL"], r["CD"], r["CMz"], r["dCL100"], r["dCD100"]))

# 4. Published truth: TMR's seven-code SA table on this grid. Bound = TMR's own stated code-to-code
#    spread (1% lift, 4% drag) about the seven-code mean.
if all(a in res for a in ALPHAS):
    cols = {"CL10": 0, "CL15": 1, "CD10": 3, "CD15": 4}
    got = {"CL10": res[10.0]["CL"], "CL15": res[15.0]["CL"], "CD10": res[10.0]["CD"], "CD15": res[15.0]["CD"]}
    for k, j in cols.items():
        ref = [v[j] for v in TMR.values()]
        mean, lo, hi = sum(ref) / len(ref), min(ref), max(ref)
        rel = got[k] / mean - 1
        tol = TMR_TOL[k[:2]]
        chk("tmr_" + k, abs(rel) <= tol, "SU2 %.5f vs TMR 7-code mean %.5f (range %.5f-%.5f): %+.2f%%, bound +-%g%%"
            % (got[k], mean, lo, hi, 100 * rel, 100 * tol))
else:
    chk("all_alphas", False, "missing alpha runs: %s" % [a for a in ALPHAS if a not in res])

open(os.path.join(OUT, "check.txt"), "w").write("\n".join(lines) + "\n")
if fail:
    print("SU2 REAL FAILED %s" % fail)
    sys.exit(1)
print("SU2 REAL OK")
