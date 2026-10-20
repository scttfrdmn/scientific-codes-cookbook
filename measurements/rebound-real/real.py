"""rebound-real, part 1: the Solar System from DE440 at J2000, integrated by REBOUND and checked
against DE440 itself over its 1550-2650 span, plus the integrators' own conservation and order laws.

    python3 -u real.py <dir with de440.bsp, gm_de440.tpc> <out dir>
"""
import hashlib
import json
import math
import os
import sys
import time

import numpy as np
import rebound

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ic import DE440, NAMES, C_AU_DAY, AU_KM, positions, ang_mom, orbit  # noqa: E402

DATA, OUT = sys.argv[1], sys.argv[2]
EPS = 2.0 ** -52
YR = 365.25
FWD = [1, 10, 100, 300, 600]           # years after J2000 (DE440 ends 2650)
BWD = [-1, -10, -100, -400]            # years before (DE440 starts 1550)
SAMPLE = 50.0                          # days between Mercury perihelion samples

checks, R = [], {}


def check(name, ok, detail):
    ok = bool(ok)
    checks.append((name, ok, str(detail)))
    print("%s\t%s\t%s" % ("PASS" if ok else "FAIL", name, detail), flush=True)


def ver(mod):
    return str(getattr(mod, "__version__", "unknown"))


import jplephem  # noqa: E402
R["versions"] = {"rebound": ver(rebound), "numpy": ver(np), "jplephem": ver(jplephem)}
print("versions", R["versions"], flush=True)

import ctypes  # noqa: E402
fpc = rebound.clibrebound.reb_check_fp_contract
fpc.restype = ctypes.c_int
R["fp_contract"] = int(fpc())
check("libREBOUND built with FP contraction off (REBOUND's own test_fpcontract)", R["fp_contract"] == 0,
      "reb_check_fp_contract() = %d" % R["fp_contract"])

de = DE440(DATA)
R["gm_au3_day2"] = [float(g) for g in de.gm]
R["jd_range"] = list(de.jd_range)
lo, hi = (de.jd_range[0] - 2451545.0) / YR, (de.jd_range[1] - 2451545.0) / YR
check("DE440 covers every checkpoint", lo < min(BWD) and hi > max(FWD), "J2000 %+.1f .. %+.1f yr" % (lo, hi))
sim0 = de.simulation()
check("ten bodies: Sun + nine system barycentres", sim0.N == 10, "N = %d" % sim0.N)
p0 = positions(sim0)
R["ic_sha256"] = hashlib.sha256(p0.tobytes()).hexdigest()


def gr_force(simp):
    """Sun-centred 1PN (Schwarzschild, harmonic gauge) acceleration on each planet; reaction on the Sun."""
    ps = simp.contents.particles
    s = ps[0]
    mu = s.m
    c2 = C_AU_DAY ** 2
    sx = sy = sz = 0.0
    for i in range(1, simp.contents.N):
        p = ps[i]
        x, y, z = p.x - s.x, p.y - s.y, p.z - s.z
        vx, vy, vz = p.vx - s.vx, p.vy - s.vy, p.vz - s.vz
        r2 = x * x + y * y + z * z
        r = math.sqrt(r2)
        v2 = vx * vx + vy * vy + vz * vz
        rv = x * vx + y * vy + z * vz
        f = mu / (c2 * r2 * r)
        a1 = f * (4.0 * mu / r - v2)
        a2 = f * 4.0 * rv
        ax, ay, az = a1 * x + a2 * vx, a1 * y + a2 * vy, a1 * z + a2 * vz
        p.ax += ax; p.ay += ay; p.az += az
        q = p.m / mu
        sx -= q * ax; sy -= q * ay; sz -= q * az
    s.ax += sx; s.ay += sy; s.az += sz


def evec(sim):
    """Mercury's heliocentric Laplace-Runge-Lenz (eccentricity) vector and orbit normal."""
    s, p = sim.particles[0], sim.particles[1]
    r = np.array([p.x - s.x, p.y - s.y, p.z - s.z])
    v = np.array([p.vx - s.vx, p.vy - s.vy, p.vz - s.vz])
    mu = s.m + p.m
    h = np.cross(r, v)
    return np.cross(v, h) / mu - r / np.linalg.norm(r), h / np.linalg.norm(h)


