#!/usr/bin/env python3
"""Qiskit over the whole QASMBench suite: compile every circuit to a heavy-hex device, prove the
compiled circuit is the same computation wherever a simulator can hold it, and simulate the
originals at the box's limit.

    real.py <qasmbench dir> <out dir> <workers>

Every circuit (the 132 originals of pnnl/QASMBench @357b942, 2 to 10,000 qubits) is
  1. parsed with qiskit.qasm2 (LEGACY_CUSTOM_INSTRUCTIONS, as QASMBench's own interface does);
  2. transpiled at optimization_level=2 (transpile()'s default) to the smallest IBM heavy-hex
     coupling map that holds it, basis {cz, rz, sx, x}, seed_transpiler fixed;
  3. checked STRUCTURALLY: every gate in the basis, every two-qubit gate on a coupling edge.
Then each unitary circuit's compiled form C (restricted to the physical qubits it touches) is
checked against its original E placed on the qubits the final layout says it ends on:
  - dense (compiled width <= SEM_MAX): Aer statevector of C against E simulated by
    qiskit.quantum_info where cheap (a second implementation) and Aer otherwise; 1-|<E|C>|^2.
  - mps (wider): Aer matrix_product_state runs C followed by E^-1, and P(|0...0>) is the
    fidelity |<E|C>|^2 -- an inverse test, valid at any width the MPS can hold within its time.
The originals are also simulated directly:
  - dense Aer statevector up to DENSE_MAX qubits: norm; a closed form where one exists
    (classical-reversible x/cx/ccx/swap/cswap: the integer bit-simulated basis state has P = 1;
    Clifford: support and its 2^-k probabilities equal terra's StabilizerState; QFT on |0>: every
    amplitude 2^(-n/2)); and quantum_info's Statevector against Aer where cheap.
  - full width, any size: Aer MPS on every classical-reversible original returns only the
    bit-simulated outcome; Aer stabilizer and MPS samples of every Clifford original all lie in
    terra's StabilizerState support at its single probability 2^-k.
Every job runs in its own process under a wall-clock limit, so one intractable circuit is a
reported timeout, never a hung run. Run with `python3 -u`.
"""
import glob
import hashlib
import json
import math
import multiprocessing as mp
import os
import resource
import sys
import time
import traceback

import numpy as np

SEED = 20261019
EPS = float(np.finfo(np.float64).eps)
BASIS = ["cz", "rz", "sx", "x"]
OK_OPS = set(BASIS) | {"measure", "barrier", "reset", "delay"}
# Compiled-vs-original fidelity. EXACT is machine precision, which grows with the work done: the
# first Graviton4 run put every circuit not touched by the tolerance below at <= 6.4 eps per gate
# (compiled + original gates, over 100 circuits, dense and MPS alike), so EXACT allows 16. CEIL is
# the level-2 bound: Qiskit's
# two-qubit synthesis accepts a Weyl specialisation at trace fidelity >= 1 - 1e-9 PER BLOCK even at
# approximation_degree=1.0 (measured: CU1(theta <= 4.8e-5) -> 0 CZ, operator error theta/4), so a
# circuit with many near-identity blocks (the QFT family) is approximate by design. Every circuit
# above EXACT is recompiled at level 1, which has no such pass, and must then be exact: that pins the
# deviation on the documented tolerance rather than on layout, routing or translation. CEIL only has
# to separate that from a miscompile, which is O(1) away.
EXACT_PER_GATE = 16


def exact_bound(gates):
    return EXACT_PER_GATE * float(np.finfo(np.float64).eps) * gates


SEM_CEIL = 1e-6
SEM_IN_POOL = 20        # dense checks up to this width run inside the pool (state <= 16 MiB)
SEM_MAX = int(os.environ.get("QR_SEM_MAX", 28))           # dense checks up to this width run serially, all threads (4 GiB state)
DENSE_MAX = int(os.environ.get("QR_DENSE_MAX", 30))         # dense originals up to this width (16 GiB state)
DENSE_BUDGET = 2e13     # serial dense jobs only when gates * 2^width <= this (~30 min of Aer)
QI_BUDGET = 2e9         # quantum_info Statevector used when gates * 2^n <= this
SHOTS = 256
T_COMPILE = int(os.environ.get("QR_T_COMPILE", 5400))
T_SERIAL = int(os.environ.get("QR_T_SERIAL", 1800))
T_MPS = int(os.environ.get("QR_T_MPS", 600))
QPY = "/var/tmp/qpy"
CLASSICAL = {"x", "cx", "ccx", "swap", "cswap", "id", "barrier", "c3x", "c4x", "mcx"}
CLIFFORD = {"h", "s", "sdg", "x", "y", "z", "cx", "cz", "cy", "swap", "id", "barrier", "sx",
            "sxdg", "ecr", "iswap", "dcx"}
