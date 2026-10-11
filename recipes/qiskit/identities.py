#!/usr/bin/env python3
"""Qiskit on Graviton, against closed forms, an independent library, and its own algebra.

Almost nothing here needs a tolerance, which is why Qiskit is a good subject: a Bell amplitude
IS 1/sqrt(2), a unitary DOES satisfy U^dag U = I, and the QFT IS the DFT matrix. Every bound
below is either an exact equality, a machine-precision bound on an exact identity, or a
sampling error the sampler itself defines.

The cross-library check is the one that is not undermined by a shared backend. qutip has its own
gate definitions and its own tensor conventions, so comparing it with qiskit-aer is two
implementations -- unlike comparing two Aer methods, or (elsewhere in this catalog) terra against
rasterio when both call GDAL.

ORDERING IS THE TRAP, AND IT IS MEASURED NOT ASSUMED. Qiskit is little-endian: its qubit 0 is the
RIGHTMOST factor in a qutip tensor product. Probed by putting X on qubit 0 alone and seeing which
qutip construction matched. The un-reversed comparison is kept below as the negative control --
it is 7.071e-01 away, so the agreement is a statement about conventions being handled rather than
something that would pass regardless.

Run with `python3 -u`.
"""
import warnings

import numpy as np

out = {}


def rec(k, v):
    out[k] = v
    print("%s\t%s" % (k, v))


def dump():
    with open("/tmp/score.tsv", "w") as fh:
        fh.write("observable\tvalue\n")
        for k, v in out.items():
            fh.write("%s\t%s\n" % (k, v))


def die(msg):
    dump()
    raise SystemExit("FAIL: %s" % msg)


import qiskit
import qiskit_aer
import qutip
from qiskit import QuantumCircuit, transpile
from qiskit.quantum_info import Clifford, Operator, Statevector
from qiskit_aer import AerSimulator

rec("qiskit", qiskit.__version__)
rec("qiskit_aer", qiskit_aer.__version__)
rec("qutip", qutip.__version__)
rec("numpy", np.__version__)
rec("aer_methods", " ".join(sorted(AerSimulator().available_methods())))

EPS = np.finfo(np.float64).eps
SEED = 20261011

# ---- 1. a Bell amplitude IS 1/sqrt(2) -------------------------------------------------------
bell = QuantumCircuit(2)
bell.h(0)
bell.cx(0, 1)
amps = np.asarray(Statevector(bell).data)
r2 = 1.0 / np.sqrt(2.0)
rec("bell_amplitudes", np.array2string(amps.real, precision=17))
exact = (amps[0].real == r2) and (amps[3].real == r2)
rec("bell_bit_identical_to_1_over_sqrt2", exact)
rec("bell_zero_amplitudes", [complex(amps[1]), complex(amps[2])])
if not exact:
    die("Bell amplitudes are not bit-identical to the float64 nearest 1/sqrt(2)")
if amps[1] != 0 or amps[2] != 0:
    die("the |01> and |10> amplitudes are not exactly zero")
nrm = abs(float(np.vdot(amps, amps).real) - 1.0)
rec("bell_norm_error", "%.3e" % nrm)
if nrm > 4 * EPS:
    die("norm error %.3e exceeds 4 eps" % nrm)
rec("identity_bell", "amplitudes bit-identical to 1/sqrt(2), off-diagonals exactly 0")

# ---- 2. U^dag U = I ------------------------------------------------------------------------
qc = QuantumCircuit(3)
qc.h(0); qc.cx(0, 1); qc.t(2); qc.ry(0.7, 1); qc.cz(1, 2); qc.swap(0, 2)
U = np.asarray(Operator(qc).data)
uerr = float(np.abs(U.conj().T @ U - np.eye(8)).max())
rec("unitarity_error", "%.3e" % uerr)
if uerr > 1e-14:
    die("U^dag U - I is %.3e" % uerr)
rec("identity_unitary", "U^dag U = I to %.1e on an 8x8 Clifford+T+rotation circuit" % uerr)

# ---- 3. two representations of the same Clifford -------------------------------------------
# A symplectic tableau and a dense complex matrix, built by different code. A tableau fixes the
# operator only up to global phase, so the phase is divided out before comparing -- and the
# measured phase is reported, because "we removed a phase" is only honest if you say which.
cl = QuantumCircuit(4)
cl.h(0); cl.cx(0, 1); cl.cx(1, 2); cl.s(3); cl.h(3); cl.cz(2, 3)
A = np.asarray(Operator(cl).data)
B = np.asarray(Clifford(cl).to_operator().data)
idx = np.unravel_index(np.argmax(np.abs(A)), A.shape)
ph = complex(B[idx] / A[idx])
rec("clifford_global_phase", "%.6f%+.6fj" % (ph.real, ph.imag))
raw = float(np.abs(A - B).max())
dephased = float(np.abs(A * ph - B).max())
rec("clifford_raw_max_diff", "%.3e" % raw)
rec("clifford_dephased_max_diff", "%.3e" % dephased)
if dephased != 0.0:
    die("tableau and dense unitary differ by %.3e after dephasing" % dephased)