def ias15_run(sign, gr):
    """IAS15 from J2000 to the furthest checkpoint in one direction, sampling Mercury's e-vector."""
    marks = FWD if sign > 0 else BWD
    sim = de.simulation()
    sim.integrator = "ias15"
    if gr:
        sim.force_is_velocity_dependent = 1
        sim.additional_forces = gr_force
    e0, L0 = sim.energy(), ang_mom(sim)
    end = abs(marks[-1]) * YR
    ts = sorted(set([k * SAMPLE for k in range(1, int(end // SAMPLE) + 1)] + [abs(m) * YR for m in marks]))
    out = {"t": [], "e": [], "h": [], "pos": {}, "dE": 0.0, "dL": 0.0}
    t0 = time.time()
    for t in ts:
        sim.integrate(sign * t)
        e, h = evec(sim)
        out["t"].append(sign * t); out["e"].append(e); out["h"].append(h)
        if not gr:
            out["dE"] = max(out["dE"], abs((sim.energy() - e0) / e0))
            out["dL"] = max(out["dL"], float(np.linalg.norm(ang_mom(sim) - L0) / np.linalg.norm(L0)))
        for m in marks:
            if abs(abs(m) * YR - t) < 1e-9:
                out["pos"][m] = positions(sim)
    out["steps"] = int(sim.steps_done)
    out["wall"] = time.time() - t0
    print("ias15 %s %s: %d steps, %.1f s" % ("fwd" if sign > 0 else "bwd", "GR" if gr else "Newton",
                                           out["steps"], out["wall"]), flush=True)
    return out


runs = {(s, g): ias15_run(s, g) for s in (1, -1) for g in (False, True)}

# ---------------------------------------------- 1. IAS15 conservation, against Brouwer's limit
# IAS15's error is a random walk of round-off (Rein & Spiegel 2015): no worse than eps*sqrt(N).
for s in (1, -1):
    r = runs[(s, False)]
    bound = EPS * math.sqrt(r["steps"])
    tag = "fwd 600 yr" if s > 0 else "bwd 400 yr"
    R["ias15_%s" % ("fwd" if s > 0 else "bwd")] = {"steps": r["steps"], "dE": r["dE"], "dL": r["dL"],
                                                    "brouwer": bound, "wall_newton": r["wall"],
                                                    "wall_gr": runs[(s, True)]["wall"]}
    check("IAS15 energy, %s, |dE/E| < eps*sqrt(N)" % tag, r["dE"] < bound, "%.2e < %.2e (N=%d)" % (r["dE"], bound, r["steps"]))
    check("IAS15 angular momentum, %s, |dL|/|L| < eps*sqrt(N)" % tag, r["dL"] < bound, "%.2e < %.2e" % (r["dL"], bound))

# ------------------------------------------------- 2. agreement with DE440 along its span
err = {}
for g in (False, True):
    for s in (1, -1):
        for m, p in runs[(s, g)]["pos"].items():
            hel = p[1:] - p[0]
            err[(g, m)] = np.linalg.norm(hel - de.helio(m * YR), axis=1) * AU_KM   # km, per body
marks = sorted(BWD + FWD, key=lambda m: (abs(m), m))
tab = []
for m in marks:
    for g in (False, True):
        tab.append({"years": m, "model": "GR" if g else "Newton", "err_km": [float(x) for x in err[(g, m)]]})
R["de440_err_km"] = tab
print("heliocentric position error vs DE440, km (" + " ".join(NAMES[1:]) + ")")
for row in tab:
    print("%+5d %-6s %s" % (row["years"], row["model"], " ".join("%.3g" % x for x in row["err_km"])), flush=True)
dist = np.linalg.norm(de.helio(0.0), axis=1) * AU_KM
rel1 = np.max([err[(True, m)] / dist for m in (1, -1)], axis=0)
R["de440_rel_err_1yr_gr"] = [float(x) for x in rel1]
# A band, set from the first Graviton4 run (worst 1.3e-9, Mercury). The Earth-Moon barycentre is
# excluded and reported: a point-mass EMB is this model's largest omission (7.8e-7 at 1 yr, growing
# linearly, i.e. a mean-motion offset).
keep = [i for i in range(len(rel1)) if NAMES[i + 1] != "EMB"]
check("+-1 yr, Sun-1PN model: every body but the EMB within 1e-8 of its distance of DE440",
      np.all(rel1[keep] < 1e-8), "worst %.1e (%s); EMB %.1e reported" % (
          rel1[keep].max(), NAMES[1 + keep[int(np.argmax(rel1[keep]))]], rel1[NAMES.index("EMB") - 1]))
for m in (600, -400):
    n, g = err[(False, m)][0], err[(True, m)][0]
    check("GR is what separates Mercury from DE440 (%+d yr): Newton err > 10x GR err" % m, n > 10 * g,
          "%.0f km vs %.0f km (%.0fx)" % (n, g, n / g))

# ----------------------------------------------- 3. Mercury's relativistic perihelion advance
p0, v0 = de.states(0.0)
mu_s = de.gm[0]
el = orbit(de.simulation().particles[1], de.simulation().particles[0])
a, e = el.a, el.e
P = 2 * math.pi * math.sqrt(a ** 3 / (mu_s + de.gm[1]))
theory = 6 * math.pi * mu_s / (C_AU_DAY ** 2 * a * (1 - e * e)) * (36525.0 / P) * 180 / math.pi * 3600
rates = []
for s in (1, -1):
    n, g = runs[(s, False)], runs[(s, True)]
    t = np.array(n["t"])
    ang = []
    for en, eg, hg in zip(n["e"], g["e"], g["h"]):
        en = en - np.dot(en, hg) * hg
        ang.append(math.atan2(np.dot(np.cross(en, eg), hg), np.dot(en, eg)))
    rate = np.polyfit(t / 36525.0, np.unwrap(np.array(ang)), 1)[0] * 180 / math.pi * 3600
    rates.append(float(rate))
R["gr_precession"] = {"theory_arcsec_cy": theory, "measured_fwd": rates[0], "measured_bwd": rates[1], "a": a, "e": e}
for s, rate in zip(("fwd 600 yr", "bwd 400 yr"), rates):
    check("Mercury GR perihelion advance == 6 pi GM/(c^2 a(1-e^2)) per orbit, %s, within 1%%" % s,
          abs(rate / theory - 1) < 0.01, "%.3f vs %.3f arcsec/century" % (rate, theory))

# ------------------------------------------------------ 4. WHFast's order: error ~ dt^2
def whfast_err(dt, corrector, years=1000.0, samples=200):
    sim = de.simulation()
    sim.integrator = "whfast"
    sim.integrator.corrector = corrector
    sim.dt = dt
    e0, L0 = sim.energy(), ang_mom(sim)
    dE = dL = 0.0
    t0 = time.time()
    for k in range(1, samples + 1):
        sim.integrate(years * YR * k / samples, exact_finish_time=0)
        sim.synchronize()
        dE = max(dE, abs((sim.energy() - e0) / e0))
        dL = max(dL, float(np.linalg.norm(ang_mom(sim) - L0) / np.linalg.norm(L0)))
    return dE, dL, int(sim.steps_done), time.time() - t0, sim


ladder = {}
for dt in (4.0, 2.0, 1.0):
    dE, dL, N, w, _ = whfast_err(dt, 0)
    ladder[dt] = (dE, dL, N)
    print("whfast plain dt=%g: dE %.3e dL %.3e N %d %.1f s" % (dt, dE, dL, N, w), flush=True)
r1, r2 = ladder[4.0][0] / ladder[2.0][0], ladder[2.0][0] / ladder[1.0][0]
R["whfast_ladder"] = {str(k): list(v) for k, v in ladder.items()}
R["whfast_ladder_ratios"] = [r1, r2]
for lab, r in (("4->2 d", r1), ("2->1 d", r2)):
    check("WHFast is second order: halving dt cuts max|dE/E| by 4 (+-10%%), %s" % lab, abs(r / 4 - 1) < 0.1, "x%.3f" % r)
# Every WHFast sub-step (Jacobi transform, Kepler drift, interaction kick) conserves L exactly, so its
# L error is round-off only: a random walk of a few ulp per step (three transforms, each rounding).
for dt, (dE, dL, N) in ladder.items():
    check("WHFast conserves L to round-off whatever dt (dt=%g d): |dL|/|L| < 10*eps*sqrt(N)" % dt,
          dL < 10 * EPS * math.sqrt(N), "%.2e < %.2e" % (dL, 10 * EPS * math.sqrt(N)))

# The long run's configuration: 17th-order correctors, dt = 4 d.
dE17, dL17, N17, w17, s17 = whfast_err(4.0, 17)
R["whfast_c17_1000yr"] = {"dE": dE17, "dL": dL17, "steps": N17, "us_per_step": w17 / N17 * 1e6}
check("WHFast + 17th-order correctors beats plain WHFast at dt=4 d by > 100x",
      ladder[4.0][0] > 100 * dE17, "%.2e vs %.2e" % (dE17, ladder[4.0][0]))
check("WHFast c17 dt=4 d, 1000 yr: |dE/E| < 1e-9 (REBOUND's own test bound for corrector 17)",
      dE17 < 1e-9, "%.2e" % dE17)

# --------------------------- 5. the long run's integrator against IAS15 at +100 and +600 yr
wf = {}
for yrs in (100, 600):
    sim = de.simulation()
    sim.integrator = "whfast"; sim.integrator.corrector = 17; sim.integrator.safe_mode = 0; sim.dt = 4.0
    sim.integrate(yrs * YR, exact_finish_time=1)
    sim.synchronize()
    p = positions(sim)
    wf[yrs] = float(np.max(np.linalg.norm((p[1:] - p[0]) - (runs[(1, False)]["pos"][yrs][1:] - runs[(1, False)]["pos"][yrs][0]), axis=1)) * AU_KM)
R["whfast_c17_vs_ias15_km"] = wf
print("WHFast c17 vs IAS15, worst body: %s km" % wf, flush=True)

# --------------------------------------------------------------------- 6. determinism
def rerun():
    sim = de.simulation(); sim.integrator = "ias15"
    sim.integrate(100 * YR)
    return positions(sim).tobytes()
a1, a2 = rerun(), rerun()
check("IAS15 +100 yr is deterministic: two reruns, identical bytes", a1 == a2, "identical" if a1 == a2 else "differs")

R["hash_fwd600_newton"] = hashlib.sha256(runs[(1, False)]["pos"][600].tobytes()).hexdigest()
R["hash_fwd600_gr"] = hashlib.sha256(runs[(1, True)]["pos"][600].tobytes()).hexdigest()
R["failed"] = [c[0] for c in checks if not c[1]]
R["checks"] = checks
np.savez(os.path.join(OUT, "ephem.npz"),
         **{"%s_%s_%+d" % ("gr" if g else "newton", "pos", m): runs[(1 if m > 0 else -1, g)]["pos"][m]
            for g in (False, True) for m in BWD + FWD},
         **{"de440_helio_%+d" % m: de.helio(m * YR) for m in BWD + FWD})
json.dump(R, open(os.path.join(OUT, "results.json"), "w"), indent=1, default=float)
with open(os.path.join(OUT, "check.txt"), "a") as fh:
    for n, ok, d in checks:
        fh.write("%s\t%s\t%s\n" % ("PASS" if ok else "FAIL", n, d))
print("failed:", R["failed"] or "none", flush=True)
import resource
print("maxrss_kib real.py %d" % resource.getrusage(resource.RUSAGE_SELF).ru_maxrss, flush=True)
sys.exit(1 if R["failed"] else 0)