NONUNITARY = {"measure", "reset", "if_else", "while_loop", "for_loop", "switch_case"}


def log(*a):
    print(*a, flush=True)


def heavy_hex(n):
    from qiskit.transpiler import CouplingMap
    d = 3
    while (5 * d * d - 2 * d - 1) // 2 < n:
        d += 2
    return d, CouplingMap.from_heavy_hex(d)


def load(path, root):
    from qiskit import qasm2
    return qasm2.load(path, include_path=[root],
                      custom_instructions=qasm2.LEGACY_CUSTOM_INSTRUCTIONS, strict=False)


def qubits_only(qc):
    """The same operations on a circuit with no classical bits (final measurements already gone)."""
    from qiskit import QuantumCircuit
    c = QuantumCircuit(qc.num_qubits, global_phase=qc.global_phase)
    for inst in qc.data:
        if inst.clbits:
            return qc
        c.append(inst.operation, [qc.find_bit(q).index for q in inst.qubits])
    return c


def expand(qc):
    """Inline every gate the QASM file itself defines (`gate majority a,b,c {...}`), by its own
    definition: exact, and not the transpiler. The reference side then holds only library gates,
    so Aer cannot dispatch a file-defined gate by a name it happens to share."""
    for _ in range(20):
        defined = {i.operation.name for i in qc.data if type(i.operation).__name__ == "_DefinedGate"}
        if not defined:
            return qc
        qc = qc.decompose(gates_to_decompose=sorted(defined))
    raise RuntimeError("file-defined gates nest deeper than 20")


def reference(qc):
    """The original's unitary part (final measurements removed) with its file-defined gates inlined."""
    return expand(qubits_only(qc.remove_final_measurements(inplace=False)))


AER_OPS = {}


def aer_ready(qc, method):
    """Decompose anything this Aer method does not implement (e.g. the inverse of a gate)."""
    if method not in AER_OPS:
        from qiskit_aer import AerSimulator
        AER_OPS[method] = set(AerSimulator(method=method).target.operation_names) | {"barrier"}
    ok = AER_OPS[method]
    for _ in range(20):
        bad = {i.operation.name for i in qc.data if i.operation.name not in ok}
        if not bad:
            return qc
        qc = qc.decompose(gates_to_decompose=sorted(bad))
    raise RuntimeError("cannot lower %s for aer %s" % (sorted(bad), method))


def structure(tqc, edges):
    """(gates outside the basis, gates off the coupling map), descending into control flow."""
    bad_gate = bad_edge = 0
    for inst in tqc.data:
        q = [tqc.find_bit(b).index for b in inst.qubits]
        o = inst.operation
        if getattr(o, "blocks", None):
            for blk in o.blocks:
                # a block's qubits are the instruction's, in order
                outer = {bq: q[i] for i, bq in enumerate(blk.qubits)}
                for bi in blk.data:
                    bq = [outer[x] for x in bi.qubits]
                    if bi.operation.name not in OK_OPS:
                        bad_gate += 1
                    elif bi.operation.name not in ("barrier", "measure") and (
                            len(bq) > 2 or (len(bq) == 2 and tuple(bq) not in edges)):
                        bad_edge += 1
            continue
        if o.name not in OK_OPS:
            bad_gate += 1
        elif o.name not in ("barrier", "measure") and (len(q) > 2 or (len(q) == 2 and tuple(q) not in edges)):
            bad_edge += 1
    return bad_gate, bad_edge


def circuit_sha(tqc):
    from qiskit import qasm3
    return hashlib.sha256(qasm3.dumps(tqc).encode()).hexdigest()


def gate_shas(tqc):
    """Two hashes that separate a different compiled circuit from the same one with different last
    bits: the gate sequence (names and qubits, no angles), and the angles rounded to 1e-9."""
    seq, ang = hashlib.sha256(), hashlib.sha256()

    def walk(c, outer):
        for inst in c.data:
            q = [outer[c.find_bit(b).index] for b in inst.qubits]
            seq.update(("%s %s;" % (inst.operation.name, q)).encode())
            for p in inst.operation.params:
                if isinstance(p, (int, float)):
                    ang.update(("%.9f;" % float(p)).encode())
            for blk in getattr(inst.operation, "blocks", None) or ():
                walk(blk, [q[i] for i in range(len(blk.qubits))])

    walk(tqc, list(range(tqc.num_qubits)))
    return seq.hexdigest(), ang.hexdigest()


