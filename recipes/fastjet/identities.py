#!/usr/bin/env python3
"""FastJet on Graviton4: infrared and collinear safety, which is why these algorithms exist.

Anti-k_t and Cambridge/Aachen were designed so that adding an infinitely soft particle, or
splitting a particle into two exactly collinear ones, does not change the hard jets. That
is a textbook property with no reference value and no tolerance, and it is the strongest
thing a jet clusterer can be held to.

What is asserted here, and why each is more than a band:

 * MOMENTUM CONSERVATION -- jet 4-momenta sum to the input 4-momentum. Exact on a
   hand-written event; 7.7e-14 on a 613-particle Pythia event.
 * COLLINEAR SAFETY -- an exactly collinear split changes the jets by exactly 0.
 * INFRARED SAFETY AS A SCALING LAW, not a threshold. A soft particle of energy eps
   clustered into a jet changes that jet by exactly eps, measured at eps = 1e-3, 1e-6 and
   1e-9. A band would pass an implementation whose error happened to be small; the scaling
   says the change IS the soft momentum and nothing else.
 * THREE ALGORITHMS AGREE exactly on a well-separated event -- anti-k_t, Cambridge/Aachen
   and k_t cluster by different sequences and must still find the same jets.

Both safety claims carry a control, because "nothing changed" is the easiest result to get
for the wrong reason:

 1. A NON-collinear split must change the jets, and the control is graded -- at dphi =
    0.05, 0.2 and 1.0 the change grows and eventually the jet count moves. Without it,
    the exact zero at perfect collinearity could mean splitting is simply ignored.
 2. Adding a HARD particle must change the jets, or the soft-particle invariance would be
    satisfied by an algorithm that ignores its input.

Run with `python3 -u` so a killed run still has its output in command.log.
"""
import math
import sys

import numpy as np
import fastjet
import awkward as ak

ROWS = []


def rec(k, v):
    print("%s\t%s" % (k, v))
    ROWS.append((k, v))


def die(msg):
    print("FAIL: %s" % msg)
    sys.exit(1)


rec("fastjet", getattr(fastjet, "__version__", "unknown"))
rec("awkward", ak.__version__)
rec("numpy", np.__version__)

R = 0.4
MIN_PT = 5.0
# Four well-separated hard particles: with R = 0.4 each is its own jet, so the jet
# structure is hand-checkable and every invariance below has an obvious expected answer.
HARD = [(50.0, 0.0, 10.0, 51.0),
        (-45.0, 5.0, -20.0, 49.5),
        (3.0, 40.0, 15.0, 43.0),
        (-5.0, -38.0, -12.0, 40.0)]


def cluster(parts, algo=None, min_pt=MIN_PT, radius=R):
    jd = fastjet.JetDefinition(algo if algo is not None else fastjet.antikt_algorithm,
                               radius)
    cs = fastjet.ClusterSequence(
        ak.Array([{"px": p[0], "py": p[1], "pz": p[2], "E": p[3]} for p in parts]), jd)
    out = []
    for j in ak.to_list(cs.inclusive_jets(min_pt=min_pt)):
        out.append(((j["px"] ** 2 + j["py"] ** 2) ** 0.5,
                    j["px"], j["py"], j["pz"], j["E"]))
    return sorted(out, reverse=True)


def dmax(a, b):
    """Largest 4-momentum difference between two jet lists; inf if the counts differ."""
    if len(a) != len(b):
        return float("inf")
    return max(max(abs(x - y) for x, y in zip(p, q)) for p, q in zip(a, b))


base = cluster(HARD)
rec("radius", "%g" % R)
rec("base_jets", "%d" % len(base))
rec("base_leading_pt", "%.9f" % base[0][0])
if len(base) != 4:
    die("the four well-separated particles gave %d jets at R=%g, not 4; the rest of this "
        "recipe assumes one jet per particle" % (len(base), R))

# --------------------------------------------- 1. momentum conservation, hand-written event
allj = cluster(HARD, min_pt=0.0)
tot_in = np.array([sum(p[i] for p in HARD) for i in range(4)])
tot_jet = np.array([sum(j[i + 1] for j in allj) for i in range(4)])
if not (np.all(np.isfinite(tot_in)) and np.all(np.isfinite(tot_jet))):
    die("a non-finite total 4-momentum")
