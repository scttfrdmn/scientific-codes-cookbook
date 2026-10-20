"""rebound-real, part 2: the DE440 Solar System for a long span with WHFast.

    python3 -u long.py <data dir> <out.npz> <span Myr> <shift_mercury_m>

WHFast, 17th-order symplectic correctors, dt = 4 days, safe_mode off (the fast configuration REBOUND
documents for long runs). Every 0.5 Myr: synchronise, record every state vector, the energy, the
angular momentum and the heliocentric elements.
"""
import math
import os
import sys
import time

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ic import DE440, positions, velocities, ang_mom, orbit  # noqa: E402

DATA, OUT, SPAN, SHIFT = sys.argv[1], sys.argv[2], float(sys.argv[3]), float(sys.argv[4])
YR = 365.25
DT = 4.0
EVERY = 0.5e6 * YR                              # checkpoint spacing, days
STEPS = int(round(EVERY / DT))                  # fixed steps per checkpoint (no exact-finish stops)

de = DE440(DATA)
sim = de.simulation(shift_mercury_m=SHIFT)
sim.integrator = "whfast"
sim.integrator.corrector = 17
sim.integrator.safe_mode = 0
sim.dt = DT
e0, L0 = sim.energy(), ang_mom(sim)
rec = {k: [] for k in ("t", "pos", "vel", "dE", "dL", "a", "e", "inc", "wall")}


def record(w):
    s = sim.particles[0]
    els = [orbit(sim.particles[i], s) for i in range(1, sim.N)]
    rec["t"].append(sim.t); rec["pos"].append(positions(sim)); rec["vel"].append(velocities(sim))
    rec["dE"].append((sim.energy() - e0) / e0)
    rec["dL"].append(float(np.linalg.norm(ang_mom(sim) - L0) / np.linalg.norm(L0)))
    rec["a"].append([o.a for o in els]); rec["e"].append([o.e for o in els]); rec["inc"].append([o.inc for o in els])
    rec["wall"].append(w)


t0 = time.time()
record(0.0)
n_ck = int(round(SPAN * 1e6 * YR / EVERY))
for k in range(1, n_ck + 1):
    sim.steps(STEPS)
    sim.synchronize()
    record(time.time() - t0)
    print("%.1f Myr  wall %.0f s  dE/E %.3e  dL/L %.3e  e_Merc %.4f" % (
        sim.t / YR / 1e6, rec["wall"][-1], rec["dE"][-1], rec["dL"][-1], rec["e"][-1][0]), flush=True)
    np.savez(OUT, steps=int(sim.steps_done), dt=DT, shift_m=SHIFT, **{k: np.array(v) for k, v in rec.items()})
print("done: %d steps, %.0f s, %.3f us/step" % (sim.steps_done, time.time() - t0,
                                                (time.time() - t0) / sim.steps_done * 1e6), flush=True)
import resource
print("maxrss_kib long.py shift=%g %d" % (SHIFT, resource.getrusage(resource.RUSAGE_SELF).ru_maxrss), flush=True)
