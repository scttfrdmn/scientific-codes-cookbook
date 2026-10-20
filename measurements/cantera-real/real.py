#!/usr/bin/env python3
"""Cantera on GRI-Mech 3.0's own validation set, plus the sweeps a mechanism study runs.

    python3 -u real.py <staged sources dir> <out dir> <worker processes> [quick]

Four workloads, each in its own job list, run N at a time with one thread per process:

 1. THE MECHANISM IS THE PUBLISHED ONE. The official GRI-Mech 3.0 files (grimech30.dat,
    thermo30.dat, transport.dat, pinned by sha256) are converted with ck2yaml on the box and
    compared with the gri30.yaml that ships inside cantera: every rate constant, standard-state
    property and transport property over a T x P grid.
 2. LAMINAR FLAMES (FreeFlame): GRI-Mech's 8 flame-speed targets on a grid ladder and in three
    transport models, and the flame-speed curves (phi 0.6-1.6) a mechanism study plots: CH4-air at
    six (P, T0) conditions up to 19.7 atm, C2H6-air at 1 atm.
 3. SHOCK-TUBE IGNITION (constant-volume IdealGasReactor): GRI-Mech's 16 ignition targets, the
    Seery-Bowman constant-density curve, and a CH4-air ignition map (phi x P x T), plus a
    constant-pressure copy of the phi = 1 map. Each reactor is integrated to t = 100 s.
 4. EQUILIBRIUM TABLES: 5 fuels x 11 phi x 11 T x 4 P with all three of cantera's equilibrium
    solvers (vcs, gibbs, element_potential).

Identities (what must be true whatever the chemistry does) are asserted in check(); the
comparison with GRI-Mech's published PREMIX/SENKIN numbers and with experiment is reported.
"""
import json
import math
import multiprocessing as mp
import os
import re
import subprocess
import sys
import time
import traceback

for v in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS"):
    os.environ[v] = "1"

import numpy as np
import cantera as ct

SRC, OUT, NP = sys.argv[1], sys.argv[2], int(sys.argv[3])
QUICK = len(sys.argv) > 4 and sys.argv[4] == "quick"
MECH = "gri30.yaml"
T_END = 1e5      # s: integrate every reactor far past ignition, to its equilibrium
OFFICIAL = os.path.join(OUT, "gri30-official.yaml")
AIR = "O2:1,N2:3.76"


def mix(gas, spec, phi):
    if "|" in spec:
        fuel, ox = spec.split("|")
        gas.set_equivalence_ratio(phi, fuel, ox)
    else:
        gas.X = spec


def phi_o2(gas):
    """Equivalence ratio as the target pages define it: O2 needed to burn every non-O2 species to
    CO2 + H2O over the O2 present. (Cantera's equivalence_ratio() counts the O in CO as oxidizer,
    which gives 1.377 for the SF7 CO/H2 mixture the page calls 1.69.)"""
    need = 0.0
    for k, x in zip(gas.species_names, gas.X):
        if k != "O2" and x > 0:
            need += x * (gas.n_atoms(k, "C") + gas.n_atoms(k, "H") / 4.0 - gas.n_atoms(k, "O") / 2.0)
    return need / gas["O2"].X[0]


def print_tol(spec):
    """How far a phi computed from printed percentages can sit from the true one: half a unit in the
    last printed digit of each fuel and O2 percentage, relative (0.8% C3H8 is 0.75-0.85, so 6%).
    PHI_TOL holds the same allowance for the page's printed phi."""
    if "|" in spec:
        return 0.0
    tol = 0.0
    for k, v in (x.split(":") for x in spec.split(",")):
        if k in ("AR", "N2"):
            continue
        d = len(v.split(".")[1]) if "." in v else 0
        tol += 0.5 * 10 ** -d / float(v)
    return tol


PHI_TOL = {}


def targets():
    rows = []
    with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "targets.tsv")) as fh:
        lines = [l.rstrip("\n") for l in fh if not l.startswith("#")]
    head = lines[0].split("\t")
    for l in lines[1:]:
        r = dict(zip(head, l.split("\t")))
        for k in ("phi", "P_atm", "T_K", "exp"):
            r[k] = float(r[k])
        r["gri30"] = float(r["gri30"]) if r.get("gri30") else None
        d = len(l.split("\t")[head.index("phi")].split(".")[-1]) if "." in l.split("\t")[head.index("phi")] else 0
        PHI_TOL[r["id"]] = 0.5 * 10 ** -d / r["phi"]
        rows.append(r)
    return rows


