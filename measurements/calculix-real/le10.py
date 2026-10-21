#!/usr/bin/env python3
"""NAFEMS LE10 (thick elliptic plate under pressure) in CalculiX, on a refinement ladder to ~1M dof.

THE BENCHMARK. A quarter of an elliptic annulus, inner ellipse x^2/2^2 + y^2/1^2 = 1, outer
x^2/3.25^2 + y^2/2.75^2 = 1, thickness 0.6 (z in [-0.3, 0.3]); E = 210 GPa, nu = 0.3; a uniform
1 MPa pressure on the upper face. y = 0 face: uy = 0. x = 0 face: ux = 0. Outer curved face:
ux = uy = 0. The outer face's mid-plane line: uz = 0. Target: sigma_yy at D = (2, 0, 0.3) is
-5.38 MPa (NAFEMS, "The Standard NAFEMS Benchmarks", LE10).

THE MESH is built here, because the benchmark is defined by its geometry and the suite ships no
real-size mesh. C3D20R hexahedra on the map (s, t) -> ((2 + 1.25 s) cos t, (1 + 1.75 s) sin t):
straight radial lines at equal parametric angle, every boundary node (midside nodes too) on its
ellipse. The base mesh is BASE = (n_t, n_r, n_z) elements and each rung multiplies all three.

WHAT IS ASSERTED, per rung and at scale:
  * vertical equilibrium. The only z constraint is the outer mid-plane line, which carries no
    load, so its total z reaction must equal the applied load: 1 MPa times the meshed upper area,
    integrated here with 3x3 Gauss on the same 8-node faces (exact for that polynomial). Bound
    1e-6 relative: the .dat prints 7 significant digits.
  * in-plane equilibrium. No load has an x or y component, so the x reactions on x = 0 and on the
    outer face cancel, and so do the y ones: |sum RF| / sum |RF| < 1e-6, same print bound.
  * the published value, at the finest rung: sigma_yy(D) within 1% of -5.38. The reference is a
    fine-mesh FE value printed to three figures, not a closed form, so the bound is ten times its
    printed precision and no tighter.
  * convergence: the change in sigma_yy(D) between rungs shrinks at every refinement.
  * thread invariance: one rung solved at 1 thread and at THREADS threads must agree on every
    printed number to 1e-5 of its block's largest magnitude (a static solve is a fixed point;
    the reactions in unconstrained directions are round-off zeros, so a per-value relative
    comparison would be comparing noise).

REPORTED, NOT ASSERTED: CalculiX's ITERATIVE CHOLESKY on the same rungs. Its stopping rule is
built in, so it is measured against SPOOLES -- the sigma_yy(D) difference, its share of the
discretisation change, and its own equilibrium residual -- rather than held to a bound.

Run with python3 -u. Args: --ladder "1 2 4 8 16" --base "6 3 1" --threads N --iter-rungs "1 2 4 8 16"
--serial-rung 8 --out /tmp/out --work /var/tmp/le10
"""
import argparse
import gzip
import json
import math
import os
import re
import shutil
import subprocess
import sys
import time

A_IN, B_IN, A_OUT, B_OUT, THICK = 2.0, 1.0, 3.25, 2.75, 0.6
E, NU, P = 210000.0, 0.3, 1.0
TARGET = -5.38
REF_TOL = 0.01
PRINT_TOL = 1e-6
INVARIANCE_TOL = 1e-5

ap = argparse.ArgumentParser()
ap.add_argument("--ladder", default="1 2 4 8 16")
ap.add_argument("--base", default="6 3 1")
ap.add_argument("--threads", type=int, default=1)
ap.add_argument("--iter-rungs", default="1 2 4 8")
ap.add_argument("--serial-rung", type=int, default=8)
ap.add_argument("--out", default="/tmp/out")
ap.add_argument("--work", default="/var/tmp/le10")
ap.add_argument("--ccx", default=shutil.which("ccx") or "ccx")
args = ap.parse_args()
LADDER = [int(x) for x in args.ladder.split()]
BASE = [int(x) for x in args.base.split()]
ITER = [int(x) for x in args.iter_rungs.split()]
os.makedirs(args.out, exist_ok=True)
os.makedirs(args.work, exist_ok=True)

score = {}
fails = []


def rec(k, v):
    v = str(v)
    score[k] = v
    print("%s\t%s" % (k, v), flush=True)


def check(name, ok, detail):
    ok = bool(ok)
    rec(name, ("PASS " if ok else "FAIL ") + str(detail))
    if not ok:
        fails.append(name)


