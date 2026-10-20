#!/usr/bin/env python3
"""fastjet-real: anti-k_t R = 1.0 over all 1.1 M LHC Olympics 2020 R&D events, with identities.

    python3 -u cluster.py <datadir> <workers> <outdir>

Every event's hadrons (pt, eta, phi) become massless 4-vectors and are clustered by FastJet in
multi-event mode, `workers` processes over 20,000-event chunks, one BLAS/OpenMP thread each. What
is asserted, on every event unless stated:

 * PARTITION -- the constituents of the inclusive jets (no pt cut) are every input index exactly
   once. Integer, exact.
 * CONSERVATION -- the jets sum to the event's 4-momentum within the rigorous bound for two
   float64 summations in any order, 2 (n-1) u sum|x| (Higham); the worst ratio is reported.
 * COLLINEAR SAFETY -- splitting each event's hardest particle into two exact halves leaves every
   jet bit-identical.
 * AN INDEPENDENT ANTI-K_T (brute.py, the definition searched exhaustively) finds the same
   partition on a stride of events, and the same jet 4-momenta to summation round-off.
 * THE ORGANISERS' PUBLISHED JETS -- the R&D features file carries the two leading R = 1.0 jets
   (px, py, pz, m) computed by the LHCO organisers; FastJet here must reproduce them.
 * DETERMINISM -- the first chunk clustered twice is bit-identical.
"""
import json
import os
import resource
import sys
import time
from multiprocessing import Pool

import numpy as np

DATA, WORKERS, OUT = sys.argv[1], int(sys.argv[2]), sys.argv[3]
R = 1.0
CH = 20000
BRUTE_STRIDE = 100
AXES = ("OnePass_KT_Axes",)
U = 2.0 ** -53
ROWS = []


def rec(k, v):
    v = str(v)
    print("%s\t%s" % (k, v), flush=True)
    ROWS.append((str(k), v))


def die(msg):
    print("FAIL: %s" % msg, flush=True)
    write_score()
    sys.exit(1)


def write_score():
    with open(os.path.join(OUT, "score.tsv"), "w") as fh:
        fh.write("observable\tvalue\n")
        for k, v in ROWS:
            fh.write("%s\t%s\n" % (k, v))


META = json.load(open(os.path.join(DATA, "lhco-meta.json")))
NEV = META["events"]
COUNTS = np.fromfile(os.path.join(DATA, "lhco-counts.i32"), dtype=np.int32)
OFF = np.zeros(NEV + 1, dtype=np.int64)
np.cumsum(COUNTS, out=OFF[1:])
PEP_DT = np.dtype(META["value_dtype"])
PEP_PATH = os.path.join(DATA, META["pep_file"])


def vectors(lo, hi):
    """Massless 4-vectors of events [lo, hi) in float64, from (pt, eta, phi)."""
    pep = np.memmap(PEP_PATH, dtype=PEP_DT, mode="r", shape=(int(OFF[-1]), 3))
    a = np.asarray(pep[OFF[lo]:OFF[hi]], dtype=np.float64)
    pt, eta, phi = a[:, 0], a[:, 1], a[:, 2]
    # values that are not float32-representable: the bound on the features comparison assumes none
    g = int(np.sum(a.astype(np.float32).astype(np.float64) != a))
    return pt * np.cos(phi), pt * np.sin(phi), pt * np.sinh(eta), pt * np.cosh(eta), g


def fingerprint(*arrs):
    """Order-sensitive integer fingerprint of float64 bits: exact, comparable across chips."""
    h = np.uint64(0)
    for x in arrs:
        b = np.ascontiguousarray(x, dtype=np.float64).view(np.uint64)
        w = (np.arange(b.size, dtype=np.uint64) + np.uint64(1))
        with np.errstate(over="ignore"):
            h = h + np.sum(b * w, dtype=np.uint64)
    return int(h)