# --------------------------------------------------------------------- 1. mechanism identity
def job_mechanism(_):
    a = ct.Solution(MECH)
    b = ct.Solution(OFFICIAL)
    res = {"job": "mechanism", "species": [a.n_species, b.n_species],
           "reactions": [a.n_reactions, b.n_reactions],
           "same_species_order": a.species_names == b.species_names,
           "same_equations": sum(x.equation == y.equation for x, y in zip(a.reactions(), b.reactions())),
           "same_duplicate_flags": sum(x.duplicate == y.duplicate for x, y in zip(a.reactions(), b.reactions()))}
    props = {"kf": lambda g: g.forward_rate_constants, "kr": lambda g: g.reverse_rate_constants,
             "h_RT": lambda g: g.standard_enthalpies_RT, "s_R": lambda g: g.standard_entropies_R,
             "cp_R": lambda g: g.standard_cp_R, "viscosity": lambda g: np.array([g.viscosity]),
             "conductivity": lambda g: np.array([g.thermal_conductivity]),
             "mix_diff": lambda g: g.mix_diff_coeffs, "binary_diff": lambda g: g.binary_diff_coeffs.ravel(),
             "thermal_diff": lambda g: g.thermal_diff_coeffs}
    worst = {k: 0.0 for k in props}
    n = 0
    X = "CH4:1,O2:2,N2:7.52,H2O:0.5,CO2:0.3,AR:0.2,H:0.01,OH:0.01,CO:0.05,NO:0.001"
    for g in (a, b):
        g.transport_model = "multicomponent"
    for T in np.linspace(300.0, 3000.0, 28):
        for P in (1e3, 1e5, 1e7):
            for g in (a, b):
                g.TPX = T, P, X
            for k, f in props.items():
                x, y = f(a), f(b)
                d = np.abs(x - y) / np.maximum(np.abs(x), 1e-300)
                worst[k] = max(worst[k], float(d.max()))
            n += 1
    res["states"] = n
    res["max_rel_diff"] = worst
    return res


# --------------------------------------------------------------------------------- 2. flames
def job_flame(j):
    t0 = time.time()
    gas = ct.Solution(MECH)
    mix(gas, j["mixture"], j["phi"])
    gas.TP = j["T0"], j["P_atm"] * ct.one_atm
    phi_calc = phi_o2(gas)
    h_in = gas.enthalpy_mass
    els = list(gas.element_names)
    Z_in = np.array([gas.elemental_mass_fraction(e) for e in els])
    eq = ct.Solution(MECH)
    eq.TPX = gas.T, gas.P, gas.X
    eq.equilibrate("HP")
    f = ct.FreeFlame(gas, width=0.03)
    f.max_grid_points = 5000
    s = j["slope"]
    f.set_refine_criteria(ratio=2, slope=s, curve=2 * s, prune=0)
    f.transport_model = j["transport"]
    if j["transport"] == "multicomponent":
        f.soret_enabled = True
    f.solve(loglevel=0, auto=True)
    NSKIP = f.flame.component_names.index(gas.species_names[0])   # species follow u, V, T, Lambda, E, Uo
    n = len(f.grid)
    Z = np.empty((n, len(els)))
    h = np.empty(n)
    for i in range(n):
        f.set_gas_state(i)
        Z[i] = [gas.elemental_mass_fraction(e) for e in els]
        h[i] = gas.enthalpy_mass
    cp_out = gas.cp_mass
    m = f.density * f.velocity
    r = dict(j)
    r.update({"Su_cm_s": float(f.velocity[0] * 100.0), "points": n, "domain_m": float(f.grid[-1]),
              "phi_calc": float(phi_calc), "T_out": float(f.T[-1]), "T_ad": float(eq.T),
              "T_max": float(f.T.max()),
              "mass_flux_spread": float(np.ptp(m) / np.mean(m)),
              "Z_drift_outlet": float(np.abs(Z[-1] - Z_in).max()),
              "Z_drift_pointwise": float(np.abs(Z - Z_in).max()),
              "h_drift_outlet_K": float((h[-1] - h_in) / cp_out),
              "h_drift_pointwise_K": float(np.abs(h - h_in).max() / cp_out),
              "Y_sum_err": float(np.abs(f.Y.sum(axis=0) - 1.0).max()),
              "Y_sum_bound": float(f.flame.steady_reltol()[NSKIP:].max() + f.flame.steady_abstol()[NSKIP:].sum()),
              "Y_min": float(f.Y.min()), "wall_s": time.time() - t0})
    return r