def opnames(qc):
    return {inst.operation.name for inst in qc.data}


def bitsim(qc):
    """Integer bit-simulation of a classical-reversible circuit on |0...0>; the basis index
    (qiskit little-endian: qubit i is bit i)."""
    s = 0
    idx = {q: i for i, q in enumerate(qc.qubits)}
    for inst in qc.data:
        nm = inst.operation.name
        q = [idx[b] for b in inst.qubits]
        if nm in ("barrier", "id"):
            continue
        if nm == "x":
            s ^= 1 << q[0]
        elif nm in ("cx", "ccx", "c3x", "c4x", "mcx"):
            if all((s >> c) & 1 for c in q[:-1]):
                s ^= 1 << q[-1]
        elif nm in ("swap", "cswap"):
            if nm == "cswap" and not (s >> q[0]) & 1:
                continue
            a, b = q[-2], q[-1]
            if ((s >> a) & 1) != ((s >> b) & 1):
                s ^= (1 << a) | (1 << b)
        else:
            raise ValueError("not classical: %s" % nm)
    return s


def compact(tqc):
    """The compiled circuit on the physical qubits it touches, and the compact index each virtual
    qubit ENDS on (final layout)."""
    from qiskit import QuantumCircuit
    lay = tqc.layout
    fin = lay.final_index_layout(filter_ancillas=True)
    ini = lay.initial_index_layout(filter_ancillas=True)
    active = set(fin) | set(ini)
    for inst in tqc.data:
        if inst.operation.name != "barrier":
            active.update(tqc.find_bit(q).index for q in inst.qubits)
    order = sorted(active)
    cm = {p: i for i, p in enumerate(order)}
    c = QuantumCircuit(len(order), global_phase=tqc.global_phase)
    for inst in tqc.data:
        if inst.operation.name in ("barrier", "measure"):
            continue
        c.append(inst.operation, [cm[tqc.find_bit(q).index] for q in inst.qubits])
    return c, [cm[p] for p in fin]


def embed(u, m, pos):
    from qiskit import QuantumCircuit
    e = QuantumCircuit(m, global_phase=u.global_phase)
    e.compose(u, qubits=pos, inplace=True)
    return e


def aer_sv(qc, threads):
    from qiskit_aer import AerSimulator
    c = aer_ready(qc, "statevector").copy()
    c.save_statevector()
    sim = AerSimulator(method="statevector", max_parallel_threads=threads, seed_simulator=SEED)
    r = sim.run(c).result()
    if not r.success:
        raise RuntimeError("aer statevector: %s" % r.status)
    return np.asarray(r.get_statevector(c).data)


def ref_sv(qc, threads):
    if qc.size() * 2.0 ** qc.num_qubits <= QI_BUDGET:
        from qiskit.quantum_info import Statevector
        return np.asarray(Statevector(qc).data), "quantum_info"
    return aer_sv(qc, threads), "aer"


def sample(qc, method, threads):
    from qiskit_aer import AerSimulator
    c = aer_ready(qc, method).copy()
    c.measure_all()
    sim = AerSimulator(method=method, seed_simulator=SEED, max_parallel_threads=threads)
    r = sim.run(c, shots=SHOTS).result()
    if not r.success:
        raise RuntimeError("aer %s: %s" % (method, r.status))
    return r.get_counts()


def qpy_path(name, tag):
    return os.path.join(QPY, "%s.%s.qpy" % (name.replace("/", "__"), tag))


def qpy_save(fn, circs):
    from qiskit import qpy
    with open(fn, "wb") as fh:
        qpy.dump(circs, fh)


def qpy_load(fn):
    from qiskit import qpy
    with open(fn, "rb") as fh:
        return qpy.load(fh)


