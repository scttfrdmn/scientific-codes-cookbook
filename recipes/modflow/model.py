import math, os, re, sys
import numpy as np
import flopy

# ---------------------------------------------------------------------------------------------
# MODFLOW 6 ships no example problems in the conda package (probed: only bin/mf6, libmf6.so,
# get-modflow). So the model is BUILT here with flopy -- which is the better position anyway,
# because a problem built on purpose can have a known closed-form answer, and then the check is
# a reference reproduction rather than a band on whatever the solver printed.
#
# Both cases below are 1-D steady confined flow, where MODFLOW's conductance formulation on a
# uniform grid reduces to   T * d2h/dx2 = -R(x)   with T = K * thickness.
# ---------------------------------------------------------------------------------------------

K, THICK, DELC = 10.0, 20.0, 1.0     # m/d, m, m  -> transmissivity T = 200 m^2/d
# THICK is 20 m so the sinusoidal case in case 2 perturbs the head by ~0.5 m rather than
# ~10 m. The solve is confined (icelltype 0), so transmissivity is head-independent and a
# larger perturbation would still be numerically valid -- it would just describe an
# aquifer pressurised far above its own top, which is a silly thing to put on a page.
# The problem is linear in the recharge rate, so the measured ORDER is unaffected.
T = K * THICK
HL, HR = 10.0, 2.0                   # fixed heads, m
XLEN = 1000.0                        # distance between the two fixed-head cell CENTRES, m

out = {}
def rec(k, v):
    out[k] = v
    print("%s\t%s" % (k, v))


def build(ncol, rch=None, ws="m"):
    """1-D confined model. CHD in the first and last cell; optional per-cell recharge."""
    # dx chosen so the first and last cell CENTRES are exactly XLEN apart, which is what the
    # analytical solution below is written against.
    dx = XLEN / (ncol - 1)
    sim = flopy.mf6.MFSimulation(sim_name="m", sim_ws=ws, exe_name="mf6", version="mf6")
    flopy.mf6.ModflowTdis(sim, nper=1, perioddata=[(1.0, 1, 1.0)])
    # Tolerances well below the 1e-8 the checks assert, so solver error cannot be mistaken for
    # discretisation error -- the same separation the PETSc ladder needs.
    flopy.mf6.ModflowIms(sim, outer_dvclose=1e-11, inner_dvclose=1e-12,
                         linear_acceleration="BICGSTAB", inner_maximum=500)
    gwf = flopy.mf6.ModflowGwf(sim, modelname="m", save_flows=True)
    flopy.mf6.ModflowGwfdis(gwf, nlay=1, nrow=1, ncol=ncol, delr=dx, delc=DELC,
                            top=THICK, botm=0.0)
    flopy.mf6.ModflowGwfic(gwf, strt=0.5 * (HL + HR))
    flopy.mf6.ModflowGwfnpf(gwf, icelltype=0, k=K)      # icelltype 0 = confined
    flopy.mf6.ModflowGwfchd(gwf, stress_period_data=[[(0, 0, 0), HL],
                                                     [(0, 0, ncol - 1), HR]])
    if rch is not None:
        # recharge is a RATE per unit area; MODFLOW multiplies by the cell area itself
        spd = [[(0, 0, j), float(rch(j, dx))] for j in range(1, ncol - 1)]
        flopy.mf6.ModflowGwfrch(gwf, stress_period_data={0: spd})
    flopy.mf6.ModflowGwfoc(gwf, head_filerecord="m.hds", budget_filerecord="m.cbc",
                           saverecord=[("HEAD", "ALL"), ("BUDGET", "ALL")])
    sim.write_simulation(silent=True)
    ok, buff = sim.run_simulation(silent=True)
    if not ok:
        print("\n".join(buff[-25:]))
        sys.exit("FAIL: mf6 did not run to completion for ncol=%d" % ncol)
    head = flopy.utils.HeadFile(os.path.join(ws, "m.hds")).get_data().flatten()
    lst = open(os.path.join(ws, "m.lst")).read()
    return dx, head, lst, "\n".join(buff)


def budget_block(lst):
    """mf6's own volumetric budget as PRINTED: TOTAL IN/OUT, IN-OUT, PERCENT DISCREPANCY.

    Reported, and used only for coarse checks: mf6 renders these to a few decimals, so an
    error below roughly 1e-4 is invisible here. The exact identity uses the binary budget.
    """
    g = lambda pat: [float(m) for m in re.findall(pat + r"\s*=\s*([-\dEe.+]+)", lst)]
    return g(r"TOTAL IN"), g(r"TOTAL OUT"), g(r"IN - OUT"), g(r"PERCENT DISCREPANCY")