# ------------------------------------------------------------------------------- 3. ignition
def job_ignition(j):
    t0 = time.time()
    gas = ct.Solution(MECH)
    mix(gas, j["mixture"], j["phi"])
    if "density_mol_m3" in j:
        gas.TD = j["T0"], j["density_mol_m3"] * gas.mean_molecular_weight / 1000.0
    else:
        gas.TP = j["T0"], j["P_atm"] * ct.one_atm
    phi_calc = phi_o2(gas)
    P0, rho0, u0, h0 = gas.P, gas.density, gas.int_energy_mass, gas.enthalpy_mass
    els = list(gas.element_names)
    Z0 = np.array([gas.elemental_mass_fraction(e) for e in els])
    conv = j["reactor"]
    eq = ct.Solution(MECH)
    eq.TPX = gas.T, gas.P, gas.X
    eq.equilibrate("UV" if conv == "UV" else "HP")
    r = ct.IdealGasReactor(gas) if conv == "UV" else ct.IdealGasConstPressureReactor(gas)
    net = ct.ReactorNet([r])
    net.rtol, net.atol = 1e-10, 1e-20
    iOH = gas.species_index("OH")
    rise = eq.T - j["T0"]          # the temperature rise the mixture has to give
    ts, dts, ohs = [], [], []
    steps = 0
    while net.time < 1.0:
        net.step()
        steps += 1
        th = r.thermo
        wdot = th.net_production_rates
        if conv == "UV":
            dTdt = -float(np.dot(th.partial_molar_int_energies, wdot)) / (th.density * th.cv_mass)
        else:
            dTdt = -float(np.dot(th.partial_molar_enthalpies, wdot)) / (th.density * th.cp_mass)
        ts.append(net.time)
        dts.append(dTdt)
        ohs.append(th.X[iOH])
        if len(ts) > 50 and th.T > j["T0"] + 0.5 * rise and dTdt < 1e-4 * max(dts):
            break
    ts, dts, ohs = np.array(ts), np.array(dts), np.array(ohs)
    k = int(np.argmax(dts))
    tig = float(ts[k])
    if 0 < k < len(ts) - 1:      # vertex of the parabola through the three steps around the peak
        x, y = ts[k - 1:k + 2], dts[k - 1:k + 2]
        c = np.polyfit(x - x[1], y, 2)
        if c[0] < 0:
            v = -c[1] / (2 * c[0])
            if x[0] - x[1] <= v <= x[2] - x[1]:
                tig = float(x[1] + v)
    t_oh = float(ts[int(np.argmax(ohs))])
    T_ign_reached = bool(r.thermo.T > j["T0"] + 0.5 * rise)
    net.advance(T_END)
    try:
        total_steps = int(net.solver_stats["steps"])    # CVODES' own count, the advance included
    except Exception:
        total_steps = None
    th = r.thermo
    Z1 = np.array([th.elemental_mass_fraction(e) for e in els])
    res = dict(j)
    res.update({"phi_calc": float(phi_calc), "P0_atm": P0 / ct.one_atm, "tau_dTdt_us": tig * 1e6,
                "tau_OHpeak_us": t_oh * 1e6, "ignited": T_ign_reached, "steps": steps,
                "total_steps": total_steps, "rtol": net.rtol,
                "T_end": float(th.T), "T_eq": float(eq.T), "dT_eq": float(th.T - eq.T),
                "dX_eq": float(np.abs(th.X - eq.X).max()), "dP_eq_rel": float(abs(th.P - eq.P) / eq.P),
                "Z_drift": float(np.abs(Z1 - Z0).max()),
                "u_drift_rel": float(abs(th.int_energy_mass - u0) / abs(u0)) if conv == "UV" else None,
                "h_drift_rel": float(abs(th.enthalpy_mass - h0) / abs(h0)) if conv == "HP" else None,
                "rho_drift_rel": float(abs(th.density - rho0) / rho0) if conv == "UV" else None,
                "P_drift_rel": float(abs(th.P - P0) / P0) if conv == "HP" else None,
                "wall_s": time.time() - t0})
    return res


# ---------------------------------------------------------------------------- 4. equilibrium
FUELS = {"CH4": "CH4", "C2H6": "C2H6", "C3H8": "C3H8", "H2": "H2", "syngas": "CO:1,H2:1"}
PHIS = [0.25, 0.5, 0.75, 0.9, 1.0, 1.1, 1.25, 1.5, 2.0, 3.0, 4.0]
TS = [1000.0 + 200.0 * i for i in range(11)]   # 1000-3000 K: GRI-Mech 3.0's thermo is fitted on 300-3000 K
SOLVERS = ["vcs", "gibbs", "element_potential"]


def kkt(g, A):
    """The Gibbs-minimum certificate: at equilibrium every species' chemical potential is a sum
    of element potentials, mu_k / RT = sum_j a_kj lambda_j. Fit lambda on the species above
    1e-12 and return the largest residual (in units of RT, i.e. ~ the relative error in X_k)."""
    X = g.X
    mu = g.chemical_potentials / (ct.gas_constant * g.T)
    on = X > 1e-12
    lam, *_ = np.linalg.lstsq(A[on], mu[on], rcond=None)
    return float(np.abs(A[on] @ lam - mu[on]).max())


def job_equilibrium(j):
    t0 = time.time()
    g = ct.Solution(MECH)
    A = np.array([[g.n_atoms(k, e) for e in g.element_names] for k in g.species_names], dtype=float)
    els = list(g.element_names)
    rows = []
    for phi in PHIS:
        for T in TS:
            g.set_equivalence_ratio(phi, FUELS[j["fuel"]], AIR)
            g.TP = T, j["P_atm"] * ct.one_atm
            X0 = g.X.copy()
            Z0 = np.array([g.elemental_mass_fraction(e) for e in els])
            out = {"fuel": j["fuel"], "P_atm": j["P_atm"], "phi": phi, "T": T}
            sol = {}
            for s in SOLVERS:
                g.TPX = T, j["P_atm"] * ct.one_atm, X0
                try:
                    g.equilibrate("TP", solver=s)
                except ct.CanteraError as e:
                    msg = [l.strip() for l in str(e).splitlines() if re.search("[a-z]", l)]
                    out[s] = {"error": " / ".join(msg)[-240:]}
                    continue
                Z1 = np.array([g.elemental_mass_fraction(e) for e in els])
                sol[s] = (g.X.copy(), g.gibbs_mass, ct.gas_constant * g.T / g.mean_molecular_weight)
                out[s] = {"kkt": kkt(g, A), "Z_drift": float(np.abs(Z1 - Z0).max()),
                          "g_RT": g.gibbs_mass / sol[s][2]}
            if "vcs" in sol:
                for s in ("gibbs", "element_potential"):
                    if s in sol:
                        out[s]["dX_vs_vcs"] = float(np.abs(sol[s][0] - sol["vcs"][0]).max())
                        out[s]["dg_vs_vcs_RT"] = float(abs(sol[s][1] - sol["vcs"][1]) / sol["vcs"][2])
            rows.append(out)
    return {"job": "equilibrium", "fuel": j["fuel"], "P_atm": j["P_atm"], "rows": rows,
            "wall_s": time.time() - t0}