# ---------------------------------------------------------------------------------- jobs --
def job_compile(name, root, row, threads):
    from qiskit.transpiler import generate_preset_pass_manager
    r = {}
    t0 = time.time()
    qc = load(os.path.join(root, name), root)
    r["parse_s"] = round(time.time() - t0, 3)
    r["n"] = qc.num_qubits
    r["size"] = qc.size()
    r["depth"] = qc.depth()
    r["nonlocal"] = qc.num_nonlocal_gates()
    u = reference(qc)
    nm = opnames(u)
    r["unitary"] = not (nm & NONUNITARY)
    r["classical"] = r["unitary"] and nm <= CLASSICAL
    r["clifford"] = r["unitary"] and nm <= CLIFFORD
    d, cmap = heavy_hex(qc.num_qubits)
    r["heavy_hex_d"] = d
    r["device_qubits"] = cmap.size()
    t0 = time.time()
    pm = generate_preset_pass_manager(optimization_level=2, coupling_map=cmap,
                                      basis_gates=BASIS, seed_transpiler=SEED)
    tqc = pm.run(qc)
    r["transpile_s"] = round(time.time() - t0, 3)
    r["t_size"] = tqc.size()
    r["t_depth"] = tqc.depth()
    r["t_cz"] = int(tqc.count_ops().get("cz", 0))
    r["t_sha256"] = circuit_sha(tqc)
    r["t_seq_sha256"], r["t_ang_sha256"] = gate_shas(tqc)
    edges = set(cmap.get_edges())
    r["bad_gate"], r["bad_edge"] = structure(tqc, edges)
    if r["unitary"]:
        # The semantic check compiles the circuit WITHOUT its final measurements: with them, the
        # transpiler may legally drop diagonal gates before a measurement (measured: dnn_n2,
        # error_correctiond3_n5 and five more come out ~0.5 off in state fidelity with an
        # unchanged output distribution), which is right for the measured circuit and wrong for
        # a state comparison. Same pass manager settings, same seed.
        t0 = time.time()
        tqu = generate_preset_pass_manager(optimization_level=2, coupling_map=cmap, basis_gates=BASIS,
                                           seed_transpiler=SEED).run(qubits_only(qc.remove_final_measurements(inplace=False)))
        r["u_transpile_s"] = round(time.time() - t0, 3)
        r["u_sha256"] = circuit_sha(tqu)
        r["u_t_size"] = tqu.size()
        g, e = structure(tqu, edges)
        r["bad_gate"] += g
        r["bad_edge"] += e
        c, pos = compact(tqu)
        r["active_width"] = c.num_qubits
        r["final_pos"] = pos
        if c.num_qubits <= SEM_IN_POOL:
            r.update(sem_dense(c, embed(u, c.num_qubits, pos), 1))
        else:
            qpy_save(qpy_path(name, "sem"), [c, u])
        if r["n"] <= SEM_IN_POOL:
            r.update(direct(u, 1, name))
    else:
        r["sem"] = "skipped: mid-circuit measurement, reset or classical control"
    return r


def sem_dense(c, e, threads):
    t0 = time.time()
    got = aer_sv(c, threads)
    exp, how = ref_sv(e, threads)
    ov = abs(np.vdot(exp, got)) ** 2
    return {"sem": "dense", "sem_ref": how, "sem_width": c.num_qubits,
            "infidelity": float(1.0 - ov), "phase_maxdiff": float(np.abs(exp - got).max()),
            "norm_err": float(abs(np.vdot(got, got).real - 1.0)), "sem_s": round(time.time() - t0, 3)}


def job_sem_dense(name, root, row, threads):
    c, u = qpy_load(qpy_path(name, "sem"))
    return sem_dense(c, embed(u, c.num_qubits, row["final_pos"]), threads)


def job_sem_mps(name, root, row, threads):
    c, u = qpy_load(qpy_path(name, "sem"))
    return sem_mps(c, u, row["final_pos"], threads)


def sem_mps(c, u, pos, threads):
    from qiskit_aer import AerSimulator
    t0 = time.time()
    m = c.num_qubits
    test = aer_ready(c.compose(embed(u, m, pos).inverse()), "matrix_product_state")
    test.save_amplitudes_squared([0])
    sim = AerSimulator(method="matrix_product_state", max_parallel_threads=threads,
                       seed_simulator=SEED)
    res = sim.run(test).result()
    if not res.success:
        raise RuntimeError("aer mps: %s" % res.status)
    p0 = float(np.asarray(res.data(0)["amplitudes_squared"])[0])
    return {"sem": "mps", "sem_width": m, "infidelity": 1.0 - p0, "sem_s": round(time.time() - t0, 3)}