def work(c):
    os.environ["OMP_NUM_THREADS"] = "1"
    import awkward as ak
    import fastjet
    lo, hi = c * CH, min(NEV, (c + 1) * CH)
    cnt = COUNTS[lo:hi]
    px, py, pz, E, grid = vectors(lo, hi)
    jd = fastjet.JetDefinition(fastjet.antikt_algorithm, R)

    def clus(px, py, pz, E, cnt):
        ev = ak.unflatten(ak.zip({"px": px, "py": py, "pz": pz, "E": E}), cnt)
        t0 = time.perf_counter()
        cs = fastjet.ClusterSequence(ev, jd)
        jets = cs.inclusive_jets()
        ci = cs.constituent_index()
        return jets, ci, time.perf_counter() - t0

    jets, ci, dt = clus(px, py, pz, E, cnt)
    nev = hi - lo
    # jets as flat numpy, sorted by pt within event (descending)
    nj = ak.to_numpy(ak.num(jets))
    jx = ak.to_numpy(ak.flatten(jets.px)); jy = ak.to_numpy(ak.flatten(jets.py))
    jz = ak.to_numpy(ak.flatten(jets.pz)); je = ak.to_numpy(ak.flatten(jets.E))
    joff = np.zeros(nev + 1, dtype=np.int64); np.cumsum(nj, out=joff[1:])
    jpt2 = jx * jx + jy * jy
    ev_of_jet = np.repeat(np.arange(nev), nj)
    order = np.lexsort((-jpt2, ev_of_jet))
    jx, jy, jz, je, jpt2 = jx[order], jy[order], jz[order], je[order], jpt2[order]

    # PARTITION: flattened constituent indices per event, sorted, == 0..n-1
    flat = ak.flatten(ci, axis=2)
    ncon = ak.to_numpy(ak.num(flat))
    srt = ak.to_numpy(ak.flatten(ak.sort(flat, axis=1)))
    loc = np.arange(srt.size) - np.repeat(np.cumsum(ncon) - ncon, ncon)
    part_ok = np.array_equal(ncon, cnt) and np.array_equal(srt, loc)

    # CONSERVATION within 2 (n-1) u sum|x|, per component, per event
    poff = np.zeros(nev + 1, dtype=np.int64); np.cumsum(cnt, out=poff[1:])
    worst_ratio = 0.0
    for P, J in ((px, jx), (py, jy), (pz, jz), (E, je)):
        sp = np.add.reduceat(P, poff[:-1]) if nev else P[:0]
        sj = np.add.reduceat(J, joff[:-1])
        sa = np.add.reduceat(np.abs(P), poff[:-1])
        r = np.abs(sp - sj) / (U * sa)
        bound = 2.0 * (cnt - 1)
        if np.any(~np.isfinite(r)) or np.any(r > bound):
            worst_ratio = float("inf")
            break
        worst_ratio = max(worst_ratio, float(np.max(r / bound)))

    # COLLINEAR: halve each event's hardest particle, append the other half at the event's end
    pt2 = px * px + py * py
    hard = np.array([poff[i] + int(np.argmax(pt2[poff[i]:poff[i + 1]])) for i in range(nev)])
    def split(X):
        Y = X.copy(); Y[hard] = X[hard] * 0.5
        return np.insert(Y, poff[1:], X[hard] * 0.5)
    sjets, _, t_split = clus(split(px), split(py), split(pz), split(E), cnt + 1)
    snj = ak.to_numpy(ak.num(sjets))
    sx = ak.to_numpy(ak.flatten(sjets.px)); sy = ak.to_numpy(ak.flatten(sjets.py))
    sz = ak.to_numpy(ak.flatten(sjets.pz)); se = ak.to_numpy(ak.flatten(sjets.E))
    sord = np.lexsort((-(sx * sx + sy * sy), np.repeat(np.arange(nev), snj)))
    coll_bad = int(np.sum(snj != nj))
    if coll_bad == 0:
        same = ((sx[sord] == jx) & (sy[sord] == jy) & (sz[sord] == jz) & (se[sord] == je))
        coll_bad = int(np.unique(np.repeat(np.arange(nev), nj)[~same]).size)

    # leading two jets per event (pt-ordered) for the published-features comparison
    lead = np.full((nev, 2, 4), np.nan)
    for k in (0, 1):
        has = nj > k
        idx = joff[:-1][has] + k
        lead[has, k] = np.stack([jx[idx], jy[idx], jz[idx], je[idx]], axis=1)

    t_tau0 = time.perf_counter()
    # N-subjettiness tau_1..3 of the two leading jets (fjcontrib Nsubjettiness through fastjet's
    # njettiness), unnormalised, beta = 1. The organisers say only "fastjet plugins"; of KT, WTA_KT
    # and OnePass_KT axes, OnePass_KT is the one their numbers match (results/probe-axes/).
    ev = ak.unflatten(ak.zip({"px": px, "py": py, "pz": pz, "E": E}), cnt)
    cio = ci[ak.argsort(jets.px ** 2 + jets.py ** 2, axis=1, ascending=False)]
    taus = np.full((nev, 2, len(AXES), 3), np.nan)
    ncons = np.zeros((nev, 2), dtype=np.int64)    # constituents of each leading jet
    for k in (0, 1):
        has = nj > k
        ncons[has, k] = ak.to_numpy(ak.num(cio[has][:, k]))
        sel = ev[has][cio[has][:, k]]
        sub = fastjet.ClusterSequence(sel, fastjet.JetDefinition(fastjet.kt_algorithm, R))
        for a, axes in enumerate(AXES):
            t = sub.njettiness(measure_definition="UnnormalizedMeasure", axes_definition=axes,
                               njets=[1, 2, 3], beta=1.0, R0=R)
            taus[has, k, a] = ak.to_numpy(t).reshape(-1, 3)

    t_tau = time.perf_counter() - t_tau0
    # the clustering DECISIONS as integers: every particle labelled with its jet's pt rank
    rank = ak.flatten(ak.broadcast_arrays(ak.local_index(cio, axis=1), cio)[0], axis=2)
    pidx = ak.flatten(cio, axis=2)
    assign = np.empty(int(poff[-1]), dtype=np.int64)
    assign[ak.to_numpy(ak.flatten(pidx + poff[:-1]))] = ak.to_numpy(ak.flatten(rank))
    fp_part = int(np.sum((assign + 1).astype(np.uint64) * (np.arange(assign.size, dtype=np.uint64) + np.uint64(OFF[lo] + 1)), dtype=np.uint64))

    t_br0 = time.perf_counter()
    # BRUTE FORCE on a stride of events
    from brute import antikt
    brute = {"events": 0, "partition_mismatch": [], "max_rel": 0.0}
    ci_list = None
    for i in range(nev):
        g = lo + i
        if g % BRUTE_STRIDE:
            continue
        if ci_list is None:
            ci_list = ak.to_list(ci)
        s, e = poff[i], poff[i + 1]
        bj = antikt(px[s:e], py[s:e], pz[s:e], E[s:e], R)
        fj = {tuple(sorted(c)) for c in ci_list[i]}
        bset = {b[0] for b in bj}
        brute["events"] += 1
        if fj != bset:
            brute["partition_mismatch"].append(g)
            continue
        # 4-momenta: brute jets keyed by constituents vs FastJet's jets, pt-matched via sets
        bmap = {b[0]: np.array(b[1:]) for b in bj}
        fjets = ak.to_list(jets[i])
        for cons, fjet in zip(ci_list[i], fjets):
            v = np.array([fjet["px"], fjet["py"], fjet["pz"], fjet["E"]])
            w = bmap[tuple(sorted(cons))]
            scale = float(np.abs(px[s:e][list(cons)]).sum() + np.abs(py[s:e][list(cons)]).sum()
                          + np.abs(pz[s:e][list(cons)]).sum() + E[s:e][list(cons)].sum())
            brute["max_rel"] = max(brute["max_rel"], float(np.max(np.abs(v - w))) / (2 * U * scale * len(cons)))

    t_brute = time.perf_counter() - t_br0
    det = None
    if c == 0:
        j2, ci2, _ = clus(px, py, pz, E, cnt)
        det = bool(ak.all(j2.px == jets.px) and ak.all(j2.E == jets.E)
                   and ak.to_list(ak.flatten(ci2, axis=None)) == ak.to_list(ak.flatten(ci, axis=None)))
    return {"rss": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
            "t_split": t_split, "t_tau": t_tau, "t_brute": t_brute, "c": c, "lo": lo, "hi": hi, "dt": dt, "nj": nj, "lead": lead, "taus": taus, "ncons": ncons, "grid": grid, "part_ok": part_ok,
            "worst_ratio": worst_ratio, "coll_bad": coll_bad, "brute": brute, "det": det,
            "fp_in": fingerprint(px, py, pz, E), "fp_part": fp_part, "fp_jets": fingerprint(jx, jy, jz, je),
            "nj20": np.add.reduceat((jpt2 > 400.0).astype(np.int64), joff[:-1]) if nev else None}