def job_determinism(_):
    a = job_flame(dict(FLAME_BASE, mixture="CH4|" + AIR, phi=0.98, P_atm=1.0, T0=300.0,
                       transport="mixture-averaged", slope=0.05, set="determinism"))
    b = job_flame(dict(FLAME_BASE, mixture="CH4|" + AIR, phi=0.98, P_atm=1.0, T0=300.0,
                       transport="mixture-averaged", slope=0.05, set="determinism"))
    ig = dict(IGN_BASE, mixture="CH4:9.1,O2:18.2,AR:72.7", phi=1.0, P_atm=1.8, T0=1500.0, reactor="UV",
              set="determinism")
    c, d = job_ignition(ig), job_ignition(ig)
    return {"job": "determinism", "flame_Su": [a["Su_cm_s"], b["Su_cm_s"]],
            "flame_points": [a["points"], b["points"]],
            "ignition_tau": [c["tau_dTdt_us"], d["tau_dTdt_us"]], "ignition_T_end": [c["T_end"], d["T_end"]]}


FLAME_BASE = {"job": "flame"}
IGN_BASE = {"job": "ignition"}


def jobs():
    J = [("mechanism", {}), ("determinism", {})]
    T = targets()
    for t in T:
        if t["kind"] == "flame":
            base = dict(FLAME_BASE, id=t["id"], mixture=t["mixture"], phi=t["phi"], P_atm=t["P_atm"],
                        T0=t["T_K"], set="target")
            ladder = [("mixture-averaged", s) for s in (0.1, 0.05, 0.025, 0.0125)] + \
                     [("multicomponent", s) for s in (0.05, 0.025)] + [("unity-Lewis-number", 0.05)]
            if QUICK:
                ladder = [("mixture-averaged", 0.1)]
            for tr, s in ladder:
                J.append(("flame", dict(base, transport=tr, slope=s)))
        else:
            J.append(("ignition", dict(IGN_BASE, id=t["id"], mixture=t["mixture"], phi=t["phi"],
                                       P_atm=t["P_atm"], T0=t["T_K"], reactor="UV", set="target")))
    phis = [round(0.6 + 0.05 * i, 2) for i in range(21)]
    conds = [(1.0, 300.0), (3.0, 300.0), (1.0, 400.0), (4.9, 400.0), (10.0, 400.0), (19.7, 400.0)]
    if QUICK:
        phis, conds = [0.7, 1.0, 1.4], [(1.0, 300.0)]
    for P, T0 in conds:
        for phi in phis:
            J.append(("flame", dict(FLAME_BASE, mixture="CH4|" + AIR, phi=phi, P_atm=P, T0=T0,
                                    transport="mixture-averaged", slope=0.05, set="curve_CH4")))
    for phi in phis:
        J.append(("flame", dict(FLAME_BASE, mixture="C2H6|" + AIR, phi=phi, P_atm=1.0, T0=300.0,
                                transport="mixture-averaged", slope=0.05, set="curve_C2H6")))
        J.append(("flame", dict(FLAME_BASE, mixture="CH4|" + AIR, phi=phi, P_atm=1.0, T0=300.0,
                                transport="unity-Lewis-number", slope=0.05, set="curve_CH4_unityLe")))
    # Seery & Bowman's constant-density curve (14.6 mol/m3), as plotted on the IG.1a page
    for x in ([5.6 + 0.2 * i for i in range(8)] if not QUICK else [6.0]):
        J.append(("ignition", dict(IGN_BASE, mixture="CH4:9.1,O2:18.2,AR:72.7", phi=1.0, density_mol_m3=14.6,
                                   T0=round(1e4 / x, 3), reactor="UV", set="seery_bowman")))
    Ts = [1100.0 + 50.0 * i for i in range(19)] if not QUICK else [1300.0, 1800.0]
    for phi in (0.5, 1.0, 2.0):
        for P in (1.0, 10.0, 40.0):
            for T0 in Ts:
                J.append(("ignition", dict(IGN_BASE, mixture="CH4|" + AIR, phi=phi, P_atm=P, T0=T0,
                                           reactor="UV", set="map_UV")))
                if phi == 1.0:
                    J.append(("ignition", dict(IGN_BASE, mixture="CH4|" + AIR, phi=phi, P_atm=P, T0=T0,
                                               reactor="HP", set="map_HP")))
    for fuel in FUELS:
        for P in ((0.1, 1.0, 10.0, 100.0) if not QUICK else (1.0,)):
            J.append(("equilibrium", {"fuel": fuel, "P_atm": P}))
    return J


FN = {"mechanism": job_mechanism, "flame": job_flame, "ignition": job_ignition,
      "equilibrium": job_equilibrium, "determinism": job_determinism}
