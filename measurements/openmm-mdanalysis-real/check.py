"""Assertions on real.py's outputs; prints check.txt and exits 1 on any failure.
   python3 -u check.py <out dir>
"""
import json
import math
import os
import re
import sys

import numpy as np

OUT = sys.argv[1]
r = json.load(open(os.path.join(OUT, "results.json")))
fail = []
KT = 0.0019872041 * 300.0          # kcal/mol at 300 K
ATOMS = {"JAC": 23558, "STMV": 1067095}   # the suite's own READMEs
# CPU platform vs Reference: the CPU platform evaluates nonbonded interactions in single precision
# (epsilon 6e-8), so 1e-6 relative on a sum of ~1e8 pair terms.
TOL_REF = 1e-6
# SHAKE/SETTLE at the requested relative tolerance 1e-6; 1e-5 allows for single-precision positions.
TOL_SHAKE = 1e-5
# DCD stores float32 Angstrom: at |x| ~ 128 A one ulp is 1.5e-5 A, so ~3e-5 of a 1 A bond is format noise.
TOL_DECODE = 1e-4


def check(label, ok, detail):
    ok = bool(ok)                  # a numpy bool must fail too; NaN comparisons are already False
    print("%-34s %s  %s" % (label, "ok  " if ok else "FAIL", detail), flush=True)
    if not ok:
        fail.append(label)


def obs(label, detail):
    print("%-34s obs   %s" % (label, detail), flush=True)


def lj_types(S):
    """Per-type-pair counts n_i*n_j and the prmtop's own A_ij, B_ij (kcal/mol A^12, A^6)."""
    from openmm import app
    raw = app.AmberPrmtopFile("PME/Topologies/%s.prmtop" % S)._prmtop._raw_data
    nt = int(raw["POINTERS"][1])
    n = np.bincount([int(x) - 1 for x in raw["ATOM_TYPE_INDEX"]], minlength=nt).astype(np.float64)
    idx = np.array([int(x) for x in raw["NONBONDED_PARM_INDEX"]]).reshape(nt, nt)
    a = np.array([float(x) for x in raw["LENNARD_JONES_ACOEF"]]); b = np.array([float(x) for x in raw["LENNARD_JONES_BCOEF"]])
    return np.outer(n, n), a[idx - 1], b[idx - 1]


def sander_terms(path):
    """The last energy block of sander's mdout (the FINAL RESULTS of a 1-cycle minimisation)."""
    t = open(path).read()
    blk = t[t.rindex("BOND    ="):]
    v = {k.strip(): float(x) for k, x in re.findall(r"([A-Z0-9\- ]+?)\s*=\s*(-?\d+\.\d+)", blk[:400])}
    alpha = float(re.search(r"Ewald Coefficient\s*=\s*([\d.]+)", t).group(1))
    order = int(re.search(r"Interpolation order\s*=\s*(\d+)", t).group(1))
    return v, alpha, order


