"""OpenMM on the Amber20 benchmark suite's JAC (DHFR, 23,558 atoms) and STMV (1,067,095 atoms).

    python3 -u real.py <out dir> <threads> <jac steps> <stmv steps> [probe thread list]

For each system: the step-0 energy per force term on the CPU platform, on the Reference platform,
and with OpenMM's own default PME parameters; then NVE dynamics by the suite's production protocol
(4 fs, SHAKE on bonds to H, 9 A cutoff, PME, velocities from the restart). Writes results.json,
energies-<S>.tsv and traj-JAC.dcd; check.py asserts on them. Nothing here asserts, so a check can
never discard a finished simulation.
"""
import json
import math
import os
import re
import resource
import sys
import time

import openmm as mm
from openmm import app, unit

OUT, THREADS, JAC_STEPS, STMV_STEPS = sys.argv[1], int(sys.argv[2]), int(sys.argv[3]), int(sys.argv[4])
PROBE = [int(x) for x in sys.argv[5].split(",")] if len(sys.argv) > 5 and sys.argv[5] else []
KCAL = 4.184
DT_FS = 4.0
STRIDE = 2500                      # the suite's ntpr = ntwx for the 1 ns production run (10 ps)
CUT_A = 9.0
GROUPS = {"HarmonicBondForce": 0, "HarmonicAngleForce": 1, "PeriodicTorsionForce": 2, "NonbondedForce": 3}
res = {"threads": THREADS, "dt_fs": DT_FS, "stride": STRIDE, "systems": {}}


def rec(*a):
    print(*[str(x) for x in a], flush=True)


def maxrss_gib():
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0 / 1024.0


def sander_alpha(cut, tol=1e-5):
    """sander's find_ewaldcof: the beta with erfc(beta*cut)/cut == dsum_tol (default 1e-5), by its
    own doubling-then-bisection. Same coefficient, so the real/reciprocal split is sander's."""
    x, i = 0.5, 0
    while True:
        x *= 2; i += 1
        if math.erfc(x * cut) / cut < tol:
            break
    lo, hi = 0.0, x
    for _ in range(i + 60):
        x = 0.5 * (lo + hi)
        if math.erfc(x * cut) / cut >= tol:
            lo = x
        else:
            hi = x
    return x


def sander_grid(mdout):
    t = open(mdout).read()
    m = re.search(r"NFFT1\s*=\s*(\d+)\s+NFFT2\s*=\s*(\d+)\s+NFFT3\s*=\s*(\d+)", t)
    return [int(v) for v in m.groups()]


def build(S, pme=None):
    prm = app.AmberPrmtopFile("PME/Topologies/%s.prmtop" % S)
    crd = app.AmberInpcrdFile("PME/Coordinates/%s.inpcrd" % S)
    system = prm.createSystem(nonbondedMethod=app.PME, nonbondedCutoff=CUT_A / 10 * unit.nanometer,
                              constraints=app.HBonds)
    system.setDefaultPeriodicBoxVectors(*crd.boxVectors)
    for f in system.getForces():
        f.setForceGroup(GROUPS.get(f.__class__.__name__, 5))
        if isinstance(f, mm.NonbondedForce) and pme is not None:
            f.setPMEParameters(pme[0], *pme[1])
    return prm, crd, system


def energies(ctx):
    out = {}
    for name, g in GROUPS.items():
        out[name] = ctx.getState(getEnergy=True, groups={g}).getPotentialEnergy().value_in_unit(unit.kilojoule_per_mole) / KCAL
    s = ctx.getState(getEnergy=True)
    out["potential"] = s.getPotentialEnergy().value_in_unit(unit.kilojoule_per_mole) / KCAL
    return out


def context(system, integ, plat, threads=None):
    p = mm.Platform.getPlatformByName(plat)
    props = {"Threads": str(threads)} if (plat == "CPU" and threads) else {}
    return mm.Context(system, integ, p, props)


rec("openmm", mm.version.version, "platforms",
    ",".join(mm.Platform.getPlatform(i).getName() for i in range(mm.Platform.getNumPlatforms())))
res["openmm_version"] = str(mm.version.version)
res["platforms_loaded"] = [mm.Platform.getPlatform(i).getName() for i in range(mm.Platform.getNumPlatforms())]
res["plugin_load_failures"] = [str(x)[:200] for x in mm.Platform.getPluginLoadFailures()]