COST = {"mechanism": 5, "determinism": 40, "equilibrium": 30, "ignition": 2}


def cost(job):
    kind, j = job
    if kind == "flame":
        c = 15 * (0.05 / j["slope"]) ** 0.7
        return c * (3 if j["transport"] == "multicomponent" else 1) * (1 + j["P_atm"] / 10)
    return COST[kind]


def run(job):
    kind, j = job
    t0 = time.time()
    try:
        r = FN[kind](j)
        r.setdefault("job", kind)
        r["ok"] = True
    except Exception as e:
        r = dict(j, job=kind, ok=False, error="%s: %s" % (type(e).__name__, str(e).strip()[-400:]),
                 trace=traceback.format_exc()[-1500:])
    r["wall_s"] = time.time() - t0
    return r


# ------------------------------------------------------------------- the checks, on all chips
FAIL = []


def say(fh, s):
    print(s, flush=True)
    fh.write(s + "\n")


def need(fh, ok, what):
    ok = bool(ok)
    say(fh, "%s  %s" % ("PASS" if ok else "FAIL", what))
    if not ok:
        FAIL.append(what)


def page_text(path):
    t = open(path, encoding="latin-1").read()
    t = re.sub(r"<!--.*?-->", "", t, flags=re.S)
    t = re.sub(r"<[^>]*>", " ", t).replace("&nbsp;", " ")
    return re.sub(r"\s+", " ", t)


def page_numbers(path):
    t = page_text(path)
    a = t.find("TARGET VALUES")
    b = t.find("SENSITIVITY DIAGRAMS")
    seg = t[t.find("GRI-Mech 3.0", a) + len("GRI-Mech 3.0"):b]
    unit = "ms" if "millisec" in t[a:b] else ""
    return re.findall(r"N/A|\d+(?:\.\d+)?", seg), unit