def direct(u, threads, label):
    from qiskit.quantum_info import StabilizerState
    n = u.num_qubits
    out = {}
    t0 = time.time()
    sv = aer_sv(u, threads)
    out["direct_s"] = round(time.time() - t0, 3)
    out["direct_norm_err"] = float(abs(np.vdot(sv, sv).real - 1.0))
    if u.size() * 2.0 ** n <= QI_BUDGET:
        from qiskit.quantum_info import Statevector
        out["qi_vs_aer_maxdiff"] = float(np.abs(np.asarray(Statevector(u).data) - sv).max())
    p = np.abs(sv) ** 2
    nm = opnames(u)
    if nm <= CLASSICAL:
        out["closed_form"] = "classical"
        out["closed_form_err"] = float(1.0 - p[bitsim(u)])
    elif nm <= CLIFFORD:
        st = StabilizerState(u)
        sup = np.flatnonzero(p > 1e-12)
        q = []
        for i in sup:
            b = format(int(i), "0%db" % n)
            q.append(st.probabilities_dict_from_bitstring(b)[b])
        q = np.array(q)
        out["closed_form"] = "clifford"
        out["support"] = int(len(sup))
        out["closed_form_err"] = float(max(np.abs(p[sup] - q).max(), abs(len(sup) * q[0] - 1.0)))
    elif os.path.basename(label).startswith("qft"):
        # the QFT of any basis state has every amplitude at magnitude 2^(-n/2)
        out["closed_form"] = "qft of a basis state"
        out["closed_form_err"] = float(np.abs(np.abs(sv) - 2.0 ** (-n / 2)).max())
    return out


def job_direct(name, root, row, threads):
    u = reference(load(os.path.join(root, name), root))
    return direct(u, threads, name)


def job_fullwidth(name, root, row, threads):
    from qiskit.quantum_info import StabilizerState
    u = reference(load(os.path.join(root, name), root))
    n = u.num_qubits
    t0 = time.time()
    r = {}
    if row["classical"]:
        key = format(bitsim(u), "0%db" % n)
        cnt = sample(u, "matrix_product_state", threads)
        r["fw_mps_exact"] = bool(dict(cnt) == {key: SHOTS})
    else:
        st = StabilizerState(u)
        ok, probs = True, set()
        for meth in ("stabilizer", "matrix_product_state"):
            cnt = sample(u, meth, threads)
            for k in cnt:
                pk = st.probabilities_dict_from_bitstring(k)[k]
                probs.add(pk)
                ok = ok and pk > 0
            r["fw_%s_outcomes" % meth] = len(cnt)
        r["fw_clifford_in_support"] = bool(ok and len(probs) == 1)
        r["fw_p"] = min(probs) if probs else None
    r["fw_s"] = round(time.time() - t0, 3)
    return r


def job_control_l1(name, root, row, threads):
    """The same circuit at optimization_level=1 (no two-qubit resynthesis), same device and seed."""
    from qiskit.transpiler import generate_preset_pass_manager
    qc = load(os.path.join(root, name), root)
    u = reference(qc)
    t0 = time.time()
    tqc = generate_preset_pass_manager(optimization_level=1, coupling_map=heavy_hex(qc.num_qubits)[1],
                                       basis_gates=BASIS, seed_transpiler=SEED).run(
                                           qubits_only(qc.remove_final_measurements(inplace=False)))
    c, pos = compact(tqc)
    r = {"t_size": tqc.size()}
    r |= (sem_dense(c, embed(u, c.num_qubits, pos), threads) if c.num_qubits <= SEM_MAX
         else sem_mps(c, u, pos, threads))
    return {"l1_" + k: v for k, v in r.items()} | {"l1_transpile_s": round(time.time() - t0, 3)}


JOBS = {"control_l1": job_control_l1, "compile": job_compile, "sem_dense": job_sem_dense, "sem_mps": job_sem_mps,
        "direct": job_direct, "fullwidth": job_fullwidth}


def _child(kind, name, root, row, threads, q):
    os.environ["RAYON_NUM_THREADS"] = str(threads)
    try:
        r = JOBS[kind](name, root, row, threads)
    except Exception as e:
        r = {"%s_error" % kind: "%s: %s" % (type(e).__name__, str(e)[:400]),
             "%s_traceback" % kind: traceback.format_exc()[-1500:]}
    r["%s_maxrss_gib" % kind] = round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 2 ** 20, 3)
    q.put(r)


def mem_gib():
    """What this container may use: the cgroup limit if there is one, else the host's RAM."""
    tot = None
    try:
        v = open("/sys/fs/cgroup/memory.max").read().strip()
        if v != "max":
            tot = int(v) / 2 ** 30
    except Exception:
        pass
    try:
        for line in open("/proc/meminfo"):
            if line.startswith("MemTotal:"):
                h = int(line.split()[1]) / 2 ** 20
                tot = h if tot is None else min(tot, h)
    except Exception:
        pass
    return tot or 8.0