for S, NSTEP in (("JAC", JAC_STEPS), ("STMV", STMV_STEPS)):
    r = res["systems"].setdefault(S, {})
    alpha_a = sander_alpha(CUT_A)
    grid = sander_grid("sander/sander-%s.mdout" % S)
    pme = (alpha_a * 10.0 / unit.nanometer, grid)           # A^-1 -> nm^-1
    r["pme_alpha_per_A"], r["pme_grid"] = alpha_a, grid
    t0 = time.perf_counter()
    prm, crd, system = build(S, pme)
    r["build_s"] = time.perf_counter() - t0
    r["n_atoms"] = system.getNumParticles()
    r["n_constraints"] = system.getNumConstraints()
    masses = [system.getParticleMass(i).value_in_unit(unit.dalton) for i in range(system.getNumParticles())]
    hm = [m for a, m in zip(prm.topology.atoms(), masses) if a.element is not None and a.element.symbol == "H"]
    r["h_mass_min"], r["h_mass_max"] = min(hm), max(hm)
    r["box_nm"] = [crd.boxVectors[i][i].value_in_unit(unit.nanometer) for i in range(3)]
    rec(S, "atoms", r["n_atoms"], "constraints", r["n_constraints"], "H mass", r["h_mass_min"], r["h_mass_max"],
        "PME alpha/A", alpha_a, "grid", grid)

    # Step 0, three ways: CPU platform (what runs the dynamics), Reference platform (OpenMM's
    # double-precision reference implementation), and CPU with OpenMM's own default PME choice.
    t0 = time.perf_counter()
    ctx = context(system, mm.VerletIntegrator(DT_FS * unit.femtosecond), "CPU", THREADS)
    ctx.setPositions(crd.positions)
    r["e0_cpu"] = energies(ctx)
    r["e0_cpu_s"] = time.perf_counter() - t0
    r["cpu_threads_property"] = ctx.getPlatform().getPropertyValue(ctx, "Threads")
    r["platform"] = ctx.getPlatform().getName()
    del ctx
    t0 = time.perf_counter()
    ctx = context(system, mm.VerletIntegrator(DT_FS * unit.femtosecond), "Reference")
    ctx.setPositions(crd.positions)
    r["e0_ref"] = energies(ctx)
    r["e0_ref_s"] = time.perf_counter() - t0
    del ctx
    # The nonbonded energy split the way sander prints it: LJ (charges off), electrostatics (LJ
    # off), LJ with and without the long-range dispersion correction. Single points on the
    # Reference platform (double), so the comparison with sander is double against double.
    r["e0_nb_split"] = {}
    for kind in ("lj", "lj_nodisp", "elec"):
        _, _, sv = build(S, pme)
        nb = [f for f in sv.getForces() if isinstance(f, mm.NonbondedForce)][0]
        if kind.endswith("nodisp"):
            nb.setUseDispersionCorrection(False)
        if kind.startswith("lj") or kind == "elec":
            for i in range(nb.getNumParticles()):
                q, s, e = nb.getParticleParameters(i)
                nb.setParticleParameters(i, 0.0 if kind != "elec" else q, s, 0.0 if kind == "elec" else e)
            for j in range(nb.getNumExceptions()):
                a, b, qq, s, e = nb.getExceptionParameters(j)
                nb.setExceptionParameters(j, a, b, 0.0 if kind != "elec" else qq, s, 0.0 if kind == "elec" else e)
        ctx = context(sv, mm.VerletIntegrator(DT_FS * unit.femtosecond), "Reference")
        ctx.setPositions(crd.positions)
        r["e0_nb_split"][kind] = ctx.getState(getEnergy=True, groups={GROUPS["NonbondedForce"]}).getPotentialEnergy().value_in_unit(
            unit.kilojoule_per_mole) / KCAL
        del ctx, sv, nb
    rec(S, "e0 nonbonded split", json.dumps(r["e0_nb_split"]))
    _, _, sysd = build(S, None)
    ctx = context(sysd, mm.VerletIntegrator(DT_FS * unit.femtosecond), "CPU", THREADS)
    ctx.setPositions(crd.positions)
    # OpenMM's own choice from ewaldErrorTolerance (5e-4), by its documented formulas.
    # (getPMEParametersInContext raised std::bad_cast here, so it is not called.)
    tol = 5e-4
    a = math.sqrt(-math.log(2 * tol)) / (CUT_A / 10)
    grid_d = [int(math.ceil(2 * a * b / (3 * tol ** 0.2))) for b in r["box_nm"]]
    r["default_pme"] = {"alpha_per_A": a / 10.0, "grid_formula_min": grid_d}
    r["e0_cpu_defaultpme"] = energies(ctx)
    del ctx, sysd
    rec(S, "e0 CPU", json.dumps(r["e0_cpu"]))
    rec(S, "e0 Ref", json.dumps(r["e0_ref"]), "(%.1f s)" % r["e0_ref_s"])
    rec(S, "e0 CPU default PME", json.dumps(r["e0_cpu_defaultpme"]), r["default_pme"])

    # Sizing probe: the same dynamics at several thread counts, short.
    for T in PROBE:
        integ = mm.VerletIntegrator(DT_FS * unit.femtosecond)
        integ.setConstraintTolerance(1e-6)
        ctx = context(system, integ, "CPU", T)
        ctx.setPositions(crd.positions); ctx.setVelocities(crd.velocities)
        n = 200 if S == "JAC" else 20
        integ.step(10); ctx.getState(getEnergy=True)
        t0 = time.perf_counter(); integ.step(n); ctx.getState(getEnergy=True); dt = time.perf_counter() - t0
        nsday = n * DT_FS * 1e-6 / dt * 86400
        r.setdefault("probe", {})[str(T)] = nsday
        rec(S, "probe threads", T, "ns/day %.2f" % nsday)
        del ctx, integ

    if NSTEP <= 0:
        continue
    # Production: the suite's NVE protocol. tol = 1e-6 (sander's is absolute in A, OpenMM's
    # relative; both are SHAKE at production tightness). No thermostat, no barostat.
    integ = mm.VerletIntegrator(DT_FS * unit.femtosecond)
    integ.setConstraintTolerance(1e-6)
    ctx = context(system, integ, "CPU", THREADS)
    ctx.setPositions(crd.positions); ctx.setVelocities(crd.velocities)
    # Project the restart onto OpenMM's constraints first, as the first SHAKE step would: the
    # restart's velocities carry components along constrained bonds, which count as kinetic energy
    # until removed. Conservation is measured from the projected state.
    s = ctx.getState(getEnergy=True)
    e_before = (s.getKineticEnergy() + s.getPotentialEnergy()).value_in_unit(unit.kilojoule_per_mole) / KCAL
    ke_before = s.getKineticEnergy().value_in_unit(unit.kilojoule_per_mole) / KCAL
    ctx.applyConstraints(1e-6); ctx.applyVelocityConstraints(1e-6)
    s = ctx.getState(getEnergy=True)
    r["projection"] = {"d_etot": (s.getKineticEnergy() + s.getPotentialEnergy()).value_in_unit(unit.kilojoule_per_mole) / KCAL - e_before,
                       "d_ke": s.getKineticEnergy().value_in_unit(unit.kilojoule_per_mole) / KCAL - ke_before}
    stride = STRIDE if NSTEP >= 10 * STRIDE else max(1, NSTEP // 10)
    dcd = None
    if S == "JAC":
        fh = open(os.path.join(OUT, "traj-JAC.dcd"), "wb")
        dcd = app.DCDFile(fh, prm.topology, DT_FS * unit.femtosecond, 0, stride)
    rows = []
    def sample(step):
        s = ctx.getState(getEnergy=True, getPositions=dcd is not None)
        ke = s.getKineticEnergy().value_in_unit(unit.kilojoule_per_mole) / KCAL
        pe = s.getPotentialEnergy().value_in_unit(unit.kilojoule_per_mole) / KCAL
        rows.append((step, step * DT_FS / 1000.0, ke, pe, ke + pe))
        if dcd is not None and step > 0:
            dcd.writeModel(s.getPositions(), periodicBoxVectors=s.getPeriodicBoxVectors())
    sample(0)
    t0 = time.perf_counter()
    done = 0
    while done < NSTEP:
        integ.step(stride); done += stride
        sample(done)
    r["md_s"] = time.perf_counter() - t0
    r["md_steps"] = done
    r["ns_per_day"] = done * DT_FS * 1e-6 / r["md_s"] * 86400
    if dcd is not None:
        fh.close()
    # Constraint satisfaction at the end, from OpenMM's own state, against each constraint's length.
    pos = ctx.getState(getPositions=True, enforcePeriodicBox=False).getPositions(asNumpy=True).value_in_unit(unit.nanometer)
    import numpy as np
    cs = [system.getConstraintParameters(k) for k in range(system.getNumConstraints())]
    ci = np.array([c[0] for c in cs]); cj = np.array([c[1] for c in cs])
    cd = np.array([c[2].value_in_unit(unit.nanometer) for c in cs])
    worst = float(np.max(np.abs(np.linalg.norm(pos[ci] - pos[cj], axis=1) - cd) / cd))
    r["constraint_max_rel_dev_end"] = worst
    if S == "JAC":
        np.savez(os.path.join(OUT, "constraints-JAC.npz"), i=ci, j=cj, d=cd)
    with open(os.path.join(OUT, "energies-%s.tsv" % S), "w") as fh:
        fh.write("step\tps\tke_kcal\tpe_kcal\ttot_kcal\n")
        for row in rows:
            fh.write("%d\t%.3f\t%.6f\t%.6f\t%.6f\n" % row)
    rec(S, "md %d steps in %.1f s, %.2f ns/day; constraint dev %.2e" % (done, r["md_s"], r["ns_per_day"], worst))
    del ctx, integ
    res["maxrss_gib_after_" + S] = maxrss_gib()

res["maxrss_gib"] = maxrss_gib()
with open(os.path.join(OUT, "results.json"), "w") as fh:
    json.dump(res, fh, indent=1)
rec("maxrss_gib %.2f" % res["maxrss_gib"])