def dump():
    with open(os.path.join(args.out, "score.tsv"), "w") as fh:
        fh.write("observable\tvalue\n")
        for k, v in score.items():
            fh.write("%s\t%s\n" % (k, v))


# ---------------------------------------------------------------- mesh
def xyz(i, j, k, nt, nr, nz):
    t = (math.pi / 2) * i / (2 * nt)
    s = j / (2 * nr)
    ct = 0.0 if i == 2 * nt else math.cos(t)
    st = 0.0 if i == 0 else math.sin(t)
    return ((A_IN + (A_OUT - A_IN) * s) * ct, (B_IN + (B_OUT - B_IN) * s) * st,
            -THICK / 2 + THICK * k / (2 * nz))


# 8-node serendipity quad: corners (-1,-1),(1,-1),(1,1),(-1,1) then midsides 1-2, 2-3, 3-4, 4-1
Q8 = [(-1, -1), (1, -1), (1, 1), (-1, 1), (0, -1), (1, 0), (0, 1), (-1, 0)]


def q8_dN(xi, eta):
    d = []
    for a, b in Q8:
        if a and b:
            dx = 0.25 * a * (1 + b * eta) * (2 * a * xi + b * eta)
            de = 0.25 * b * (1 + a * xi) * (a * xi + 2 * b * eta)
        elif a == 0:
            dx = -xi * (1 + b * eta)
            de = 0.5 * b * (1 - xi * xi)
        else:
            dx = 0.5 * a * (1 - eta * eta)
            de = -eta * (1 + a * xi)
        d.append((dx, de))
    return d


def face_area(pts, ngp):
    g = {2: [(-1 / math.sqrt(3), 1.0), (1 / math.sqrt(3), 1.0)],
         3: [(-math.sqrt(0.6), 5 / 9), (0.0, 8 / 9), (math.sqrt(0.6), 5 / 9)]}[ngp]
    area = 0.0
    for xi, wx in g:
        for eta, we in g:
            d = q8_dN(xi, eta)
            jx = [sum(d[n][0] * pts[n][c] for n in range(8)) for c in range(2)]
            je = [sum(d[n][1] * pts[n][c] for n in range(8)) for c in range(2)]
            area += wx * we * (jx[0] * je[1] - jx[1] * je[0])
    return area


