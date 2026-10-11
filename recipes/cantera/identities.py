#!/usr/bin/env python3
"""Cantera on Graviton4: thermochemistry held to exact identities and detailed balance.

Thermochemistry is unusually rich in things that must be true rather than things that
happen to be close, so almost nothing here is a band:

 * ALGEBRAIC IDENTITIES among independently computed properties -- h = u + pv,
   g = h - Ts, cp - cv = R, p = rho R T / M. Cantera derives each by its own route, so
   these are real constraints, not restatements. All hold to float64 rounding.
 * ELEMENT CONSERVATION through an equilibrium solve. Whatever the chemistry does, atoms
   are conserved. No reference value needed.
 * DETAILED BALANCE. At equilibrium every reversible reaction's forward and reverse rates
   of progress must be equal -- a statement about the converged state, not a fitted number.
 * A PUBLISHED REFERENCE that costs nothing: GRI-Mech 3.0 has exactly 53 species and 325
   reactions, and it ships INSIDE the cantera package, so there is nothing to stage.

Two controls, because conservation claims are the easiest to satisfy vacuously:

 1. The composition must genuinely CHANGE through equilibration. If nothing reacted,
    "elements are conserved" would be true and empty.
 2. Away from equilibrium the net rates must be LARGE. If the rate calculation simply
    returned zero everywhere, detailed balance would pass for the wrong reason.

Run with `python3 -u` so a killed run still has its output in command.log.
"""
import os
import sys

import numpy as np
import cantera as ct

ROWS = []


def rec(k, v):
    print("%s\t%s" % (k, v))
    ROWS.append((k, v))


def die(msg):
    print("FAIL: %s" % msg)
    sys.exit(1)


rec("cantera", getattr(ct, "__version__", "unknown"))
rec("numpy", np.__version__)
rec("python", "%d.%d.%d" % sys.version_info[:3])

# ------------------ 0. the mechanism ships in the package: a pinned, version-matched input
MECH = "gri30.yaml"
data_dir = os.path.join(os.path.dirname(ct.__file__), "data")
mech_path = os.path.join(data_dir, MECH)
rec("mechanism", MECH)
rec("mechanism_in_package", "%s" % os.path.exists(mech_path))
if not os.path.exists(mech_path):
    die("%s is not in the installed package (%s); this recipe reads the mechanism from "
        "cantera itself rather than staging a copy" % (MECH, data_dir))

gas = ct.Solution(MECH)
# GRI-Mech 3.0's size is published: 53 species, 325 reactions. Exact integers.
rec("species", "%d" % gas.n_species)
rec("reactions", "%d" % gas.n_reactions)
if (gas.n_species, gas.n_reactions) != (53, 325):
    die("gri30 loaded with %d species and %d reactions; GRI-Mech 3.0 is published as "
        "53 and 325" % (gas.n_species, gas.n_reactions))
rec("published_mechanism_size", "53 species / 325 reactions, as published")

# ------------------------- 1. algebraic identities at three very different state points
R_UNIV = ct.gas_constant
STATES = [("cold_O2", 300.0, ct.one_atm, "O2:1"),
          ("warm_rich", 1200.0, 5 * ct.one_atm, "CH4:1,O2:2"),
          ("hot_lean", 2500.0, 0.3 * ct.one_atm, "H2:2,O2:1,N2:4")]