cons = float(np.abs(tot_in - tot_jet).max())
rec("input_total", ",".join("%.6f" % v for v in tot_in))
rec("jet_total", ",".join("%.6f" % v for v in tot_jet))
rec("momentum_conservation", "%.3e" % cons)
if cons != 0.0:
    die("the jets do not sum to the input 4-momentum (max |diff| %.3e); with no pt cut "
        "clustering is a partition and must conserve momentum exactly" % cons)

# ------------------------------------------- 2. collinear safety, exact, with its control
p0 = HARD[0]
half = (p0[0] / 2, p0[1] / 2, p0[2] / 2, p0[3] / 2)
coll = [half, half] + HARD[1:]
d_coll = dmax(base, cluster(coll))
rec("collinear_split_change", "%.3e" % d_coll)
if d_coll != 0.0:
    die("splitting a particle into two exactly collinear halves changed the jets by %.3e; "
        "collinear safety requires exactly zero" % d_coll)


def rotate(p, dphi):
    c, s = math.cos(dphi), math.sin(dphi)
    return (p[0] * c - p[1] * s, p[0] * s + p[1] * c, p[2], p[3])


# THE CONTROL, graded: the further from collinear, the more the jets must move.
grades = []
for dphi in (0.05, 0.20, 1.00):
    a = rotate(half, dphi / 2)
    b = rotate(half, -dphi / 2)
    d = dmax(base, cluster([a, b] + HARD[1:]))
    grades.append((dphi, d))
    rec("control_split_dphi_%.2f" % dphi, "%.3e" % d)
if not all(d > 0 for _, d in grades):
    die("a non-collinear split left the jets unchanged, so the exact zero above does not "
        "show collinear safety -- it would also hold if splitting were ignored")
if not (grades[0][1] < grades[1][1] < grades[2][1]):
    die("the control is not graded: changes at dphi %s were %s, which should increase"
        % ([g[0] for g in grades], ["%.3e" % g[1] for g in grades]))
rec("collinear_control", "0 when exactly collinear; %.1e / %.1e / inf as dphi grows"
    % (grades[0][1], grades[1][1]))

# --------------- 3. infrared safety as a SCALING LAW: the change equals the soft momentum
n0 = np.array(p0[:3], dtype="float64")
n0 = n0 / np.linalg.norm(n0)
ladder = []
for eps in (1e-3, 1e-6, 1e-9):
    soft = (n0[0] * eps, n0[1] * eps, n0[2] * eps, eps)
    t = cluster(HARD + [soft])
    if len(t) != len(base):
        die("adding a %.0e GeV particle changed the jet count from %d to %d; infrared "
            "safety requires the hard jet structure to survive" % (eps, len(base), len(t)))
    d = dmax(base, t)
    ladder.append((eps, d))
    rec("ir_soft_%.0e" % eps, "change %.3e  ratio to eps %.6f" % (d, d / eps))
    # The jet absorbs the soft particle and nothing else moves, so the change IS eps.
    # Asserting the RATIO rather than a bound is what makes this a scaling law: an
    # implementation whose error merely happened to be small would not track eps.
    if not (0.999 <= d / eps <= 1.001):
        die("a soft particle of %.0e GeV moved the jets by %.3e, a ratio of %.6f; IR "
            "safety means the jet absorbs exactly that momentum" % (eps, d, d / eps))
rec("infrared_scaling", "change/eps = %s across 6 orders of magnitude"
    % ", ".join("%.4f" % (d / e) for e, d in ladder))

# THE CONTROL: a hard particle must change the jets.
hard_extra = cluster(HARD + [(30.0, 30.0, 0.0, 42.5)])
rec("control_hard_particle_jets", "%d (base %d)" % (len(hard_extra), len(base)))
if len(hard_extra) == len(base) and dmax(base, hard_extra) < 1.0:
    die("adding a 42.5 GeV particle barely changed the jets, so the soft-particle result "
        "does not distinguish a safe algorithm from one ignoring its input")