rec("identity_clifford_representations",
    "symplectic tableau and dense unitary agree exactly (0.0) up to a global phase of %.1f"
    % ph.real)

# ---- 4. the QFT IS the DFT matrix, and the wrong conventions are not close -----------------
# Measured: do_swaps=True with the +2pi i j k / N sign. The other three combinations are O(1)
# away, so this check cannot pass under a convention mix-up -- the discrimination is intrinsic.
from qiskit.synthesis import synth_qft_full
with warnings.catch_warnings():
    warnings.simplefilter("ignore", DeprecationWarning)
    from qiskit.circuit.library import QFT
worst_right, best_wrong = 0.0, 1e9
for n in (2, 3, 4):
    N = 2 ** n
    j, k = np.meshgrid(np.arange(N), np.arange(N), indexing="ij")
    F = np.exp(2j * np.pi * j * k / N) / np.sqrt(N)
    for label, circ in (("QFT(deprecated)", QFT(n, do_swaps=True)),
                        ("synth_qft_full", synth_qft_full(n, do_swaps=True))):
        d = float(np.abs(np.asarray(Operator(circ).data) - F).max())
        worst_right = max(worst_right, d)
        rec("qft_n%d_%s_vs_DFT" % (n, label), "%.3e" % d)
        if d > 1e-13:
            die("%s at n=%d differs from the DFT matrix by %.3e" % (label, n, d))
    # the three wrong conventions, as a built-in control
    for label, M, circ in (("minus_sign", np.conj(F), QFT(n, do_swaps=True)),
                           ("no_swaps_plus", F, QFT(n, do_swaps=False)),
                           ("no_swaps_minus", np.conj(F), QFT(n, do_swaps=False))):
        if n == 2:
            d = float(np.abs(np.asarray(Operator(circ).data) - M).max())
            best_wrong = min(best_wrong, d)
            rec("qft_control_%s" % label, "%.3e" % d)
rec("qft_worst_correct", "%.3e" % worst_right)
rec("qft_closest_wrong_convention", "%.3e" % best_wrong)
if best_wrong < 0.1:
    die("a wrong convention is only %.3e away -- the check cannot discriminate" % best_wrong)
rec("identity_qft",
    "QFT == DFT matrix to %.1e at n=2,3,4 for both the deprecated and replacement APIs, "
    "while the nearest wrong convention is %.3f away" % (worst_right, best_wrong))
rec("observation_qft_deprecated",
    "qiskit.circuit.library.QFT is deprecated as of Qiskit 2.1 and removed in 3.0; "
    "synth_qft_full gives the same matrix, verified above")

# ---- 5. <Z> = cos(theta) -------------------------------------------------------------------
Z = Operator.from_label("Z")
worst = 0.0
for th in (0.0, 0.3, 1.1, np.pi / 2, 2.4, np.pi):
    q = QuantumCircuit(1)
    q.ry(th, 0)
    got = float(Statevector(q).expectation_value(Z).real)
    worst = max(worst, abs(got - np.cos(th)))
rec("expZ_max_error_vs_cos", "%.3e" % worst)
if worst > 1e-14:
    die("<Z> departs from cos(theta) by %.3e" % worst)
rec("identity_expectation", "<Z> = cos(theta) to %.1e over 6 angles" % worst)

# ---- 6. Grover on 2 qubits: one iteration is exact ----------------------------------------
# Amplitude amplification lands exactly on the marked state here, so the only departure from 1
# is float accumulation. Measured 8.882e-16 (4 eps) -- so the assertion is a machine-precision
# bound, NOT `== 1`, which would be wrong.
worst_g = 0.0
for marked in range(4):
    bits = format(marked, "02b")
    g = QuantumCircuit(2)
    g.h([0, 1])
    if bits[1] == "0":
        g.x(0)
    if bits[0] == "0":
        g.x(1)
    g.cz(0, 1)
    if bits[1] == "0":
        g.x(0)
    if bits[0] == "0":
        g.x(1)
    g.h([0, 1]); g.x([0, 1]); g.cz(0, 1); g.x([0, 1]); g.h([0, 1])
    p = Statevector(g).probabilities_dict()
    best = max(p, key=p.get)
    if str(best) != bits:
        die("Grover marked %s but peaked at %s" % (bits, best))
    worst_g = max(worst_g, 1.0 - p[best])