def net_source_sink(ws):
    """Sum every boundary flow in the BINARY budget file, at full precision.

    Water in must equal water out, so the signed sum over all source/sink packages (CHD, RCH)
    is zero up to roundoff. FLOW-JA-FACE is internal cell-to-cell flow and cancels on its own,
    so it is summed separately and reported rather than mixed in.
    """
    cbc = flopy.utils.CellBudgetFile(os.path.join(ws, "m.cbc"))
    names = [n.decode().strip() if isinstance(n, bytes) else str(n).strip()
             for n in cbc.get_unique_record_names()]
    bnd, internal, gross = 0.0, 0.0, 0.0
    for nm in names:
        for arr in cbc.get_data(text=nm):
            a = np.asarray(arr)
            q = a["q"] if (a.dtype.names and "q" in a.dtype.names) else a.astype(float).ravel()
            q = np.asarray(q, dtype=float)
            if nm.upper().replace("-", "").replace(" ", "") == "FLOWJAFACE":
                internal += float(q.sum())
            else:
                bnd += float(q.sum())
                gross += float(np.abs(q).sum())
    return names, bnd, internal, gross


# ============================================================= CASE 1: the closed-form answer
# With no sources and constant T, the governing equation is d2h/dx2 = 0, so the head is LINEAR
# between the two fixed-head cells. The three-point finite-difference stencil reproduces a
# linear function exactly, so this is not an approximation to within a tolerance -- MODFLOW
# must return the analytical line to solver precision.
NCOL = 101
dx, head, lst, buff = build(NCOL, ws="case1")
x = np.arange(NCOL) * dx
analytic = HL + (HR - HL) * x / XLEN
err = float(np.max(np.abs(head - analytic)))
rec("case1_cells", NCOL)
rec("case1_dx_m", "%.4f" % dx)
rec("case1_head_first_last", "%.6f / %.6f" % (head[0], head[-1]))
rec("case1_max_abs_error_vs_analytic", "%.3e" % err)
if err > 1e-8:
    for i in (0, 1, NCOL // 2, NCOL - 2, NCOL - 1):
        print("   x=%8.2f  mf6=%.10f  analytic=%.10f" % (x[i], head[i], analytic[i]))
    sys.exit("FAIL: heads deviate from the analytical linear profile by %.3e" % err)
rec("case1_identity", "heads reproduce the analytical linear profile exactly")

# The flux is then also known in closed form: Q = T * (HL - HR) / XLEN per unit width.
q_analytic = T * (HL - HR) / XLEN * DELC
tin, tout, inout, pct = budget_block(lst)
rec("case1_mf6_total_in", "%.6f" % tin[-1])
rec("case1_mf6_total_out", "%.6f" % tout[-1])
rec("case1_analytic_flux", "%.6f" % q_analytic)
qerr = abs(tin[-1] - q_analytic) / q_analytic
rec("case1_flux_relative_error", "%.3e" % qerr)
if qerr > 1e-9:
    sys.exit("FAIL: mf6's through-flow is %.3e off the analytical Darcy flux" % qerr)
rec("case1_flux_identity", "through-flow equals the analytical Darcy flux")

# ================================================ CASE 1b: mf6's own conservation bookkeeping
# A groundwater solve must conserve water, and mf6 computes and reports the discrepancy itself.
# This is its own completion-grade check: inflow must equal outflow, exactly, not within a band.
rec("case1_mf6_in_minus_out_printed", "%.3e" % inout[-1])
rec("case1_mf6_percent_discrepancy_printed", "%.3e" % pct[-1])
if abs(pct[-1]) > 1e-6:
    sys.exit("FAIL: mf6 reports a %.3e%% budget discrepancy" % pct[-1])

names, bnd, internal, gross = net_source_sink("case1")
rec("case1_budget_records", ",".join(names))
rec("case1_net_boundary_flow", "%.3e" % bnd)
rec("case1_gross_boundary_flow", "%.6f" % gross)
rec("case1_internal_flow_sum", "%.3e" % internal)
if gross <= 0:
    sys.exit("FAIL: no boundary flow in the budget file -- the check would be vacuous")
rel = abs(bnd) / gross
rec("case1_conservation_relative", "%.3e" % rel)
if rel > 1e-10:
    sys.exit("FAIL: boundary flows net to %.3e of %.6f gross -- water is not conserved" % (bnd, gross))
rec("case1_budget_identity",
    "inflow == outflow to %.3e of gross, from the binary budget at full precision" % rel)

# COMPLETION SENTINELS. mf6 must be shown to have finished, not merely to have left a parseable
# head file behind. Three independent signals, all of them measured present rather than guessed:
#
#   1. flopy's own success flag, which is mf6's verdict relayed -- build() exits on `not ok`.
#   2. "TOTAL SIMULATION TIME" in the listing, written in the closing timing block, so a solve
#      killed part-way never reaches it.
#   3. "PERCENT DISCREPANCY" in the listing, written only once the budget is closed out.
#
# An earlier version asserted the phrase "Normal termination of simulation" in the listing.
# It is not there in 6.8.1 -- and flopy returns an EMPTY stdout buffer under silent=True, so it
# was not reachable that way either. Asserting a string the tool never emits fails a correct
# run, which is the same class of mistake as a check that passes for the wrong reason.
MARKERS = ("TOTAL SIMULATION TIME", "PERCENT DISCREPANCY")


def terminated_normally(lst):
    return all(m in lst for m in MARKERS)

print("  mf6 stdout lines captured by flopy: %d" % len(buff.splitlines()))
print("  listing tail: %s" % " | ".join(
    [l.strip() for l in lst.strip().splitlines() if l.strip()][-3:]))
missing = [m for m in MARKERS if m not in lst]
if missing:
    sys.exit("FAIL: mf6's listing lacks %s -- the solve did not finish" % ", ".join(missing))
rec("case1_completion_sentinel", "listing carries %s" % " + ".join(MARKERS))

# ======================================================= CASE 2: a measured convergence order
# A linear profile is reproduced exactly, which is a strong correctness check but tests nothing
# about the DISCRETISATION. So case 2 drives the same solver with sinusoidal recharge, whose
# closed-form solution is not a polynomial:
#
#     T d2h/dx2 = -R0 sin(pi x / L)   ->   h = H0 + (R0 L^2)/(T pi^2) sin(pi x / L)
#
# with h = H0 at both fixed-head cells. The error must fall as O(h^2), and asserting the ORDER
# rather than an error magnitude is what catches a correct-but-first-order scheme.
R0 = 1.0e-3                       # m/d
AMP = R0 * XLEN ** 2 / (T * math.pi ** 2)
rows = []
for ncol in (26, 51, 101, 201):
    dxi, h, lsti, buffi = build(ncol, rch=lambda j, d: R0 * math.sin(math.pi * (j * d) / XLEN),
                                ws="case2_%d" % ncol)
    xi = np.arange(ncol) * dxi
    # Linear part must satisfy the same two fixed heads, so it is HL..HR as before.
    exact = HL + (HR - HL) * xi / XLEN + AMP * np.sin(math.pi * xi / XLEN)
    e = float(np.max(np.abs(h - exact)))
    _, _, _, p = budget_block(lsti)
    _, b_i, _, g_i = net_source_sink("case2_%d" % ncol)
    if g_i <= 0 or abs(b_i) / g_i > 1e-10:
        sys.exit("FAIL: ncol=%d does not conserve water (net %.3e of %.6f gross)" % (ncol, b_i, g_i))
    if not terminated_normally(lsti):
        sys.exit("FAIL: ncol=%d did not finish (listing lacks its closing markers)" % ncol)
    rows.append((ncol, dxi, e))
    print("  ncol %4d  dx %8.3f  max|err| %.6e  net/gross %.1e" % (ncol, dxi, e, abs(b_i)/g_i))

orders = []
for (n1, h1, e1), (n2, h2, e2) in zip(rows, rows[1:]):
    pord = math.log(e1 / e2) / math.log(h1 / h2)
    orders.append(pord)
    print("  h %8.3f -> %8.3f   order %.3f" % (h1, h2, pord))
rec("case2_amplitude_m", "%.6f" % AMP)
rec("case2_errors", ", ".join("%.3e" % e for _, _, e in rows))
rec("case2_measured_orders", ", ".join("%.3f" % o for o in orders))
if min(orders) < 1.8 or max(orders) > 2.2:
    sys.exit("FAIL: measured orders %s are not second order" % orders)
rec("case2_identity", "error falls as O(h^2) on a non-polynomial solution")
rec("case2_conservation", "every rung conserves water and terminates normally")

with open("score.tsv", "w") as fh:
    fh.write("observable\tvalue\n")
    for k, v in out.items():
        fh.write("%s\t%s\n" % (k, v))
print("MODFLOW OK")