def check(R, fh):
    ok = [r for r in R if r.get("ok")]
    bad = [r for r in R if not r.get("ok")]
    say(fh, "jobs %d, ok %d, failed %d" % (len(R), len(ok), len(bad)))
    for r in bad:
        say(fh, "  job failed: %s %s %s" % (r["job"], {k: r.get(k) for k in ("id", "set", "mixture", "phi",
                                                                            "P_atm", "T0", "transport",
                                                                            "slope", "fuel")}, r["error"]))
    need(fh, not bad, "every job ran to completion (%d)" % len(R))

    # transcription: every number in targets.tsv is the one on its pinned page
    T = targets()
    mism = []
    for t in T:
        nums, unit = page_numbers(os.path.join(SRC, "targets30", t["page"] + ".html"))
        sc = 1000.0 if unit == "ms" else 1.0
        vals = [float(x) * sc if x != "N/A" else None for x in nums]
        if not vals or vals[0] != t["exp"]:
            mism.append("%s exp %s vs page %s" % (t["id"], t["exp"], nums))
        if t["gri30"] is not None and (len(vals) != 4 or vals[3] != t["gri30"]):
            mism.append("%s gri30 %s vs page %s" % (t["id"], t["gri30"], nums))
        if t["gri30"] is None and len(vals) == 4:
            mism.append("%s has 4 values on its page but none transcribed" % t["id"])
    for t in T:
        tx = page_text(os.path.join(SRC, "targets30", t["page"] + ".html"))
        mt = re.search(r"Mixture:\s*([A-Za-z0-9\-]+)\s*(\(([\d.%\-\s]+)\))?\s*,\s*phi\s*=\s*([\d.]+)\s*,\s*P\s*=\s*([\d.]+)\s*atm\s*,\s*T(?:\(0\))?\s*=\s*([\d.]+)\s*K", tx)
        if not mt:
            mism.append("%s: no Mixture line" % t["id"])
            continue
        names, pct = mt.group(1).upper().split("-"), mt.group(3)
        if [float(mt.group(i)) for i in (4, 5, 6)] != [t["phi"], t["P_atm"], t["T_K"]]:
            mism.append("%s phi/P/T %s vs page %s" % (t["id"], (t["phi"], t["P_atm"], t["T_K"]), mt.group(4, 5, 6)))
        if "|" in t["mixture"]:
            if names != [t["mixture"].split("|")[0], "AIR"] or pct:
                mism.append("%s mixture %s vs page %s" % (t["id"], t["mixture"], mt.group(0)))
        else:
            sp = [(k, float(v)) for k, v in (x.split(":") for x in t["mixture"].split(","))]
            vals = [float(x) for x in re.findall(r"[\d.]+", pct or "")]
            if names[-1] == "AIR":    # air as O2 + N2 at 21/79
                o2, n2 = sp[-2][1], sp[-1][1]
                if abs(o2 - 0.21 * (o2 + n2)) > 1e-9:
                    mism.append("%s: O2 is not 21%% of the air" % t["id"])
                sp = sp[:-2] + [("AIR", round(o2 + n2, 6))]
            if [k for k, _ in sp] != names or [v for _, v in sp] != vals:
                mism.append("%s mixture %s vs page %s" % (t["id"], sp, mt.group(0)))
    need(fh, not mism, "targets.tsv matches its %d pinned target pages (%s)" % (len(T), "; ".join(mism) or "all"))

    mech = [r for r in ok if r["job"] == "mechanism"]
    if mech:
        m = mech[0]
        say(fh, "mechanism: shipped gri30.yaml vs ck2yaml of the official GRI-Mech 3.0 files, %d states" % m["states"])
        for k, v in m["max_rel_diff"].items():
            say(fh, "    %-14s max rel diff %.3e" % (k, v))
        need(fh, m["species"] == [53, 53] and m["reactions"] == [325, 325] and m["same_species_order"]
             and m["same_equations"] == 325 and m["same_duplicate_flags"] == 325,
             "53 species / 325 reactions, same order, same equations and duplicate flags")
        need(fh, max(m["max_rel_diff"].values()) == 0.0,
             "shipped gri30.yaml == the published GRI-Mech 3.0 files, bit for bit, in every rate constant, "
             "thermo and transport property")

    F = [r for r in ok if r["job"] == "flame"]
    if F:
        say(fh, "flames: %d, %d grid points total, %.0f s of solve" % (
            len(F), sum(r["points"] for r in F), sum(r["wall_s"] for r in F)))
        w = max(F, key=lambda r: abs(r["phi_calc"] - r["phi"]) / r["phi"])
        need(fh, abs(w["phi_calc"] - w["phi"]) / w["phi"] < 0.01,
             "every flame mixture has the stated phi (worst %.4f vs %.3f, %s)" % (w["phi_calc"], w["phi"], w.get("id", w["set"])))
        w = max(F, key=lambda r: r["mass_flux_spread"])
        need(fh, w["mass_flux_spread"] < 1e-4,
             "continuity: rho*u uniform through every flame, worst spread %.2e (the steady solver's rtol is 1e-4)"
             % w["mass_flux_spread"])
        w = max(F, key=lambda r: r["Y_sum_err"])
        # Newton converges each Y_k to rtol*Y_k + atol, so sum Y = 1 holds to rtol + K*atol (1.0005e-4)
        need(fh, all(r["Y_sum_err"] <= r["Y_sum_bound"] for r in F),
             "sum Y = 1 at every grid point within the solver's tolerance (%.2e), worst %.2e" % (
                 w["Y_sum_bound"], w["Y_sum_err"]))
        need(fh, all(math.isfinite(r["Su_cm_s"]) and r["Su_cm_s"] > 0 for r in F), "every flame speed finite and > 0")
        ule = [r for r in F if r["transport"] == "unity-Lewis-number"]
        oth = [r for r in F if r["transport"] != "unity-Lewis-number"]
        if ule:
            w = max(ule, key=lambda r: r["Z_drift_pointwise"])
            need(fh, w["Z_drift_pointwise"] < 1e-5,
                 "unity Lewis number: element mass fractions equal the inlet's at EVERY grid point of %d flames, "
                 "worst %.2e" % (len(ule), w["Z_drift_pointwise"]))
        if oth and ule:
            lo = min(oth, key=lambda r: r["Z_drift_pointwise"])
            need(fh, lo["Z_drift_pointwise"] > 100 * max(r["Z_drift_pointwise"] for r in ule),
                 "control: differential diffusion moves the element fractions in every mixture-averaged / "
                 "multicomponent flame, least %.2e (so the unity-Lewis identity is not vacuous)" % lo["Z_drift_pointwise"])
        w = max(F, key=lambda r: abs(r["h_drift_pointwise_K"]))
        say(fh, "    enthalpy vs inlet, worst over all flames and points: %.2f K-equivalent (%s)" % (
            w["h_drift_pointwise_K"], w["transport"]))
        w = max(F, key=lambda r: abs(r["T_out"] - r["T_ad"]))
        say(fh, "    outlet T minus HP-equilibrium T: worst %.2f K (%s phi %.2f P %.1f)" % (
            w["T_out"] - w["T_ad"], w["transport"], w["phi"], w["P_atm"]))
        # The flame-speed curve: Su peaks slightly rich of stoichiometric and falls with pressure.
        for P, T0 in sorted({(r["P_atm"], r["T0"]) for r in F if r["set"] == "curve_CH4"}):
            c = sorted((r["phi"], r["Su_cm_s"]) for r in F if r["set"] == "curve_CH4" and r["P_atm"] == P and r["T0"] == T0)
            pk = max(c, key=lambda x: x[1])
            say(fh, "    CH4-air %5.1f atm %3.0f K: Su peak %.3f cm/s at phi %.2f; phi 0.6 %.3f, phi 1.6 %.3f" % (
                P, T0, pk[1], pk[0], c[0][1], c[-1][1]))
        tg = {}
        for r in F:
            if r["set"] == "target":
                tg.setdefault(r["id"], {})[(r["transport"], r["slope"])] = r["Su_cm_s"]
        if tg:
            say(fh, "    flame-speed targets (cm/s): mix-avg at slope 0.1/0.05/0.025/0.0125, multicomponent+Soret "
                    "0.05/0.025, unity Le 0.05 | GRI-Mech 3.0 published | experiment")
            for t in T:
                if t["id"] in tg:
                    d = tg[t["id"]]
                    cols = [d.get(k) for k in [("mixture-averaged", s) for s in (0.1, 0.05, 0.025, 0.0125)]
                            + [("multicomponent", 0.05), ("multicomponent", 0.025), ("unity-Lewis-number", 0.05)]]
                    say(fh, "    %-5s %s | %7.2f | %7.2f" % (t["id"], " ".join("%7.3f" % c if c else "      -" for c in cols),
                                                         t["gri30"], t["exp"]))

    I = [r for r in ok if r["job"] == "ignition"]
    if I:
        say(fh, "ignition: %d reactors, %d steps, %.0f s" % (len(I), sum(r["steps"] for r in I), sum(r["wall_s"] for r in I)))
        need(fh, all(r["ignited"] for r in I), "every reactor ignited (T rose by over half its equilibrium rise)")
        off = {r.get("id", r["set"]): r["phi"] / r["phi_calc"] for r in I
               if abs(r["phi_calc"] - r["phi"]) / r["phi"] > print_tol(r["mixture"]) + PHI_TOL.get(r.get("id"), 1e-9)}
        say(fh, "    phi stated on the page vs phi of the page's own mole fractions, where they differ: %s" % (
            ", ".join("%s x%.4f" % kv for kv in sorted(off.items())) or "none"))
        # The two Hidaka C2H6 pages state a phi that is their mole fractions' phi times 2.75/3.5: the
        # same factor on both, i.e. a stoichiometric O2 of 2.75 per C2H6 instead of 3.5. The mole
        # fractions (which are what is simulated) are the page's data; the phi is its arithmetic.
        need(fh, sorted(off) == ["IG.6a", "IG.6b"] and all(abs(v - 2.75 / 3.5) < 2e-3 for v in off.values()),
             "every ignition mixture's mole fractions give its page's phi, except IG.6a/IG.6b, which are both "
             "off by the same factor 2.75/3.5 = 0.7857 (a stoichiometry slip on the page, not a transcription)")
        uv = [r for r in I if r["reactor"] == "UV"]
        hp = [r for r in I if r["reactor"] == "HP"]
        if uv:
            # u is not an integrated variable (T and Y are), so its drift is the integrator's error
            # summed over steps: CVODES holds each step's local error to rtol, so it is bounded by
            # rtol x steps. (A flat 1e-8 picked before the first run failed on Intel at 1.66e-8.)
            w = max(uv, key=lambda r: r["u_drift_rel"] / (r["rtol"] * r["total_steps"]))
            need(fh, all(r["u_drift_rel"] <= r["rtol"] * r["total_steps"] for r in uv),
                 "constant-volume: internal energy conserved through %d trajectories within rtol x steps; worst "
                 "%.2e of that bound (%.2e over %d steps); largest drift %.2e" % (
                     len(uv), w["u_drift_rel"] / (w["rtol"] * w["total_steps"]), w["u_drift_rel"], w["total_steps"],
                     max(r["u_drift_rel"] for r in uv)))
            w = max(uv, key=lambda r: r["rho_drift_rel"])
            need(fh, w["rho_drift_rel"] < 1e-12, "constant-volume: density constant, worst %.2e" % w["rho_drift_rel"])
        if hp:
            w = max(hp, key=lambda r: r["h_drift_rel"] / (r["rtol"] * r["total_steps"]))
            need(fh, all(r["h_drift_rel"] <= r["rtol"] * r["total_steps"] for r in hp),
                 "constant-pressure: enthalpy conserved through %d trajectories within rtol x steps; worst "
                 "%.2e of that bound (%.2e over %d steps); largest drift %.2e" % (
                     len(hp), w["h_drift_rel"] / (w["rtol"] * w["total_steps"]), w["h_drift_rel"], w["total_steps"],
                     max(r["h_drift_rel"] for r in hp)))
            w = max(hp, key=lambda r: r["P_drift_rel"])
            need(fh, w["P_drift_rel"] < 1e-12, "constant-pressure: pressure constant, worst %.2e" % w["P_drift_rel"])
        w = max(I, key=lambda r: r["Z_drift"])
        need(fh, w["Z_drift"] < 1e-9, "elements conserved through every trajectory, worst %.2e" % w["Z_drift"])
        w = max(I, key=lambda r: abs(r["dT_eq"]))
        need(fh, abs(w["dT_eq"]) < 1e-3,
             "the integrator's end state IS the equilibrium solver's (UV / HP): worst |T_end - T_eq| %.2e K" % abs(w["dT_eq"]))
        w = max(I, key=lambda r: r["dX_eq"])
        need(fh, w["dX_eq"] < 1e-6, "  ... and its composition, worst max|X_end - X_eq| %.2e" % w["dX_eq"])
        for r in sorted([r for r in I if r["set"] == "target"], key=lambda r: r["id"]):
            t = next(t for t in T if t["id"] == r["id"])
            say(fh, "    %-9s tau(max dT/dt) %9.2f us  tau(OH peak) %9.2f us | GRI-Mech 3.0 %s | experiment %g" % (
                r["id"], r["tau_dTdt_us"], r["tau_OHpeak_us"], "%g" % t["gri30"] if t["gri30"] else "-", t["exp"]))
        for P in (1.0, 10.0, 40.0):
            for phi in (0.5, 1.0, 2.0):
                c = sorted((r["T0"], r["tau_dTdt_us"]) for r in I if r["set"] == "map_UV" and r["P_atm"] == P and r["phi"] == phi)
                if c:
                    mono = all(c[i][1] > c[i + 1][1] for i in range(len(c) - 1))
                    say(fh, "    map UV P %4.0f atm phi %.1f: tau %.4g us at %g K -> %.4g us at %g K, monotone in T: %s" % (
                        P, phi, c[0][1], c[0][0], c[-1][1], c[-1][0], mono))

    E = [r for r in ok if r["job"] == "equilibrium"]
    if E:
        rows = [x for r in E for x in r["rows"]]
        say(fh, "equilibrium: %d states x %d solvers" % (len(rows), len(SOLVERS)))
        for s in SOLVERS:
            got = [x[s] for x in rows if "kkt" in x[s]]
            err = [x for x in rows if "error" in x[s]]
            say(fh, "    %-17s converged %d of %d; worst KKT residual %.2e; worst element drift %.2e" % (
                s, len(got), len(rows), max(g["kkt"] for g in got) if got else float("nan"),
                max(g["Z_drift"] for g in got) if got else float("nan")))
            for x in err[:5]:
                say(fh, "      fails at %s phi %.2f T %.0f P %g: %s" % (x["fuel"], x["phi"], x["T"], x["P_atm"], x[s]["error"]))
        vcs = [x["vcs"] for x in rows if "kkt" in x["vcs"]]
        need(fh, len(vcs) == len(rows), "vcs converges on all %d states" % len(rows))
        allg = [x[s] for x in rows for s in SOLVERS if "kkt" in x[s]]
        w = max(g["kkt"] for g in allg)
        # VCS's own convergence test is |dG/RT| <= 1e-6 for minor species, 1e-8 for major ones
        # (m_tolmin / m_tolmaj in vcs_solve.h); this residual is that quantity, with lambda fitted.
        need(fh, w <= 1e-6, "Gibbs-minimum certificate: mu_k/RT = sum_j a_kj lambda_j for every species above 1e-12, "
                           "every solver, every state; worst residual %.2e" % w)
        w = max(g["Z_drift"] for g in allg)
        need(fh, w < 1e-9, "elements conserved by every solver, worst %.2e" % w)
        for s in ("gibbs", "element_potential"):
            both = [x[s] for x in rows if "dX_vs_vcs" in x[s]]
            if both:
                need(fh, max(b["dX_vs_vcs"] for b in both) < 1e-6,
                     "%s == vcs where both converge (%d states): worst max|dX| %.2e, worst |dg|/RT %.2e" % (
                         s, len(both), max(b["dX_vs_vcs"] for b in both), max(b["dg_vs_vcs_RT"] for b in both)))

    D = [r for r in ok if r["job"] == "determinism"]
    if D:
        d = D[0]
        need(fh, d["flame_Su"][0] == d["flame_Su"][1] and d["ignition_tau"][0] == d["ignition_tau"][1]
             and d["ignition_T_end"][0] == d["ignition_T_end"][1],
             "determinism: a flame and a reactor repeated in one process are bit-identical")