rec("grover_worst_1_minus_P", "%.3e" % worst_g)
if worst_g > 1e-14:
    die("Grover leaves %.3e probability off the marked state" % worst_g)
rec("identity_grover",
    "all 4 marked states recovered with 1-P = %.3e (%.0f eps)" % (worst_g, worst_g / EPS))

# ---- 7. the cross-library check, and the control that gives it meaning --------------------
QG = {"h": qutip.Qobj(np.array([[1, 1], [1, -1]]) / np.sqrt(2)),
      "x": qutip.sigmax(), "t": qutip.Qobj(np.array([[1, 0], [0, np.exp(1j * np.pi / 4)]]))}


def one(n, gate, q, reverse):
    pos = (n - 1 - q) if reverse else q
    return qutip.tensor(*[QG[gate] if i == pos else qutip.qeye(2) for i in range(n)])


def cx(n, c, t, reverse):
    cpos = (n - 1 - c) if reverse else c
    tpos = (n - 1 - t) if reverse else t
    dim = 2 ** n
    M = np.zeros((dim, dim))
    for s in range(dim):
        b = [(s >> (n - 1 - i)) & 1 for i in range(n)]
        if b[cpos]:
            b[tpos] ^= 1
        M[sum(v << (n - 1 - i) for i, v in enumerate(b)), s] = 1
    return qutip.Qobj(M, dims=[[2] * n, [2] * n])


n = 3
qq = QuantumCircuit(n)
qq.h(0); qq.cx(0, 1); qq.t(2); qq.x(1)
qs = np.asarray(Statevector(qq).data)
diffs = {}
for reverse in (True, False):
    psi = qutip.tensor(*[qutip.basis(2, 0) for _ in range(n)])
    psi = one(n, "h", 0, reverse) * psi
    psi = cx(n, 0, 1, reverse) * psi
    psi = one(n, "t", 2, reverse) * psi
    psi = one(n, "x", 1, reverse) * psi
    diffs[reverse] = float(np.abs(qs - np.asarray(psi.full()).ravel()).max())
rec("qutip_vs_qiskit_reversed", "%.3e" % diffs[True])
rec("qutip_vs_qiskit_unreversed_control", "%.3e" % diffs[False])
if diffs[True] != 0.0:
    die("qutip and qiskit differ by %.3e with the ordering fix applied" % diffs[True])
if diffs[False] < 0.1:
    die("the un-reversed control is only %.3e away -- ordering is not being tested"
        % diffs[False])
rec("identity_cross_library",
    "qutip and qiskit agree BIT-IDENTICALLY (0.0) on a 3-qubit state; ignoring Qiskit's "
    "little-endian order puts them %.3f apart" % diffs[False])

# ---- 8. sampling: support exactly, frequencies within the sampler's own error -------------
clm = cl.copy()
clm.measure_all()
exact_p = {str(k): float(v) for k, v in Statevector(cl).probabilities_dict().items()
           if v > 1e-12}
rec("exact_support", sorted(exact_p))
SHOTS = 8192
counts = {}
for method in ("statevector", "stabilizer"):
    sim = AerSimulator(method=method, seed_simulator=SEED)
    counts[method] = sim.run(transpile(clm, sim), shots=SHOTS).result().get_counts()
    rec("support_%s" % method, sorted(counts[method]))
    if set(counts[method]) != set(exact_p):
        die("%s support %s != exact %s" % (method, sorted(counts[method]), sorted(exact_p)))
rec("identity_support",
    "stabilizer and statevector both reproduce the exact support %s" % sorted(exact_p))

# A sampled frequency is expected within its binomial standard error, not at the exact value.
# The bound is 5 sigma, taken from the distribution rather than chosen to fit.
worst_sigma = 0.0
for method, c in counts.items():
    for k, nk in c.items():
        p = exact_p[str(k)]
        se = (p * (1 - p) / SHOTS) ** 0.5
        worst_sigma = max(worst_sigma, abs(nk / SHOTS - p) / se)
rec("worst_deviation_in_sigma", "%.3f" % worst_sigma)
if worst_sigma > 5.0:
    die("a sampled frequency is %.2f sigma from exact" % worst_sigma)
rec("identity_sampling",
    "every sampled frequency is within %.2f binomial sigma of the exact probability"
    % worst_sigma)

# A seeded run must be reproducible, or none of the sampled numbers above means anything.
sim = AerSimulator(method="statevector", seed_simulator=SEED)
again = sim.run(transpile(clm, sim), shots=SHOTS).result().get_counts()
same = dict(again) == dict(counts["statevector"])
rec("same_seed_identical_counts", same)
if not same:
    die("two runs at seed %d produced different counts" % SEED)
rec("identity_seed", "two runs at seed %d give identical counts" % SEED)

dump()
print("QISKIT OK")