worst = {}
g = ct.Solution(MECH)
for label, T, P, X in STATES:
    g.TPX = T, P, X
    h, u, p, v = g.enthalpy_mole, g.int_energy_mole, g.P, g.volume_mole
    s, gibbs = g.entropy_mole, g.gibbs_mole
    cp, cv = g.cp_mole, g.cv_mole
    rho, M = g.density, g.mean_molecular_weight
    checks = {
        "h_eq_u_plus_pv": abs(h - (u + p * v)) / abs(h),
        "g_eq_h_minus_Ts": abs(gibbs - (h - T * s)) / abs(gibbs),
        "cp_minus_cv_eq_R": abs((cp - cv) - R_UNIV) / R_UNIV,
        "ideal_gas_law": abs(p - rho * R_UNIV * T / M) / p,
        "mass_fractions_sum": abs(g.Y.sum() - 1.0),
        "mole_fractions_sum": abs(g.X.sum() - 1.0),
    }
    for k, val in checks.items():
        if not np.isfinite(val):
            die("%s produced a non-finite residual at %s" % (k, label))
        worst[k] = max(worst.get(k, 0.0), val)
    rec("identities_%s" % label, " ".join("%s=%.1e" % (k.split("_")[0], v)
                                          for k, v in checks.items()))
for k, val in sorted(worst.items()):
    rec("worst_%s" % k, "%.3e" % val)
    # These relate quantities cantera computes independently; float64 rounding is the
    # only slack they get. 1e-14 is ~45 eps, so it is a real constraint, not a band.
    if val > 1e-14:
        die("%s is violated by %.3e across the three state points; this is an exact "
            "thermodynamic identity and float64 only explains ~1e-16" % (k, val))
rec("algebraic_identities", "h=u+pv, g=h-Ts, cp-cv=R, p=rhoRT/M all hold to %.1e"
    % max(worst.values()))

# ------------------------------- 2. element conservation through an equilibrium solve
eq = ct.Solution(MECH)
eq.TPX = 1500.0, ct.one_atm, "CH4:1, O2:2, N2:7.52"
els = list(eq.element_names)
before = np.array([eq.elemental_mole_fraction(e) for e in els])
X_before = eq.X.copy()
eq.equilibrate("TP")
after = np.array([eq.elemental_mole_fraction(e) for e in els])
X_after = eq.X.copy()
drift = float(np.abs(after - before).max())
rec("elements", ",".join(els))
rec("element_drift", "%.3e" % drift)
if not np.all(np.isfinite(after)):
    die("a non-finite elemental mole fraction after equilibration")
if drift > 1e-9:
    die("elemental mole fractions moved by %.3e through equilibration; atoms are "
        "conserved and the solver's own tolerance is ~1e-11" % drift)

# THE CONTROL: the chemistry must actually have happened. Conservation across a
# no-op is true and meaningless.
change = float(np.abs(X_after - X_before).max())
rec("control_composition_change", "%.6f" % change)
if change < 0.01:
    die("equilibration moved the composition by only %.3e, so 'elements are conserved' "
        "is vacuous -- nothing reacted" % change)
rec("conservation_elements",
    "conserved to %.1e while the composition moved by %.3f" % (drift, change))

# ------------------------------------------------- 3. detailed balance at equilibrium
db = ct.Solution(MECH)
db.TPX = 1800.0, ct.one_atm, "CH4:1, O2:2, N2:7.52"
db.equilibrate("TP")
fwd, rev, net = (db.forward_rates_of_progress, db.reverse_rates_of_progress,
                 db.net_rates_of_progress)
scale = float(np.abs(fwd).max())
if not np.isfinite(scale) or scale == 0.0:
    die("all forward rates of progress are zero at equilibrium; there is nothing to "
        "balance and the check below would be vacuous")
bal = float(np.abs(fwd - rev).max() / scale)
reversible = np.array([r.reversible for r in db.reactions()])
rec("rate_scale", "%.6e" % scale)
rec("reversible_reactions", "%d of %d" % (int(reversible.sum()), len(reversible)))
rec("detailed_balance", "%.3e" % bal)
if bal > 1e-12:
    die("forward and reverse rates differ by %.3e of the largest rate at equilibrium; "
        "detailed balance must hold for every reversible reaction" % bal)
if (~reversible).any():
    irr = float(np.abs(net[~reversible]).max() / scale)
    rec("irreversible_net_rate", "%.3e" % irr)
    if irr > 1e-15:
        die("an irreversible reaction carries net rate %.3e at equilibrium" % irr)