def mass(v):
    """Jet mass as PseudoJet::m() computes it: m2 = (E+pz)(E-pz) - kt2, signed sqrt."""
    m2 = (v[..., 3] + v[..., 2]) * (v[..., 3] - v[..., 2]) - (v[..., 0] ** 2 + v[..., 1] ** 2)
    return np.sign(m2) * np.sqrt(np.abs(m2))


def sig6_f32(x):
    """A value as the features file stores it: printed to 6 significant figures, then float32."""
    return np.array([float(np.float32(float("%.6g" % v))) for v in x.tolist()])


def published(lead, taus, ncons, grid):
    """Hold the two leading jets against the organisers' features file (Zenodo 6466204).

    The record is float32 at both ends, and the bound is built from exactly that, nothing else:
     * OUTPUT: the features are printed to 6 significant figures and stored as float32 (-1467.239990
       is float32(-1467.24)) -- half a unit in the 6th figure plus float32's half-ulp;
     * PIPELINE: the particles are float32 (asserted below: every value float32-representable), so a
       pipeline that formed and summed the 4-vectors in float32 is as faithful as ours. With
       u32 = 2^-24, forming a massless particle's px, py, pz, E costs <= 3 u32 E_i and summing n of
       them <= (n - 1) u32 sum E_i, so a jet's components may differ by B = (n + 2) u32 E_jet.
    Masses are compared as signed m^2, which carries 6 E B + 4 B^2 from that plus float64's 8 u E^2.
    Then, more sharply, our value printed the same way is counted against the published bits.
    """
    cols = [l.strip() for l in open(os.path.join(DATA, "lhco-features.columns"))]
    F = np.fromfile(os.path.join(DATA, "lhco-features.f8"), dtype=np.float64).reshape(NEV, len(cols))
    C = {n: i for i, n in enumerate(cols)}
    label = np.fromfile(os.path.join(DATA, "lhco-label.i8"), dtype=np.int8)
    names = ["pxj1", "pyj1", "pzj1", "mj1", "pxj2", "pyj2", "pzj2", "mj2"]
    P = F[:, [C[n] for n in names]]
    O = np.concatenate([lead[:, 0, :3], mass(lead[:, 0])[:, None],
                        lead[:, 1, :3], mass(lead[:, 1])[:, None]], axis=1)
    mom = [0, 1, 2, 4, 5, 6]
    # IDENTIFICATION: does row i describe event i? Six momentum components within 0.05 GeV --
    # loose enough to survive any rounding above, tight enough that no other event qualifies.
    TOL = 0.05
    rowwise = np.all(np.abs(O[:, mom] - P[:, mom]) <= TOL, axis=1)
    rec("features_rowwise_identified", "%d of %d rows" % (int(rowwise.sum()), NEV))
    lab_eq = int(np.sum(F[:, C["label"]] == label))
    rec("features_label_equal", "%d of %d" % (lab_eq, NEV))
    # and under ANY permutation: each published row against every event within TOL in pxj1
    order = np.argsort(O[:, 0], kind="stable")
    key = O[order, 0]
    lo = np.searchsorted(key, P[:, 0] - TOL, "left")
    hi = np.searchsorted(key, P[:, 0] + TOL, "right")
    nmatch = np.zeros(NEV, dtype=np.int64)
    best = np.full(NEV, -1, dtype=np.int64)
    for k in range(int((hi - lo).max()) if NEV else 0):
        idx = lo + k
        valid = idx < hi
        cand = order[np.where(valid, idx, 0)]
        ok = valid & np.all(np.abs(O[cand][:, mom] - P[:, mom]) <= TOL, axis=1)
        nmatch += ok
        best = np.where(ok & (best < 0), cand, best)
    rec("features_any_permutation", "%d rows match exactly one event, %d none, %d several; matched to self %d"
        % (int(np.sum(nmatch == 1)), int(np.sum(nmatch == 0)), int(np.sum(nmatch > 1)),
           int(np.sum(best == np.arange(NEV)))))
    if not (rowwise.all() and lab_eq == NEV):
        die("the features file does not describe these events row for row")
    if not np.all(nmatch == 1):
        die("a published row is matched by another event too, so the identification is ambiguous")
    # PRECISION
    rec("input_values_not_float32", grid)
    if grid:
        die("%d particle values are not float32-representable, so the float32 bound is unfounded" % grid)
    E1, E2 = lead[:, 0, 3], lead[:, 1, 3]
    Ej = np.stack([E1, E1, E1, E1, E2, E2, E2, E2], axis=1)
    B = (np.repeat(ncons, 4, axis=1) + 2) * 2.0 ** -24 * Ej
    def out_round(x, ref):
        mg = np.maximum(np.abs(x), np.abs(ref))
        return 0.5 * 10.0 ** (np.floor(np.log10(np.where(mg > 0, mg, 1.0))) - 5) + 2.0 ** -24 * np.abs(ref)
    rp = out_round(O, P)
    D = np.abs(O - P)
    allow = rp + B + 2.0 * 560 * U * Ej
    m_ = [3, 7]
    s_o = O[:, m_] * np.abs(O[:, m_])                     # signed m^2, as PseudoJet::m2()
    s_p = P[:, m_] * np.abs(P[:, m_])
    allow_m2 = 6 * Ej[:, m_] * B[:, m_] + 4 * B[:, m_] ** 2 + rp[:, m_] * (2 * np.abs(P[:, m_]) + rp[:, m_]) + 8 * U * Ej[:, m_] ** 2
    worst = D / allow
    worst[:, m_] = np.abs(s_o - s_p) / allow_m2
    for i, n in enumerate(names):
        eq = int(np.sum(sig6_f32(O[:, i]) == P[:, i]))
        rec("features_%s" % n, "max|d| %.3e GeV, worst %.3f of the bound, "
            "6-sig-fig float32 of ours == published %d of %d" % (D[:, i].max(), worst[:, i].max(), eq, NEV))
    if not np.all(worst <= 1.0):
        i, j = np.unravel_index(np.argmax(worst), worst.shape)
        die("event %d %s: ours %.9g, published %.9g, beyond the bound" % (i, names[j], O[i, j], P[i, j]))
    rec("features_bound", "all %d values within output rounding + a float32 pipeline" % D.size)
    # N-SUBJETTINESS, reported: OnePass_KT_Axes is an iterative minimisation from kt seeds, a path
    for k, j in ((0, "j1"), (1, "j2")):
        for t in range(3):
            pub = F[:, C["tau%d%s" % (t + 1, j)]]
            ours = taus[:, k, 0, t]
            d = np.abs(ours - pub)
            mg = np.maximum(np.abs(ours), np.abs(pub))
            al = 0.5 * 10.0 ** (np.floor(np.log10(np.where(mg > 0, mg, 1.0))) - 5) + 2.0 ** -24 * np.abs(pub)
            far = int(np.sum(d > 0.01 * np.maximum(np.abs(pub), 1e-12)))
            eq = int(np.sum(sig6_f32(ours) == pub))
            rec("features_tau%d%s" % (t + 1, j), "within published precision %d, 6-sig-fig equal %d, "
                "off by > 1%% %d, of %d" % (int(np.sum(d <= al)), eq, far, NEV))


