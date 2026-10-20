"""Initial conditions from JPL DE440: the Sun and the nine planetary-system barycentres.

Units: AU, day, and G = 1 with each mass given as GM in AU^3/day^2, so DE440's GM constants enter
unrounded. Time is TDB; t = 0 is J2000.0 (JD 2451545.0 TDB).
"""
import os
import re

import numpy as np
import rebound
from jplephem.spk import SPK

AU_KM = 149597870.700            # km; DE440's AU (the IAU 2012 definition)
C_AU_DAY = 299792.458 * 86400.0 / AU_KM
EPOCH_JD = 2451545.0             # J2000.0 TDB
NAMES = ["Sun", "Mercury", "Venus", "EMB", "Mars", "Jupiter", "Saturn", "Uranus", "Neptune", "Pluto"]
CODES = [10, 1, 2, 3, 4, 5, 6, 7, 8, 9]


def gm_table(path):
    """BODYnnn_GM = ( value ) from a NAIF text PCK, in km^3/s^2."""
    txt = open(path).read().replace("D+", "E+").replace("D-", "E-")
    return {int(k): float(v) for k, v in re.findall(r"BODY(\d+)_GM\s*=\s*\(\s*([-+0-9.Ee]+)", txt)}


class DE440:
    def __init__(self, data):
        self.kernel = SPK.open(os.path.join(data, "de440.bsp"))
        self.gm_km = gm_table(os.path.join(data, "gm_de440.tpc"))
        gm = dict(self.gm_km)
        if 3 not in gm:                                   # Earth-Moon barycentre = Earth + Moon
            gm[3] = gm[399] + gm[301]
        self.gm = np.array([gm[c] * 86400.0 ** 2 / AU_KM ** 3 for c in CODES])
        seg = [self.kernel[0, c] for c in CODES]
        self.jd_range = (max(s.start_jd for s in seg), min(s.end_jd for s in seg))

    def states(self, t_days):
        """Barycentric positions (AU) and velocities (AU/day) at J2000 + t_days, shape (10, 3) each."""
        pos, vel = [], []
        for c in CODES:
            p, v = self.kernel[0, c].compute_and_differentiate(EPOCH_JD, float(t_days))
            pos.append(np.asarray(p) / AU_KM)
            vel.append(np.asarray(v) / AU_KM)
        return np.array(pos), np.array(vel)

    def helio(self, t_days):
        p, _ = self.states(t_days)
        return p[1:] - p[0]

    def simulation(self, shift_mercury_m=0.0):
        p, v = self.states(0.0)
        sim = rebound.Simulation()
        sim.G = 1.0
        for i in range(len(CODES)):
            x = p[i][0] + (shift_mercury_m / 1000.0 / AU_KM if i == 1 else 0.0)
            sim.add(m=self.gm[i], x=x, y=p[i][1], z=p[i][2], vx=v[i][0], vy=v[i][1], vz=v[i][2])
        return sim


def positions(sim):
    ps = sim.particles
    return np.array([[ps[i].x, ps[i].y, ps[i].z] for i in range(sim.N)])


def velocities(sim):
    ps = sim.particles
    return np.array([[ps[i].vx, ps[i].vy, ps[i].vz] for i in range(sim.N)])


def ang_mom(sim):
    return np.array([float(c) for c in sim.angular_momentum()])


def orbit(p, primary):
    """REBOUND renamed calculate_orbit -> orbit across generations; use whichever exists."""
    f = getattr(p, "orbit", None) or getattr(p, "calculate_orbit")
    return f(primary=primary)