def build(m, path):
    nt, nr, nz = BASE[0] * m, BASE[1] * m, BASE[2] * m
    nid, coords = {}, []
    for k in range(2 * nz + 1):
        for j in range(2 * nr + 1):
            for i in range(2 * nt + 1):
                if (i % 2) + (j % 2) + (k % 2) <= 1:
                    nid[(i, j, k)] = len(coords) + 1
                    coords.append(xyz(i, j, k, nt, nr, nz))
    elems, top = [], []
    for c in range(nz):
        for b in range(nr):
            for a in range(nt):
                I, J, K = 2 * a, 2 * b, 2 * c
                corner = [(J, I), (J + 2, I), (J + 2, I + 2), (J, I + 2)]       # (s, t): 1..4
                mid = [(J + 1, I), (J + 2, I + 1), (J + 1, I + 2), (J, I + 1)]   # 1-2, 2-3, 3-4, 4-1
                n = ([nid[(i, j, K)] for j, i in corner] + [nid[(i, j, K + 2)] for j, i in corner] +
                     [nid[(i, j, K)] for j, i in mid] + [nid[(i, j, K + 2)] for j, i in mid] +
                     [nid[(i, j, K + 1)] for j, i in corner])
                elems.append(n)
                if c == nz - 1:
                    top.append(len(elems))
    ijk = {v: key for key, v in nid.items()}
    symy = [n for n, (i, j, k) in ijk.items() if i == 0]
    symx = [n for n, (i, j, k) in ijk.items() if i == 2 * nt]
    outer = [n for n, (i, j, k) in ijk.items() if j == 2 * nr]
    outmid = [n for n, (i, j, k) in ijk.items() if j == 2 * nr and k == nz]
    d = nid[(0, 0, 2 * nz)]
    fixx = sorted(set(symx) | set(outer))
    fixy = sorted(set(symy) | set(outer))
    # the applied load: P times the area of the meshed upper faces (nodes 5-8, 13-16)
    a3 = a2 = 0.0
    for e in top:
        n = elems[e - 1]
        pts = [coords[q - 1] for q in n[4:8] + n[12:16]]
        a3 += face_area(pts, 3)
        a2 += face_area(pts, 2)

    def nset(fh, name, ids):
        fh.write("*NSET, NSET=%s\n" % name)
        for q in range(0, len(ids), 12):
            fh.write(", ".join(str(x) for x in ids[q:q + 12]) + "\n")

    with open(path, "w") as fh:
        fh.write("*HEADING\nNAFEMS LE10 thick elliptic plate, rung %d (%d x %d x %d C3D20R)\n" % (m, nt, nr, nz))
        fh.write("*NODE, NSET=NALL\n")
        for q, (x, y, z) in enumerate(coords, 1):
            fh.write("%d, %.17g, %.17g, %.17g\n" % (q, x, y, z))
        fh.write("*ELEMENT, TYPE=C3D20R, ELSET=EALL\n")
        for q, n in enumerate(elems, 1):
            fh.write("%d, %s,\n%s\n" % (q, ", ".join(map(str, n[:15])), ", ".join(map(str, n[15:]))))
        nset(fh, "NSYMY", sorted(symy)); nset(fh, "NSYMX", sorted(symx))
        nset(fh, "NOUT", sorted(outer)); nset(fh, "NOUTMID", sorted(outmid))
        nset(fh, "NFIXX", fixx); nset(fh, "NFIXY", fixy); nset(fh, "ND", [d])
        fh.write("*MATERIAL, NAME=STEEL\n*ELASTIC\n%g, %g\n" % (E, NU))
        fh.write("*SOLID SECTION, ELSET=EALL, MATERIAL=STEEL\n")
        fh.write("*BOUNDARY\nNSYMY, 2, 2\nNSYMX, 1, 1\nNOUT, 1, 2\nNOUTMID, 3, 3\n")
        fh.write("*STEP\n*STATIC, SOLVER=@SOLVER@\n*DLOAD\n")
        for e in top:
            fh.write("%d, P2, %g\n" % (e, P))
        fh.write("*NODE PRINT, NSET=ND\nU\n")
        fh.write("*NODE PRINT, NSET=NOUTMID, TOTALS=ONLY\nRF\n")
        fh.write("*NODE PRINT, NSET=NFIXX, TOTALS=YES\nRF\n")
        fh.write("*NODE PRINT, NSET=NFIXY, TOTALS=YES\nRF\n")
        fh.write("*NODE FILE\nS\n*END STEP\n")
    return dict(nt=nt, nr=nr, nz=nz, nodes=len(coords), elements=len(elems), node_d=d,
                area3=a3, area2=a2, n_outmid=len(outmid), d_xyz=coords[d - 1])


# ---------------------------------------------------------------- run + parse
NUM = re.compile(r"^\s*-?\d")


def parse_dat(path):
    blocks, cur = {}, None
    for line in open(path):
        if " for set " in line:
            kind = line.split("(")[0].strip()
            cur = (kind, line.split(" for set ")[1].split()[0].upper())
            blocks[cur] = []
        elif cur and NUM.match(line):
            blocks[cur].append([float(x) for x in line.split()])
    return blocks


def parse_frd_stress(path):
    out, on = {}, False
    for line in open(path):
        if line.startswith(" -4") and "STRESS" in line:
            on = True
        elif on and line.startswith(" -3"):
            break
        elif on and line.startswith(" -1"):
            out[int(line[3:13])] = tuple(float(line[13 + 12 * q:25 + 12 * q]) for q in range(6))
    return out