def main():
    os.makedirs(OUT, exist_ok=True)
    print("cantera %s, numpy %s, python %d.%d.%d" % (str(getattr(ct, "__version__", "unknown")),
                                                    str(np.__version__), *sys.version_info[:3]), flush=True)
    # the official files, through cantera's own converter
    rc = subprocess.run([sys.executable, "-m", "cantera.ck2yaml", "--input=" + os.path.join(SRC, "grimech30.dat"),
                         "--thermo=" + os.path.join(SRC, "thermo30.dat"),
                         "--transport=" + os.path.join(SRC, "transport.dat"), "--output=" + OFFICIAL, "--quiet"],
                        capture_output=True, text=True)
    print("ck2yaml rc %d %s" % (rc.returncode, (rc.stdout + rc.stderr).strip()[-500:]), flush=True)
    J = jobs()
    J.sort(key=cost, reverse=True)
    print("%d jobs on %d workers" % (len(J), NP), flush=True)
    R = []
    t0 = time.time()
    phase = {}
    with mp.get_context("fork").Pool(NP, maxtasksperchild=20) as pool, \
            open(os.path.join(OUT, "results.jsonl"), "w") as fh:
        for i, r in enumerate(pool.imap_unordered(run, J)):
            R.append(r)
            fh.write(json.dumps(r, default=float) + "\n")
            fh.flush()
            key = r.get("set", r["job"]) if r["job"] != "equilibrium" else "equilibrium"
            phase[key] = phase.get(key, 0.0) + r["wall_s"]
            if (i + 1) % 25 == 0 or not r.get("ok"):
                print("  %d/%d done, %.0f s%s" % (i + 1, len(J), time.time() - t0,
                                                  "" if r.get("ok") else "  FAILED %s" % r.get("error")), flush=True)
    wall = time.time() - t0
    with open(os.path.join(OUT, "phases.txt"), "w") as fh:
        fh.write("jobs_wall_s\t%.1f\n" % wall)
        for k, v in sorted(phase.items()):
            fh.write("cpu_s_%s\t%.1f\n" % (k, v))
    with open(os.path.join(OUT, "check.txt"), "w") as fh:
        check(R, fh)
        say(fh, "ALL CHECKS PASSED" if not FAIL else "FAILED: %d checks" % len(FAIL))
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
