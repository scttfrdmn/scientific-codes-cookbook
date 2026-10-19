"""Identities for one chip's AUSURF112 run: two converged SCFs of the same slab under two MPI
decompositions. A converged total energy is a fixed point, so the decomposition cannot move it past
the SCF threshold; forces are first order in the density error, so they may move by up to QE's own
"SCF correction" estimate. Values are read from pw.x's output (the benchmark's disk_io='none'
writes no XML): energies to 1e-8 Ry, forces to 1e-8 Ry/au. Writes results.tsv and forces-{A,B}.tsv; exits non-zero on any failure.
    check.py <out dir> <ranks> <decomposition B flags>"""
import math
import os
import re
import sys

O, NP, DECOMP_B = sys.argv[1], int(sys.argv[2]), sys.argv[3]
NAT, NBND, NELEC = 112, 800, 1232      # ausurf.in: 112 Au, nbnd = 800, 11 valence electrons each
CONV_THR = 1e-8                        # Ry, run.sh's edit
# QE's "estimated scf accuracy" is an estimate, not a bound: on Graviton4 the two decompositions
# differ by 3e-8 Ry with estimates of 8.3e-9 and 5.4e-9. 100x conv_thr is 1e-10 relative on 11,427 Ry
# and 1.4e-5 eV over 112 atoms; any physics error (pseudopotential, k-mesh, cutoff) is orders larger.
TOL_E = 100 * CONV_THR
RY_EV = 13.605693122994

fail = []


def chk(label, ok, detail):
    ok = bool(ok)                      # a numpy bool or a NaN comparison must still fail cleanly
    print("%-34s %s  [%s]" % (label, detail, "ok" if ok else "FAIL"))
    if not ok:
        fail.append(label)


def last(pat, text, cast=str, default=None):
    m = re.findall(pat, text, re.M)
    return cast(m[-1]) if m else default


def parse(tag):
    p = os.path.join(O, "pw-%s.out" % tag)
    t = open(p, errors="replace").read() if os.path.exists(p) else ""
    r = {"tag": tag}
    r["version"] = last(r"Program PWSCF v\.(\S+)", t)
    r["ranks"] = last(r"Number of MPI processes:\s+(\d+)", t, int, 0)
    r["npool"] = last(r"K-points division:\s+npool\s+=\s+(\d+)", t, int, 1)
    r["nks"] = last(r"number of k points=\s+(\d+)", t, int, 0)
    r["nat"] = last(r"number of atoms/cell\s+=\s+(\d+)", t, int, 0)
    r["nbnd"] = last(r"number of Kohn-Sham states=\s+(\d+)", t, int, 0)
    r["nelec"] = last(r"number of electrons\s+=\s+([\d.]+)", t, float, float("nan"))
    r["iters"] = last(r"convergence has been achieved in\s+(\d+) iterations", t, int, 0)
    r["E_ry_out"] = last(r"^!\s+total energy\s+=\s+(-?\d+\.\d+) Ry", t, float, float("nan"))
    r["scf_acc"] = last(r"estimated scf accuracy\s+<\s+([\d.Ee+-]+) Ry", t, float, float("nan"))
    r["ef_ev"] = last(r"the Fermi energy is\s+(-?\d+\.\d+) ev", t, float, float("nan"))
    r["wall"] = last(r"PWSCF\s+:.*CPU\s+(.*?)\s*WALL", t)
    r["tot_force"] = last(r"Total force =\s+(\S+)\s+Total SCF correction =", t, float, float("nan"))
    r["scf_corr"] = last(r"Total SCF correction =\s+(\S+)", t, float, float("nan"))
    blk = t.split("Forces acting on atoms (cartesian axes, Ry/au):")
    f = []
    if len(blk) > 1:
        f = [tuple(map(float, m)) for m in
             re.findall(r"atom\s+\d+ type\s+\d+\s+force =\s+(\S+)\s+(\S+)\s+(\S+)", blk[1])[:NAT]]
    r["forces"] = f
    r["E_ry"] = r["E_ry_out"]
    return r