def run(m, solver, threads, info, tag):
    job = os.path.join(args.work, "le10_%s" % tag)
    src = open(os.path.join(args.work, "le10_m%d.inp" % m)).read().replace("@SOLVER@", solver)
    open(job + ".inp", "w").write(src)
    env = dict(os.environ, OMP_NUM_THREADS=str(threads), CCX_NPROC_EQUATION_SOLVER=str(threads),
               CCX_NPROC_STIFFNESS=str(threads), CCX_NPROC_RESULTS=str(threads), OPENBLAS_NUM_THREADS="1")
    t0 = time.time()
    with open(job + ".log", "w") as lg:
        p = subprocess.Popen([args.ccx, "-i", os.path.basename(job)], cwd=args.work, stdout=lg, stderr=subprocess.STDOUT, env=env)
        _, status, ru = os.wait4(p.pid, 0)
    wall = time.time() - t0
    rc = os.waitstatus_to_exitcode(status)
    log = open(job + ".log").read()
    shutil.copy(job + ".log", os.path.join(args.out, "ccx_%s.log" % tag))
    r = dict(tag=tag, rung=m, solver=solver, threads=threads, rc=rc, wall_s=round(wall, 2),
             maxrss_gib=round(ru.ru_maxrss / 2 ** 20, 3))
    neq = re.search(r"number of equations\s*\n\s*(\d+)", log)
    r["equations"] = int(neq.group(1)) if neq else None
    r["ccx_threads_reported"] = sorted(set(re.findall(r"Using up to (\d+) cpu\(s\) for ([^.\n]+)", log)))
    if rc != 0 or not os.path.exists(job + ".dat"):
        r["error"] = log[-600:]
        return r, None, None
    shutil.copy(job + ".dat", os.path.join(args.out, "le10_%s.dat" % tag))
    dat = parse_dat(job + ".dat")
    frd = parse_frd_stress(job + ".frd")
    u = dat[("displacements", "ND")][0]
    r["uD"] = u[1:4]
    r["rf_z_outmid"] = dat[("total force", "NOUTMID")][0][2]
    fx = dat[("forces", "NFIXX")]; fy = dat[("forces", "NFIXY")]
    r["rf_x_total"] = dat[("total force", "NFIXX")][0][0]
    r["rf_x_abs"] = sum(abs(q[1]) for q in fx)
    r["rf_y_total"] = dat[("total force", "NFIXY")][0][1]
    r["rf_y_abs"] = sum(abs(q[2]) for q in fy)
    r["syy_D"] = frd[info["node_d"]][1]
    r["frd_nodes"] = len(frd)
    if m == LADDER[-1] and solver == "SPOOLES" and threads == args.threads:
        with open(job + ".frd", "rb") as a, gzip.open(os.path.join(args.out, "le10_finest_stress.frd.gz"), "wb", 6) as b:
            shutil.copyfileobj(a, b)
    for ext in (".frd", ".dat"):
        os.remove(job + ext)
    return r, dat, frd


# ---------------------------------------------------------------- main
v = subprocess.run([args.ccx, "-v"], capture_output=True, text=True)
ver = re.search(r"Version ([0-9.]+)", v.stdout + v.stderr)
rec("ccx_binary", args.ccx)
rec("ccx_version", ver.group(1) if ver else "unknown")
if not ver or ver.group(1) != "2.23":
    rec("FATAL", "ccx is not 2.23"); dump(); sys.exit(1)
rec("threads", args.threads)
rec("base_mesh", "x".join(map(str, BASE)))

infos, runs = {}, []
for m in LADDER:
    t0 = time.time()
    infos[m] = build(m, os.path.join(args.work, "le10_m%d.inp" % m))
    infos[m]["mesh_s"] = round(time.time() - t0, 2)
    print("mesh m=%d %s" % (m, json.dumps(infos[m])), flush=True)
    r, _, _ = run(m, "SPOOLES", args.threads, infos[m], "m%d_spooles_t%d" % (m, args.threads))
    print("run %s" % json.dumps(r), flush=True)
    runs.append(r)
    if "error" in r:
        rec("FATAL", "rung %d SPOOLES failed rc=%s" % (m, r["rc"])); break
    if m in ITER:
        r2, _, _ = run(m, "ITERATIVE CHOLESKY", args.threads, infos[m], "m%d_iter_t%d" % (m, args.threads))
        print("run %s" % json.dumps(r2), flush=True)
        runs.append(r2)
    if m == args.serial_rung and args.threads != 1:
        r3, _, _ = run(m, "SPOOLES", 1, infos[m], "m%d_spooles_t1" % m)
        print("run %s" % json.dumps(r3), flush=True)
        runs.append(r3)

json.dump({"infos": {str(k): v for k, v in infos.items()}, "runs": runs}, open(os.path.join(args.out, "ladder.json"), "w"), indent=1)

ok = [r for r in runs if "error" not in r]
sp = {r["rung"]: r for r in ok if r["solver"] == "SPOOLES" and r["threads"] == args.threads}
for m, r in sorted(sp.items()):
    load = P * infos[m]["area3"]
    rec("rung%d_mesh" % m, "%d el, %d nodes, %s equations" % (infos[m]["elements"], infos[m]["nodes"], r["equations"]))
    rec("rung%d_syy_D" % m, "%.5e" % r["syy_D"])
    rec("rung%d_uD" % m, " ".join("%.6e" % x for x in r["uD"]))
    rec("rung%d_wall_s" % m, r["wall_s"]); rec("rung%d_maxrss_gib" % m, r["maxrss_gib"])
    rec("rung%d_area_3x3_vs_2x2" % m, "%.12f %.12f" % (infos[m]["area3"], infos[m]["area2"]))
    check("rung%d_vertical_equilibrium" % m, abs(r["rf_z_outmid"] - load) / load < PRINT_TOL,
          "RF_z %.6e vs P*A %.9e rel %.2e" % (r["rf_z_outmid"], load, abs(r["rf_z_outmid"] - load) / load))
    check("rung%d_inplane_equilibrium" % m,
          abs(r["rf_x_total"]) / r["rf_x_abs"] < PRINT_TOL and abs(r["rf_y_total"]) / r["rf_y_abs"] < PRINT_TOL,
          "x %.2e y %.2e" % (abs(r["rf_x_total"]) / r["rf_x_abs"], abs(r["rf_y_total"]) / r["rf_y_abs"]))