# THE CONTROL: away from equilibrium the net rates must be large. Otherwise the balance
# above could pass simply because the rate calculation returns zero.
#
# The obvious choice -- an UNBURNED CH4/O2/N2 mixture -- does not work, and the reason is
# chemistry rather than code: it contains no radicals, and essentially every GRI-Mech
# reaction needs one, so every rate is exactly zero. Measured: max|forward| = 0, which
# made this control report 0.000000 and correctly refuse to certify the result above.
# "Unburned" is kinetically frozen, not "far from equilibrium with active chemistry".
#
# So the control holds the EQUILIBRIUM composition (radicals present, rates non-zero) at
# a different temperature. The rate constants shift, the composition no longer satisfies
# them, and the net rates must become large.
ne = ct.Solution(MECH)
ne.TPX = 1800.0, ct.one_atm, "CH4:1, O2:2, N2:7.52"
ne.equilibrate("TP")
ne.TP = 2400.0, ct.one_atm                               # same X, equilibrium for 1800 K
nf = ne.forward_rates_of_progress
nn = ne.net_rates_of_progress
ns = float(np.abs(nf).max())
if not np.isfinite(ns) or ns == 0.0:
    die("the control state has zero forward rates everywhere, so it cannot show that "
        "detailed balance distinguishes equilibrium from anything else")
noneq = float(np.abs(nn).max() / ns)
rec("control_nonequilibrium_net", "%.6f" % noneq)
if noneq < 0.1:
    die("away from equilibrium the net rates are only %.3e of the forward rates, so the "
        "detailed-balance result above does not distinguish equilibrium from anything "
        "else" % noneq)
rec("balance_control", "net/forward is %.3f off-equilibrium and %.1e at equilibrium"
    % (noneq, bal))

# --------------------------------------------- 4. idempotence of the equilibrium solve
X1 = db.X.copy()
db.equilibrate("TP")
idem = float(np.abs(db.X - X1).max())
rec("idempotence", "%.3e" % idem)
if idem > 1e-9:
    die("re-equilibrating an equilibrated mixture moved the composition by %.3e" % idem)

# --------------- 5. constant-enthalpy equilibration, and a published flame temperature
ad = ct.Solution(MECH)
ad.TPX = 300.0, ct.one_atm, "CH4:1, O2:2, N2:7.52"
h0 = ad.enthalpy_mass
ad.equilibrate("HP")
h_drift = abs(ad.enthalpy_mass - h0) / abs(h0)
rec("adiabatic_flame_T_K", "%.2f" % ad.T)
rec("enthalpy_drift_HP", "%.3e" % h_drift)
if h_drift > 1e-8:
    die("an HP equilibration holds enthalpy fixed by construction, yet it moved by %.3e"
        % h_drift)
# The adiabatic flame temperature of stoichiometric methane-air at 1 atm is ~2225 K, a
# textbook value for this mixture and mechanism. A 25 K window is loose enough to survive
# solver noise and tight enough that a wrong thermo database could not pass.
if not (2200.0 < ad.T < 2250.0):
    die("the adiabatic flame temperature is %.2f K; stoichiometric CH4/air at 1 atm is "
        "~2225 K" % ad.T)
rec("published_flame_temperature", "%.2f K against the ~2225 K literature value" % ad.T)

# --------------------------------------------------------------------- 6. determinism
def once():
    s = ct.Solution(MECH)
    s.TPX = 1500.0, ct.one_atm, "CH4:1, O2:2, N2:7.52"
    s.equilibrate("TP")
    return s.X.copy()


a, b = once(), once()
rec("determinism", "byte-identical" if np.array_equal(a, b) else "DIFFERS")
if not np.array_equal(a, b):
    die("two identical equilibrations gave different compositions")

with open("score.tsv", "w") as fh:
    fh.write("observable\tvalue\n")
    for k, v in ROWS:
        fh.write("%s\t%s\n" % (k, v))

print("CANTERA OK")