def run_jobs(kind, todo, root, rows, workers, threads, limit, weight=None, cap=None):
    """Each job in its own process, `workers` at a time, killed at `limit` seconds. With `weight`
    (estimated GiB per job), jobs start only while the in-flight estimate stays under `cap`; a job
    bigger than the cap runs once nothing else is."""
    ctx = mp.get_context("spawn")
    pending, live = list(todo), []
    w = weight or (lambda name: 0.0)
    while pending or live:
        while pending and len(live) < workers:
            inflight = sum(w(x[2]) for x in live)
            pick = None
            for name in pending:
                if cap is None or not live or inflight + w(name) <= cap:
                    pick = name
                    break
            if pick is None:
                break
            pending.remove(pick)
            name = pick
            q = ctx.Queue()
            p = ctx.Process(target=_child, args=(kind, name, root, rows[name], threads, q))
            p.start()
            live.append((p, q, name, time.time()))
        time.sleep(0.2)
        for item in list(live):
            p, q, name, t0 = item
            res = None
            try:
                res = q.get_nowait()
            except Exception:
                pass
            if res is not None:
                p.join()
            elif time.time() - t0 > limit:
                p.kill()
                p.join()
                res = {"%s_timeout_s" % kind: limit}
            elif not p.is_alive() and p.exitcode is not None:
                try:
                    res = q.get(timeout=5)
                except Exception:
                    res = {"%s_error" % kind: "process died, exit %s" % p.exitcode}
            if res is not None:
                res["%s_wall_s" % kind] = round(time.time() - t0, 2)
                rows[name].update(res)
                live.remove(item)
                show = {k: v for k, v in res.items() if not k.endswith("traceback")
                        and k not in ("final_pos", "t_sha256")}
                log("%s\t%s\t%s" % (kind, name, json.dumps(show, sort_keys=True, default=str)))


def main():
    root, outd, workers = sys.argv[1], sys.argv[2], int(sys.argv[3])
    os.makedirs(outd, exist_ok=True)
    os.makedirs(QPY, exist_ok=True)
    import qiskit
    import qiskit_aer
    files = sorted(os.path.relpath(p, root) for p in glob.glob(os.path.join(root, "*", "*", "*.qasm"))
                   if not p.endswith("_transpiled.qasm"))
    log("circuits\t%d" % len(files))
    log("qiskit\t%s\nqiskit_aer\t%s\nnumpy\t%s" % (str(qiskit.__version__),
        str(qiskit_aer.__version__), str(np.__version__)))
    threads = os.cpu_count()
    rows = {f: {"circuit": f} for f in files}
    T = {}
    # 1. compile everything, biggest files first so the long compiles start at once
    t0 = time.time()
    order = sorted(files, key=lambda f: -os.path.getsize(os.path.join(root, f)))
    # compile memory: measured locally on bwt_n21 (1.5 MB of QASM -> 0.89 GiB peak), so ~0.6 GiB per
    # MB; budgeted at 0.75 against 70% of the box so a few huge circuits cannot OOM it together
    size_mb = {f: os.path.getsize(os.path.join(root, f)) / 1e6 for f in files}
    cap = 0.7 * mem_gib()
    log("memory_gib\t%.1f\tcompile_cap_gib\t%.1f" % (mem_gib(), cap))
    run_jobs("compile", order, root, rows, workers, 1, T_COMPILE,
             weight=lambda f: 0.2 + 0.75 * size_mb[f], cap=cap)
    T["phase1_compile_s"] = round(time.time() - t0, 1)
    ok = [f for f in files if "transpile_s" in rows[f] and rows[f]["unitary"]]
    # 2. wide compiled circuits: dense serially (all threads), wider ones by MPS inverse test
    t0 = time.time()
    dense = [f for f in ok if SEM_IN_POOL < rows[f]["active_width"] <= SEM_MAX and "sem" not in rows[f]]
    for f in list(dense):
        cost = rows[f]["t_size"] * 2.0 ** rows[f]["active_width"]
        if cost > DENSE_BUDGET:
            dense.remove(f)
            rows[f]["sem"] = "none: dense cost %.1e > %.0e" % (cost, DENSE_BUDGET)
    run_jobs("sem_dense", dense, root, rows, 1, threads, T_SERIAL)
    T["phase2_sem_dense_s"] = round(time.time() - t0, 1)
    t0 = time.time()
    wide = [f for f in ok if rows[f]["active_width"] > SEM_MAX
            or str(rows[f].get("sem", "")).startswith("none: dense cost")]
    run_jobs("sem_mps", wide, root, rows, workers, 1, T_MPS)
    T["phase3_sem_mps_s"] = round(time.time() - t0, 1)
    # 3. originals at the box's dense limit, then full width for classical / Clifford
    t0 = time.time()
    big = [f for f in ok if SEM_IN_POOL < rows[f]["n"] <= DENSE_MAX]
    for f in list(big):
        cost = rows[f]["size"] * 2.0 ** rows[f]["n"]
        if cost > DENSE_BUDGET:
            big.remove(f)
            rows[f]["direct"] = "none: dense cost %.1e > %.0e" % (cost, DENSE_BUDGET)
    run_jobs("direct", big, root, rows, 1, threads, T_SERIAL)
    T["phase4_direct_s"] = round(time.time() - t0, 1)
    t0 = time.time()
    fw = [f for f in ok if rows[f]["classical"] or rows[f]["clifford"]]
    run_jobs("fullwidth", fw, root, rows, workers, 1, T_MPS)
    T["phase5_fullwidth_s"] = round(time.time() - t0, 1)
    # 4. every circuit the level-2 compile left above machine precision, recompiled at level 1
    t0 = time.time()
    flag = [f for f in ok if abs(rows[f].get("infidelity", 0)) > exact_bound(rows[f]["u_t_size"] + rows[f]["size"])]
    run_jobs("control_l1", flag, root, rows, 1, threads, T_SERIAL)
    T["phase6_control_l1_s"] = round(time.time() - t0, 1)
    with open(os.path.join(outd, "circuits.jsonl"), "w") as fh:
        for f in files:
            fh.write(json.dumps(rows[f], sort_keys=True) + "\n")
    with open(os.path.join(outd, "phases.txt"), "w") as fh:
        for k, v in T.items():
            fh.write("%s\t%s\n" % (k, v))
            log("%s\t%s" % (k, v))
    sys.exit(check([rows[f] for f in files], outd))