rec("exact_upper_area", "%.12f" % (math.pi / 4 * (A_OUT * B_OUT - A_IN * B_IN)))

ms = sorted(sp)
if ms and ms[-1] == LADDER[-1]:
    f = sp[ms[-1]]["syy_D"]
    check("published_sigma_yy_D", abs(f - TARGET) / abs(TARGET) < REF_TOL,
          "%.5e vs %.2f (%.3f%%)" % (f, TARGET, 100 * (f - TARGET) / abs(TARGET)))
    ch = [abs(sp[b]["syy_D"] - sp[a]["syy_D"]) for a, b in zip(ms, ms[1:])]
    rec("syy_D_changes", " ".join("%.3e" % c for c in ch))
    check("syy_D_converges", all(b < a for a, b in zip(ch, ch[1:])), "each refinement changes it less")
    if len(ms) >= 3:
        f1, f2, f3 = (sp[q]["syy_D"] for q in ms[-3:])
        if (f1 - f2) * (f2 - f3) > 0:
            p = math.log(abs(f1 - f2) / abs(f2 - f3), ms[-1] / ms[-2])
            rec("syy_D_observed_order", "%.3f" % p)
            rec("syy_D_richardson", "%.5e" % (f3 + (f3 - f2) / ((ms[-1] / ms[-2]) ** p - 1)))
else:
    fails.append("ladder_incomplete")

def dat_compare(pa, pb):
    """Every printed number, block by block, scaled by the block's largest magnitude."""
    ba, bb = parse_dat(pa), parse_dat(pb)
    vals = lambda row: row[1:] if len(row) == 4 else row      # per-node rows lead with the node id
    n = nd = 0
    worst = 0.0
    for k, rows in ba.items():
        scale = max((abs(x) for row in rows for x in vals(row)), default=0.0) or 1.0
        for ra, rb in zip(rows, bb.get(k, [])):
            if len(ra) == 4 and ra[0] != rb[0]:
                worst = float("inf")
            for x, y in zip(vals(ra), vals(rb)):
                n += 1
                if math.isnan(x) or math.isnan(y):
                    worst = float("inf")
                elif x != y:
                    nd += 1
                    worst = max(worst, abs(x - y) / scale)
        if len(rows) != len(bb.get(k, [])):
            worst = float("inf")
    return n, nd, worst


for r in ok:
    if r["solver"] != "SPOOLES":
        d = sp.get(r["rung"])
        prev = [q for q in ms if q < r["rung"]]
        if d:
            load = P * infos[r["rung"]]["area3"]
            disc = abs(d["syy_D"] - sp[prev[-1]]["syy_D"]) if prev else float("nan")
            diff = abs(r["syy_D"] - d["syy_D"])
            rec("rung%d_iterative_vs_spooles" % r["rung"],
                "syy_D %.5e vs %.5e: diff %.3e = %.3f of the discretisation change; equilibrium residual %.2e "
                "(SPOOLES %.2e); wall %.1f s vs %.1f s; rss %.2f GiB vs %.2f GiB"
                % (r["syy_D"], d["syy_D"], diff, diff / disc, abs(r["rf_z_outmid"] - load) / load,
                   abs(d["rf_z_outmid"] - load) / load, r["wall_s"], d["wall_s"], r["maxrss_gib"], d["maxrss_gib"]))
    if r["threads"] == 1 and args.threads != 1:
        d = sp[r["rung"]]
        n, nd, worst = dat_compare(os.path.join(args.out, "le10_%s.dat" % d["tag"]), os.path.join(args.out, "le10_%s.dat" % r["tag"]))
        check("rung%d_thread_invariance" % r["rung"], worst < INVARIANCE_TOL,
              "1 vs %d threads: %d printed numbers, %d differ, worst %.2e of block max (wall %.1f s vs %.1f s)"
              % (args.threads, n, nd, worst, r["wall_s"], d["wall_s"]))

rec("failed_checks", ",".join(fails) or "none")
dump()
sys.exit(1 if fails else 0)