# ------------------- 4. three clustering sequences, one answer on well-separated input
tabs = {}
for name in ("antikt_algorithm", "cambridge_algorithm", "kt_algorithm"):
    algo = getattr(fastjet, name, None)
    if algo is None:
        die("fastjet has no %s" % name)
    tabs[name] = cluster(HARD, algo=algo)
    rec("algo_%s" % name, "%d jets, leading pt %.9f"
        % (len(tabs[name]), tabs[name][0][0]))
ref = tabs["antikt_algorithm"]
for name, t in tabs.items():
    d = dmax(ref, t)
    rec("algo_vs_antikt_%s" % name, "%.3e" % d)
    if d != 0.0:
        die("%s differs from anti-k_t by %.3e on a well-separated event; particles more "
            "than R apart must cluster identically under all three" % (name, d))
rec("algorithm_agreement", "anti-k_t, Cambridge/Aachen and k_t agree exactly")

# ------------------------- 5. a realistic event: 13 TeV QCD from Pythia, nothing staged
try:
    import pythia8
except Exception as exc:
    die("pythia8 is not importable (%s); the realistic-event check needs it" % exc)


def generate(seed):
    py = pythia8.Pythia("", False)
    for s in ("Beams:eCM = 13000.", "HardQCD:all = on", "PhaseSpace:pTHatMin = 100.",
              "Random:setSeed = on", "Random:seed = %d" % seed,
              "Print:quiet = on", "Next:numberShowEvent = 0",
              "Next:numberShowInfo = 0", "Next:numberShowProcess = 0"):
        py.readString(s)
    if not py.init():
        die("pythia8 failed to initialise")
    if not py.next():
        die("pythia8 failed to generate an event")
    return [(p.px(), p.py(), p.pz(), p.e())
            for p in py.event if p.isFinal() and p.isVisible()]


SEED = 20261011
parts = generate(SEED)
rec("pythia_seed", "%d" % SEED)
rec("pythia_particles", "%d" % len(parts))
if len(parts) < 50:
    die("only %d final visible particles; this is meant to be a realistic event" % len(parts))

pj = cluster(parts, min_pt=0.0)
tin = np.array([sum(p[i] for p in parts) for i in range(4)])
tjet = np.array([sum(j[i + 1] for j in pj) for i in range(4)])
if not np.all(np.isfinite(tjet)):
    die("a non-finite jet momentum on the Pythia event")
rel = float(np.abs(tin - tjet).max())
rec("pythia_momentum_conservation", "%.3e" % rel)
# float64 over hundreds of additions of O(1e3) GeV numbers: ~1e-13 is the floor.
if rel > 1e-11:
    die("on %d particles the jets miss the input momentum by %.3e; clustering partitions "
        "the input and must conserve it" % (len(parts), rel))

hard_jets = cluster(parts, min_pt=20.0)
rec("pythia_jets_pt20", "%d" % len(hard_jets))
rec("pythia_leading_pt", "%.4f" % hard_jets[0][0])
if not hard_jets:
    die("no jets above 20 GeV from a pTHat > 100 GeV event")

# the collinear-safety invariance must also hold on the realistic event
lead = max(parts, key=lambda p: p[0] ** 2 + p[1] ** 2)
h = (lead[0] / 2, lead[1] / 2, lead[2] / 2, lead[3] / 2)
rest = [p for p in parts if p is not lead]
d_real = dmax(cluster(parts, min_pt=20.0), cluster([h, h] + rest, min_pt=20.0))
rec("pythia_collinear_split_change", "%.3e" % d_real)
if d_real > 1e-11:
    die("splitting the hardest particle collinearly changed the Pythia jets by %.3e"
        % d_real)

# ------------------------------------------------------------------ 6. determinism
rec("determinism_clustering", "identical" if cluster(HARD) == base else "DIFFERS")
if cluster(HARD) != base:
    die("two clusterings of the same event differed")
again = generate(SEED)
rec("determinism_pythia", "%d particles, identical: %s"
    % (len(again), again == parts))
if again != parts:
    die("Pythia with a fixed seed did not reproduce the event")

with open("score.tsv", "w") as fh:
    fh.write("observable\tvalue\n")
    for k, v in ROWS:
        fh.write("%s\t%s\n" % (k, v))

print("FASTJET OK")