def check(rows, outd):
    lines, fails = [], []

    def a(label, ok, obs):
        lines.append("%s\t%s\t%s" % ("PASS" if bool(ok) else "FAIL", label, obs))
        if not bool(ok):
            fails.append(label)

    def rep(label, obs):
        lines.append("REPORT\t%s\t%s" % (label, obs))

    good = [r for r in rows if "transpile_s" in r]
    # A file that is not valid OpenQASM 2 must be refused by the parser, never compiled silently;
    # anything else that fails to compile is a failure.
    invalid = [r for r in rows if str(r.get("compile_error", "")).startswith("QASM2ParseError")]
    other = [r for r in rows if r not in good and r not in invalid]
    a("every circuit compiles, or is refused as invalid OpenQASM 2 (%ds limit)" % T_COMPILE, not other,
      "%d/%d compiled; %d refused: %s; other failures: %s" % (
          len(good), len(rows), len(invalid),
          "; ".join("%s (%s)" % (r["circuit"], r["compile_error"].split(": ", 1)[1]) for r in invalid),
          " ".join("%s %s" % (r["circuit"], r.get("compile_error", "timeout")) for r in other) or "none"))
    a("every compiled gate is in {cz,rz,sx,x}", all(r["bad_gate"] == 0 for r in good),
      "%d outside the basis" % sum(r["bad_gate"] for r in good))
    a("every compiled 2q gate sits on a heavy-hex edge", all(r["bad_edge"] == 0 for r in good),
      "%d off-edge, %d cz gates in all" % (sum(r["bad_edge"] for r in good),
                                           sum(r["t_cz"] for r in good)))
    sem = [r for r in good if "infidelity" in r]
    above_exact = lambda r: abs(r["infidelity"]) > exact_bound(r["u_t_size"] + r["size"])
    fin = lambda x: isinstance(x, float) and math.isfinite(x)
    for how in ("dense", "mps"):
        k = [r for r in sem if r["sem"] == how]
        if not k:
            continue
        w = max(k, key=lambda r: r["infidelity"] if fin(r["infidelity"]) else 9)
        # NaN fails a one-sided bound silently, so finiteness is part of the assertion
        a("%s: level-2 compiled == original, |1-|<E|C>|^2| <= %.0e" % (how, SEM_CEIL),
          all(fin(r["infidelity"]) and abs(r["infidelity"]) <= SEM_CEIL for r in k),
          "%d circuits, widths %d-%d, worst %.3e (%s); %d at machine precision (<= %d eps/gate)%s" % (
              len(k), min(r["sem_width"] for r in k), max(r["sem_width"] for r in k),
              w["infidelity"], w["circuit"], sum(not above_exact(r) for r in k), EXACT_PER_GATE,
              "; %d against quantum_info" % sum(r.get("sem_ref") == "quantum_info" for r in k)
              if how == "dense" else ""))
    above = [r for r in sem if above_exact(r)]
    if above:
        a("every level-2 circuit above machine precision is exact at level 1 (<= %d eps/gate)" % EXACT_PER_GATE,
          all(fin(r.get("l1_infidelity")) and abs(r["l1_infidelity"]) <= exact_bound(r["l1_t_size"] + r["size"])
              for r in above),
          "; ".join("%s L2 %.2e L1 %s" % (r["circuit"], r["infidelity"],
                    "%.2e" % r["l1_infidelity"] if "l1_infidelity" in r else "none") for r in above))
    dn = [r for r in sem if r["sem"] == "dense"]
    if dn:
        # recursive-summation bound: eps per gate applied plus eps per amplitude summed
        a("dense: compiled state norm within eps*(gates + 2^width)",
          all(r["norm_err"] <= EPS * (r["u_t_size"] + 2.0 ** r["sem_width"]) for r in dn),
          "worst %.3e" % max(r["norm_err"] for r in dn))
        rep("dense: max |E-C| including global phase", "worst %.3e" % max(r["phase_maxdiff"] for r in dn))
    unit = [r for r in good if r["unitary"]]
    rep("originals not simulated directly (dense cost over budget)", " ".join(
        "%s(n=%d)" % (r["circuit"], r["n"]) for r in unit if str(r.get("direct", "")).startswith("none")))
    rep("unitary circuits with no semantic verdict", "%d of %d: %s" % (
        len(unit) - len([r for r in unit if r in sem]), len(unit),
        " ".join("%s(w=%s,%s)" % (r["circuit"], r.get("active_width"),
                 "timeout" if any(k.endswith("timeout_s") for k in r) else r.get("sem", "error"))
                 for r in unit if r not in sem)))
    rep("non-unitary circuits (structural check only)",
        "%d: %s" % (len(good) - len(unit), " ".join(r["circuit"] for r in good if not r["unitary"])))
    d = [r for r in good if "direct_norm_err" in r]
    if d:
        a("direct dense: norm within eps*(gates + 2^n)",
          all(r["direct_norm_err"] <= EPS * (r["size"] + 2.0 ** r["n"]) for r in d),
          "%d originals up to %d qubits, worst %.3e" % (len(d), max(r["n"] for r in d),
                                                        max(r["direct_norm_err"] for r in d)))
        cf = [r for r in d if "closed_form_err" in r]
        for kind in sorted({r["closed_form"] for r in cf}):
            k = [r for r in cf if r["closed_form"] == kind]
            a("direct dense: closed form (%s) within 1e-10" % kind,
              all(math.isfinite(r["closed_form_err"]) and r["closed_form_err"] <= 1e-10 for r in k),
              "%d originals up to %d qubits, worst %.3e" % (
                  len(k), max(r["n"] for r in k), max(r["closed_form_err"] for r in k)))
        qi = [r for r in d if "qi_vs_aer_maxdiff" in r]
        a("direct dense: quantum_info Statevector == Aer within 1e-10",
          all(r["qi_vs_aer_maxdiff"] <= 1e-10 for r in qi),
          "%d originals, worst %.3e" % (len(qi), max(r["qi_vs_aer_maxdiff"] for r in qi)))
    c = [r for r in good if r["unitary"] and r["classical"]]
    if c:
        a("full width: Aer MPS on every classical original == integer bit simulation",
          all(r.get("fw_mps_exact") for r in c),
          "%d originals up to %d qubits" % (len(c), max(r["n"] for r in c)))
    s = [r for r in good if r["unitary"] and r["clifford"]]
    if s:
        a("full width: Aer stabilizer + MPS samples of every Clifford original in terra's support at one 2^-k",
          all(r.get("fw_clifford_in_support") for r in s),
          "%d originals up to %d qubits" % (len(s), max(r["n"] for r in s)))
    errs = [(r["circuit"], k, v) for r in rows for k, v in r.items() if k.endswith("_error")]
    rep("job errors", "%d: %s" % (len(errs), "; ".join("%s %s %s" % e for e in errs)[:1500]))
    rep("widest circuit compiled", "%d qubits" % max(r["n"] for r in good))
    with open(os.path.join(outd, "check.txt"), "w") as fh:
        fh.write("\n".join(lines) + "\n")
    log("\n".join(lines))
    log("ALL CHECKS PASSED" if not fails else "FAILED: %s" % "; ".join(fails))
    return 1 if fails else 0


if __name__ == "__main__":
    main()