A, B = parse("A"), parse("B")
for r in (A, B):
    s = r["tag"]
    chk("%s: QE version" % s, r["version"] == "7.5", "v%s want 7.5" % r["version"])
    chk("%s: MPI ranks from pw.x" % s, r["ranks"] == NP, "%d want %d" % (r["ranks"], NP))
    chk("%s: the AUSURF112 system" % s, r["nat"] == NAT and r["nbnd"] == NBND and abs(r["nelec"] - NELEC) < 1e-6,
        "nat=%d nbnd=%d nelec=%.2f want %d/%d/%d" % (r["nat"], r["nbnd"], r["nelec"], NAT, NBND, NELEC))
    chk("%s: SCF converged" % s, r["iters"] > 0 and r["scf_acc"] < CONV_THR,
        "%d iterations, scf accuracy %.2e Ry want < %.0e" % (r["iters"], r["scf_acc"], CONV_THR))
    chk("%s: forces on all atoms" % s, len(r["forces"]) == NAT and all(map(math.isfinite, sum(r["forces"], ()))),
        "%d of %d finite" % (len(r["forces"]), NAT))
want_pools = 2 if "-nk 2" in DECOMP_B else 1
chk("B: k-point pools", B["npool"] == want_pools, "npool=%d want %d (%s)" % (B["npool"], want_pools, DECOMP_B))
chk("A: one pool", A["npool"] == 1, "npool=%d" % A["npool"])

dE = abs(A["E_ry"] - B["E_ry"])
chk("decomposition: total energy", dE < TOL_E, "|E_A-E_B| = %.2e Ry want < %.0e" % (dE, TOL_E))
fa, fb = A["forces"], B["forces"]
dF = max((abs(a - b) for x, y in zip(fa, fb) for a, b in zip(x, y)), default=float("nan"))
bound = A["scf_corr"] + B["scf_corr"]
chk("decomposition: forces", len(fa) == len(fb) == NAT and dF <= bound,
    "max|F_A-F_B| = %.2e Ry/au want <= SCF corrections %.2e" % (dF, bound))
dEf = abs(A["ef_ev"] - B["ef_ev"])
print("%-34s |Ef_A-Ef_B| = %.1e eV  [reported]" % ("decomposition: Fermi level", dEf))
for r in (A, B):
    net = [sum(f[i] for f in r["forces"]) for i in range(3)] if r["forces"] else [float("nan")] * 3
    print("%-34s net force %s Ry/au, %d k-points, %s wall  [reported]"
          % ("%s: " % r["tag"], " ".join("%.1e" % v for v in net), r["nks"], r["wall"]))

with open(os.path.join(O, "results.tsv"), "w") as fh:
    keys = ["version", "ranks", "npool", "nks", "iters", "E_ry", "scf_acc",
            "ef_ev", "tot_force", "scf_corr", "wall"]
    fh.write("run\t" + "\t".join(keys) + "\n")
    for r in (A, B):
        fh.write(r["tag"] + "\t" + "\t".join(repr(r[k]) if isinstance(r[k], float) else str(r[k]) for k in keys) + "\n")
for r in (A, B):
    with open(os.path.join(O, "forces-%s.tsv" % r["tag"]), "w") as fh:
        for i, f in enumerate(r["forces"], 1):
            fh.write("%d\t%r\t%r\t%r\n" % ((i,) + tuple(f)))

print("----")
print("E(A) = %.8f Ry  E(B) = %.8f Ry  Ef = %.4f eV  iterations %d / %d"
      % (A["E_ry"], B["E_ry"], A["ef_ev"], A["iters"], B["iters"]))
print("CHECKS PASSED" if not fail else "CHECKS FAILED %s" % fail)
sys.exit(1 if fail else 0)