def main():
    import awkward as ak
    import fastjet
    rec("fastjet", getattr(fastjet, "__version__", "unknown"))
    rec("awkward", getattr(ak, "__version__", "unknown"))
    rec("numpy", getattr(np, "__version__", "unknown"))
    rec("events", NEV)
    rec("particles", int(OFF[-1]))
    rec("max_particles_per_event", int(COUNTS.max()))
    rec("mean_particles_per_event", "%.2f" % (OFF[-1] / NEV))
    rec("radius", R)
    rec("workers", WORKERS)
    nch = -(-NEV // CH)
    t0 = time.time()
    res = [None] * nch
    with Pool(WORKERS) as pool:
        for r in pool.imap_unordered(work, range(nch)):
            res[r["c"]] = r
            print("chunk %d done %.1f s" % (r["c"], time.time() - t0), flush=True)
    wall = time.time() - t0
    rec("wall_s_pass", "%.1f" % wall)
    # per-phase seconds, summed over workers: the FastJet anti-k_t pass itself, then the checks
    rec("cluster_cpu_s_sum", "%.1f" % sum(r["dt"] for r in res))
    rec("split_recluster_cpu_s_sum", "%.1f" % sum(r["t_split"] for r in res))
    rec("nsubjettiness_cpu_s_sum", "%.1f" % sum(r["t_tau"] for r in res))
    rec("brute_cpu_s_sum", "%.1f" % sum(r["t_brute"] for r in res))
    rec("peak_rss_gib_worker", "%.2f" % (max(r["rss"] for r in res) / 2 ** 20))   # ru_maxrss, KiB on Linux
    rec("peak_rss_gib_main", "%.2f" % (resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 2 ** 20))

    nj = np.concatenate([r["nj"] for r in res])
    lead = np.concatenate([r["lead"] for r in res])
    nj20 = np.concatenate([r["nj20"] for r in res])
    taus = np.concatenate([r["taus"] for r in res])
    ncons = np.concatenate([r["ncons"] for r in res])
    grid = sum(r["grid"] for r in res)
    np.save(os.path.join(OUT, "lead.npy"), lead)
    np.save(os.path.join(OUT, "taus.npy"), taus)
    rec("fingerprint_lead", "%d" % fingerprint(lead.ravel()))
    rec("fingerprint_taus", "%d" % fingerprint(taus.ravel()))
    rec("jets_total", int(nj.sum()))
    rec("jets_pt20_total", int(nj20.sum()))
    rec("fingerprint_input_vectors", "%d" % (sum(r["fp_in"] for r in res) % 2 ** 64))
    rec("fingerprint_jets", "%d" % (sum(r["fp_jets"] for r in res) % 2 ** 64))
    rec("fingerprint_partition", "%d" % (sum(r["fp_part"] for r in res) % 2 ** 64))

    bad = [r["c"] for r in res if not r["part_ok"]]
    rec("partition_exact", "all %d events" % NEV if not bad else "FAILS in chunks %s" % bad)
    if bad:
        die("constituents are not a partition of the input in chunks %s" % bad)
    wr = max(r["worst_ratio"] for r in res)
    rec("conservation_worst_fraction_of_bound", "%.3e" % wr)
    if not wr <= 1.0:
        die("a jet sum misses the event 4-momentum by more than 2(n-1) u sum|x|")
    cb = sum(r["coll_bad"] for r in res)
    rec("collinear_split_events_changed", cb)
    if cb:
        die("splitting the hardest particle changed the jets in %d events" % cb)
    be = sum(r["brute"]["events"] for r in res)
    bm = [g for r in res for g in r["brute"]["partition_mismatch"]]
    brel = max(r["brute"]["max_rel"] for r in res)
    rec("brute_events", be)
    rec("brute_partition_mismatch", "%d %s" % (len(bm), bm[:10]))
    rec("brute_max_diff_in_units_of_n_u_sum", "%.3e" % brel)
    if bm:
        die("the independent anti-k_t disagrees with FastJet on %d events" % len(bm))
    if not brel <= 1.0:
        die("brute-force jet momenta differ by more than n u sum|x|")
    published(lead, taus, ncons, grid)
    det = res[0]["det"]
    rec("determinism_first_chunk", det)
    if not det:
        die("the first chunk clustered twice gave different jets")
    write_score()
    print("CLUSTER OK")


if __name__ == "__main__":
    main()