obs("openmm_version", r["openmm_version"])
obs("platforms_loaded", ",".join(r["platforms_loaded"]))
for S, sysr in r["systems"].items():
    print("== %s ==" % S)
    check(S + " platform_cpu", sysr["platform"] == "CPU", "%s  want CPU (loaded: %s)" % (sysr["platform"], ",".join(r["platforms_loaded"])))
    check(S + " cpu_threads", sysr["cpu_threads_property"] == str(r["threads"]),
          "%s  want %d (the CPU platform's own Threads property)" % (sysr["cpu_threads_property"], r["threads"]))
    check(S + " atoms", sysr["n_atoms"] == ATOMS[S], "%d  want %d (suite README)" % (sysr["n_atoms"], ATOMS[S]))
    obs(S + " constraints", "%d; H mass %.3f-%.3f Da" % (sysr["n_constraints"], sysr["h_mass_min"], sysr["h_mass_max"]))

    # --- cross-code: OpenMM vs sander on the same bytes, same PME method --------------------
    sv, s_alpha, s_order = sander_terms("sander/sander-%s.mdout" % S)
    s0, _, _ = sander_terms("sander/sander-%s-novdwcorr.mdout" % S)
    check(S + " pme_matches_sander", round(sysr["pme_alpha_per_A"], 5) == s_alpha and s_order == 5,
          "alpha %.8f/A (sander prints %.5f), order %d, grid %s" % (sysr["pme_alpha_per_A"], s_alpha, s_order, sysr["pme_grid"]))
    e = sysr["e0_cpu"]; ref = sysr["e0_ref"]; nb = sysr["e0_nb_split"]
    # Bonded terms: the same functional forms on the same parameters. sander prints 4 decimals and
    # stores torsion phases to 8 digits, so 1e-6 relative.
    for name, o, s in (("bond", ref["HarmonicBondForce"], s0["BOND"]), ("angle", ref["HarmonicAngleForce"], s0["ANGLE"]),
                       ("dihedral", ref["PeriodicTorsionForce"], s0["DIHED"])):
        d = abs(o - s)
        check("%s sander_%s" % (S, name), d <= 1e-6 * abs(s) + 1e-4,
              "OpenMM %.4f  sander %.4f  |d| %.4f kcal/mol (%.1e rel)  want <= 1e-6 rel" % (o, s, d, d / abs(s)))
    # Lennard-Jones, correction off on both sides (vdwmeth=0 / setUseDispersionCorrection(False)):
    # the same finite pair sum over the same cutoff, both in double (OpenMM's Reference platform).
    # What separates them is sander's 4 printed decimals and OpenMM re-deriving the pair coefficients
    # from the prmtop's 9-digit A/B by the combining rule (no NBFIX here), ~1e-9; so 1e-7.
    o, s = nb["lj_nodisp"], s0["VDWAALS"] + s0["1-4 VDW"]
    check(S + " sander_lj", abs(o - s) <= 1e-7 * abs(s) + 2e-4,
          "OpenMM %.4f  sander %.4f (VDWAALS + 1-4 VDW, vdwmeth=0)  %.1e rel  want <= 1e-7" % (o, s, abs(o - s) / abs(s)))
    # Electrostatics: sander's charges carry Amber's 18.2223^2 = 332.0522173 kcal A/mol e^2, OpenMM
    # its own 1/(4 pi eps0) = 138.935456 kJ nm/mol e^2 = 332.0637 -- a units constant, not physics,
    # that alone makes OpenMM's electrostatics 3.46e-5 larger. Compared after that ratio, the residual
    # is PME's; same alpha, grid and order, so it is bounded by sander's direct-sum tolerance, 1e-5.
    K = (138.935456 / 4.184 * 10.0) / 18.2223 ** 2
    o, s = nb["elec"], s0["EEL"] + s0["1-4 EEL"]
    resid = o / (K * s) - 1.0
    check(S + " sander_elec", abs(resid) <= 1e-5,
          "OpenMM %.4f  sander %.4f (EEL + 1-4 EEL)  raw ratio-1 %.3e, Coulomb-constant ratio-1 %.3e, residual %.1e  want <= 1e-5" % (
              o, s, o / s - 1.0, K - 1.0, resid))
    # The long-range dispersion correction, against its closed form from the prmtop's own A/B
    # coefficients and per-type counts n_i, with X_ij = A_ij/(9 rc^9) - B_ij/(3 rc^3):
    #   E = 2 pi/V sum_ij n_i n_j X_ij                                   (the textbook N^2 form)
    #   E = 2 pi/V N/(N+1) [sum_ij n_i n_j X_ij + sum_i n_i X_ii]        (OpenMM's: it averages
    #       over N(N+1)/2 pairs including self-pairs, NonbondedForceImpl::calcDispersionCorrection)
    # The two differ by O(1/N); OpenMM is asserted against its own documented form, double vs double.
    nn, A, B = lj_types(S)
    V = float(np.prod([10.0 * x for x in sysr["box_nm"]])); rc = 9.0
    X = A / (9 * rc ** 9) - B / (3 * rc ** 3)
    n = np.sqrt(np.diag(nn)); N = float(n.sum())
    exact_att = -2 * math.pi / (3 * V * rc ** 3) * float(np.sum(nn * B))
    textbook = 2 * math.pi / V * float(np.sum(nn * X))
    omm_form = 2 * math.pi / V * N / (N + 1) * (float(np.sum(nn * X)) + float(np.sum(n * np.diag(X))))
    o_corr = nb["lj"] - nb["lj_nodisp"]
    check(S + " openmm_dispersion_correction", abs(o_corr - omm_form) <= 1e-7 * abs(omm_form),
          "%.4f kcal/mol  closed form (OpenMM's) %.4f  (%.1e rel)  want <= 1e-7; textbook N^2 form %.4f (%.1e)" % (
              o_corr, omm_form, abs(o_corr - omm_form) / abs(omm_form), textbook, o_corr / textbook - 1))
    s_corr = sv["VDWAALS"] - s0["VDWAALS"]
    wrapped = (np.round(nn).astype(np.int64) + 2 ** 31) % 2 ** 32 - 2 ** 31
    obs(S + " sander_dispersion_correction",
        "%.4f kcal/mol (vdwmeth=1 - vdwmeth=0); closed form (attractive, sander's form) %.4f; with n_i*n_j wrapped "
        "to int32 %.4f; largest type count %d" % (s_corr, exact_att,
            -2 * math.pi / (3 * V * rc ** 3) * float(np.sum(wrapped * B)), int(round(math.sqrt(nn.max())))))

    # --- cross-implementation: CPU platform (mixed precision) vs Reference (double) ----------
    for k in ("HarmonicBondForce", "HarmonicAngleForce", "PeriodicTorsionForce", "NonbondedForce", "potential"):
        d = abs(e[k] - ref[k])
        check("%s cpu_eq_reference %s" % (S, k[:9]), d <= TOL_REF * abs(ref[k]),
              "CPU %.6f  Reference %.6f  (%.1e rel)  want <= %.0e" % (e[k], ref[k], d / abs(ref[k]), TOL_REF))
    dp = sysr["e0_cpu_defaultpme"]["NonbondedForce"] - e["NonbondedForce"]
    obs(S + " openmm_default_pme", "alpha %.5f/A grid %s: nonbonded %+.4f kcal/mol vs sander's PME parameters" % (
        sysr["default_pme"]["alpha_per_A"], sysr["default_pme"]["grid_formula_min"], dp))

    # --- NVE production: conservation on a thermal scale, constraints, temperature ----------
    path = os.path.join(OUT, "energies-%s.tsv" % S)
    if not os.path.exists(path):
        continue
    a = np.loadtxt(path, skiprows=1)
    ps, ke, tot = a[:, 1], a[:, 2], a[:, 4]
    check(S + " energies_finite", np.isfinite(a).all() and len(a) >= 2, "%d samples, all finite" % len(a))
    ndof = 3 * sysr["n_atoms"] - sysr["n_constraints"] - 3
    ns = (ps[-1] - ps[0]) / 1000.0
    slope = float(np.polyfit(ps / 1000.0, tot, 1)[0])            # kcal/mol per ns, least squares
    leak = abs(slope) / ndof
    temp = 2.0 * ke / (ndof * 0.0019872041)
    obs(S + " nve_length", "%d steps x %.0f fs = %.3f ns; %.2f ns/day on %d threads" % (
        sysr["md_steps"], r["dt_fs"], ns, sysr["ns_per_day"], r["threads"]))
    obs(S + " nve_etot", "%.4f -> %.4f kcal/mol, slope %+.4f kcal/mol/ns; std(Etot)/std(KE) %.4f" % (
        tot[0], tot[-1], slope, float(np.std(tot) / np.std(ke)) if np.std(ke) > 0 else float("nan")))
    obs(S + " constraint_projection", "step 0 before/after applyConstraints: Etot %+.4f kcal/mol, KE %+.4f" % (
        sysr["projection"]["d_etot"], sysr["projection"]["d_ke"]))
    msg_c = "%.2e kcal/mol per DoF per ns = %.3f%% of kT(300K)  want < 5%% (ndof %d)" % (leak, 100 * leak / KT, ndof)
    msg_t = "mean %.2f K (%.2f-%.2f)  want 290-310 (restart equilibrated at 300 K; NVE)" % (temp.mean(), temp.min(), temp.max())
    if ns >= 0.5:
        check(S + " nve_conserved", leak < 0.05 * KT, msg_c)
        check(S + " temperature", 290.0 < float(temp.mean()) < 310.0, msg_t)
    else:
        # 4 ps of a million atoms: an energy slope over a window this short is fluctuation, not drift,
        # so it is reported. The conservation claim is JAC's, over 1 ns.
        obs(S + " nve_slope (4 ps)", msg_c.split("  want")[0])
        obs(S + " temperature (4 ps)", msg_t.split("  want")[0])
    check(S + " shake_end", sysr["constraint_max_rel_dev_end"] < TOL_SHAKE,
          "max |d-d0|/d0 = %.2e at the last step  want < %.0e (tolerance 1e-6)" % (sysr["constraint_max_rel_dev_end"], TOL_SHAKE))

    if S != "JAC":
        continue
    # --- MDAnalysis reads OpenMM's trajectory back against the Amber topology ---------------
    import MDAnalysis as mda
    u = mda.Universe("PME/Topologies/JAC.prmtop", os.path.join(OUT, "traj-JAC.dcd"), format="DCD")
    obs("mdanalysis_version", str(getattr(mda, "__version__", "unknown")))
    nfr = sysr["md_steps"] // r["stride"]
    check("JAC mda_atoms", len(u.atoms) == sysr["n_atoms"], "%d  == OpenMM %d" % (len(u.atoms), sysr["n_atoms"]))
    check("JAC mda_frames", len(u.trajectory) == nfr, "%d  want %d (every %d steps)" % (len(u.trajectory), nfr, r["stride"]))
    box = [10.0 * x for x in sysr["box_nm"]]
    dbox = max(abs(float(u.dimensions[i]) - box[i]) for i in range(3))
    check("JAC mda_box", dbox < 1e-3, "%.4f %.4f %.4f A  == OpenMM's box (|d| %.1e)" % (tuple(float(x) for x in u.dimensions[:3]) + (dbox,)))
    c = np.load(os.path.join(OUT, "constraints-JAC.npz"))
    worst, n = 0.0, 0
    for ts in u.trajectory:
        x = u.atoms.positions.astype(np.float64)
        dev = np.abs(np.linalg.norm(x[c["i"]] - x[c["j"]], axis=1) - 10.0 * c["d"]) / (10.0 * c["d"])
        worst = max(worst, float(dev.max())); n += dev.size
    check("JAC mda_constraints_decode", worst < TOL_DECODE,
          "max |d-d0|/d0 = %.2e over %s constrained distances (%d x %d frames), read by MDAnalysis  want < %.0e" % (
              worst, format(n, ","), len(c["d"]), len(u.trajectory), TOL_DECODE))

if fail:
    print("FAILED: " + ", ".join(fail))
    sys.exit(1)
print("ALL CHECKS PASSED")
