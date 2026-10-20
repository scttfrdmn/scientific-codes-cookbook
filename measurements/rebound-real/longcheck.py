"""rebound-real, part 3: check the long runs and measure how fast a 1 m change in Mercury grows.

    python3 -u longcheck.py <nominal.npz> <shadow.npz> <span Myr> <out.json> <check.txt>
"""
import json
import math
import sys

import numpy as np

NOM, SHA, SPAN, OUT, CHK = sys.argv[1], sys.argv[2], float(sys.argv[3]), sys.argv[4], sys.argv[5]
EPS = 2.0 ** -52
YR = 365.25
AU_M = 149597870700.0
checks, R = [], {}


def check(name, ok, detail):
    ok = bool(ok)
    checks.append((name, ok, str(detail)))
    print("%s\t%s\t%s" % ("PASS" if ok else "FAIL", name, detail), flush=True)


runs = {"nominal": np.load(NOM), "shadow": np.load(SHA)}
for lab, r in runs.items():
    n = len(r["t"])
    want = int(round(SPAN / 0.5)) + 1
    check("%s run reached %g Myr (%d checkpoints)" % (lab, SPAN, want), n == want and abs(r["t"][-1] / YR / 1e6 - SPAN) < 1e-6,
          "%d checkpoints, t = %.6f Myr" % (n, r["t"][-1] / YR / 1e6))
    N = int(r["steps"])
    dE, dL = float(np.max(np.abs(r["dE"]))), float(np.max(r["dL"]))
    check("%s: WHFast c17 |dE/E| < 1e-9 over %g Myr (REBOUND's own test bound for corrector 17)" % (lab, SPAN),
          dE < 1e-9, "%.2e" % dE)
    check("%s: WHFast conserves L to round-off: |dL|/|L| < 10*eps*sqrt(N)" % lab, dL < 10 * EPS * math.sqrt(N),
          "%.2e < %.2e (N=%d)" % (dL, 10 * EPS * math.sqrt(N), N))
    e = np.array(r["e"]); a = np.array(r["a"])
    check("%s: every orbit stays bound (e < 1, a > 0) at every checkpoint" % lab, np.all(e < 1) and np.all(a > 0),
          "max e %.4f (Pluto %.4f, Mercury %.4f)" % (e.max(), e[:, 8].max(), e[:, 0].max()))
    half = len(r["t"]) // 2
    R[lab] = {"steps": N, "dE_max": dE, "dL_max": dL, "brouwer_L": 10 * EPS * math.sqrt(N),
              "dE_max_first_half": float(np.max(np.abs(r["dE"][:half + 1]))),
              "dE_max_second_half": float(np.max(np.abs(r["dE"][half:]))),
              "a_rel_range": [float((a[:, i].max() - a[:, i].min()) / a[0, i]) for i in range(a.shape[1])],
              "e_range": [[float(e[:, i].min()), float(e[:, i].max())] for i in range(e.shape[1])],
              "wall_s": float(r["wall"][-1]), "us_per_step": float(r["wall"][-1] / N * 1e6)}

# ---- the shadow: Mercury displaced by 1 m at J2000. Report how the separation grows.
n, s = runs["nominal"], runs["shadow"]
t = n["t"] / YR / 1e6
helio = lambda r, i: r["pos"][:, i] - r["pos"][:, 0]
sep = {nm: np.linalg.norm(helio(n, i) - helio(s, i), axis=1) * AU_M for i, nm in
       enumerate(["Mercury", "Venus", "EMB", "Mars", "Jupiter", "Saturn", "Uranus", "Neptune", "Pluto"], start=1)}
check("the shadow starts 1 m from the nominal run", abs(sep["Mercury"][0] - float(s["shift_m"])) < 1e-3,
      "%.6f m" % sep["Mercury"][0])
d = sep["Mercury"]
win = (d > 1e3 * d[0]) & (d < 1e-2 * AU_M)
fit = None
if win.sum() >= 4:
    slope = np.polyfit(t[win], np.log(d[win]), 1)[0]
    fit = {"lyapunov_time_myr": float(1 / slope), "window_myr": [float(t[win][0]), float(t[win][-1])], "points": int(win.sum())}
R["shadow"] = {"t_myr": t.tolist(), "sep_m": {k: v.tolist() for k, v in sep.items()}, "fit": fit}
print("shadow Mercury separation (m) at 10/25/50/75/100 Myr:",
      ["%.3g" % np.interp(x, t, d) for x in (10, 25, 50, 75, 100) if x <= t[-1]], flush=True)
print("e-folding fit:", fit, flush=True)
R["failed"] = [c[0] for c in checks if not c[1]]
R["checks"] = checks
json.dump(R, open(OUT, "w"), indent=1)
with open(CHK, "a") as fh:
    for nm, ok, dd in checks:
        fh.write("%s\t%s\t%s\n" % ("PASS" if ok else "FAIL", nm, dd))
sys.exit(1 if R["failed"] else 0)
