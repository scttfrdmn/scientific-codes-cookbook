#!/usr/bin/env python3
"""yoda-real, part 1: fill YODA histograms from all 61.5 M events of CMS Open Data record 12341.

    python3 -u fill.py <workers> <file.root> <root-hist-mass.txt> <root-hist-nmuon.txt> <outdir>

The input is root-real's: Run2012B+C DoubleMuParked NanoAOD reduced on muons, 149.3 M muons.
The file is split into 250,000-event chunks, read with uproot, and each chunk fills its own five YODA
objects in a worker process, as a batch of Rivet jobs would. The chunks are then merged with `+`,
which is what yodamerge does. The objects:

  /CMS/nmuon    Histo1D, 64 bins [0, 64)        nMuon of every event          61.5 M fills
  /CMS/mass     Histo1D, df102's 30,000 bins    opposite-sign dimuon mass     24.1 M fills
  /CMS/pt_mass  Profile1D, 300 log bins         <pT(mu mu)> vs mass           24.1 M fills
  /CMS/mu_pt    Histo1D, 200 log bins           pT of every muon             149.3 M fills
  /CMS/eta_phi  Histo2D, 48 x 64                (eta, phi) of every muon     149.3 M fills

Asserted, on the merged objects unless stated:
  * FILL CONSERVATION -- every object's bins plus its under/overflow hold every fill, exactly,
    and numEntries() == sumW == the fill count (unit weights: integers below 2^53, exact).
  * AN INDEPENDENT BINNING -- every bin, flows included, equals numpy's count of the same values
    on the object's own edges (searchsorted), and its sumWX equals numpy's sequential sum.
  * ROOT, ON THE SAME BYTES -- /CMS/mass and /CMS/nmuon equal root-real's TH1D contents in all
    30,002 and 66 bins, under/overflow included (ROOT's histograms are identical on all chips).
  * LOSSLESS TRANSFER -- serializeContent() in the worker -> deserializeContent() in the parent
    reproduces every bin field bit for bit (the merge is built on it).
  * MERGE -- merged in reverse chunk order: every count identical; float sums reported.
  * TRANSFORMATIONS -- rebinXBy(100) conserves each group of 100 bins exactly; scaleW(k) gives
    sumW == fl(sumW k) bitwise; normalize() within the 2 n u of re-summing n bins.
  * DETERMINISM -- the first chunk filled twice is bit-identical.
Reported, not asserted: the text writer. writeYODA prints %.6e whatever `precision` says, so a
written .yoda is measured against the in-memory object (how many fields change, the worst
relative change), and the bytes for precision=-1 and 17 are compared.
"""
import faulthandler
import hashlib
import os
import resource
import subprocess
import sys
import time
from concurrent.futures import ProcessPoolExecutor

import awkward as ak
import numpy as np
import uproot
import yoda

W, SRC, ROOT_MASS, ROOT_NMU, OUT = int(sys.argv[1]), sys.argv[2], sys.argv[3], sys.argv[4], sys.argv[5]
CHUNK = 250_000      # 1 M-event chunks peaked at 1.66 GiB per worker: 8 of them and the input outgrew 16 GiB
U = 2.0 ** -53
ROWS = []


def rec(k, v):
    v = str(v)
    print("%s\t%s" % (k, v), flush=True)
    ROWS.append((str(k), v))


def write_score():
    with open(os.path.join(OUT, "fill.tsv"), "w") as fh:
        fh.write("observable\tvalue\n")
        for k, v in ROWS:
            fh.write("%s\t%s\n" % (k, v))


def die(msg):
    print("FAIL: %s" % msg, flush=True)
    write_score()
    sys.exit(1)


def check(ok, msg):
    if not bool(ok):
        die(msg)


def ver(mod):
    return str(getattr(mod, "__version__", "unknown"))


MU = ["Muon_pt", "Muon_eta", "Muon_phi", "Muon_mass", "Muon_charge"]
NMU_E = np.arange(65, dtype=np.float64)                    # 64 bins [0, 64)
M_NB, M_LO, M_HI = 30000, 0.25, 300.0                      # df102's binning
P_NB = 300
PT_NB = 200
EP_NX, EP_NY = 48, 64


def book():
    return {
        "/CMS/nmuon": yoda.Histo1D(64, 0.0, 64.0, "/CMS/nmuon"),
        "/CMS/mass": yoda.Histo1D(M_NB, M_LO, M_HI, "/CMS/mass"),
        "/CMS/pt_mass": yoda.Profile1D(list(yoda.logspace(P_NB, M_LO, M_HI)), "/CMS/pt_mass"),
        "/CMS/mu_pt": yoda.Histo1D(list(yoda.logspace(PT_NB, 1.0, 1000.0)), "/CMS/mu_pt"),
        "/CMS/eta_phi": yoda.Histo2D(EP_NX, -2.4, 2.4, EP_NY, -np.pi, np.pi, "/CMS/eta_phi"),
    }


# fields per bin, read back through the public bin API
F1 = ("sumW", "sumW2", "sumWX", "sumWX2", "numEntries")
FP = ("sumW", "sumW2", "sumWX", "sumWX2", "sumWY", "sumWY2", "sumWXY", "numEntries")
F2 = ("sumW", "sumW2", "sumWX", "sumWX2", "sumWY", "sumWY2", "sumWXY", "numEntries")


def fields(o):
    names = FP if "Profile" in o.type() else (F2 if o.dim() == 3 else F1)
    bins = sorted(o.bins(True), key=lambda b: b.index())
    return names, np.array([[getattr(b, n)() for n in names] for b in bins], dtype=np.float64)


def fp(a):
    return hashlib.sha256(np.ascontiguousarray(a).tobytes()).hexdigest()[:16]


def gidx(edges, v):
    """yoda's 1-D global bin index: 0 underflow, 1..n in range, n+1 overflow (half-open bins)."""
    return np.searchsorted(edges, v, side="right")


def init():
    faulthandler.dump_traceback_later(600, repeat=True)   # a stuck worker says where, in the log
    os.environ["OMP_NUM_THREADS"] = "1"


def work(args):
    lo, hi, again = args
    r0 = resource.getrusage(resource.RUSAGE_SELF)
    t0 = time.time()
    with uproot.open(SRC) as fh:      # per chunk: a worker that kept one open grew ~80 MB a chunk
        a = fh["Events"].arrays(["nMuon"] + MU, entry_start=lo, entry_stop=hi)
    t1 = time.time()
    n = ak.to_numpy(a["nMuon"]).astype(np.float64)
    pt_all = ak.to_numpy(ak.flatten(a["Muon_pt"])).astype(np.float64)
    eta_all = ak.to_numpy(ak.flatten(a["Muon_eta"])).astype(np.float64)
    phi_all = ak.to_numpy(ak.flatten(a["Muon_phi"])).astype(np.float64)
    two = a[ak.to_numpy(a["nMuon"]) == 2]
    q = ak.to_numpy(two["Muon_charge"])
    os_ = two[q[:, 0] != q[:, 1]]
    # root-real check.py's formula, character for character: its histogram of these masses equals
    # ROOT's on all three chips (0 bins differ)
    pt, eta, phi, ms = (ak.to_numpy(os_[c]).astype(np.float64) for c in MU[:4])
    px, py, pz = pt * np.cos(phi), pt * np.sin(phi), pt * np.sinh(eta)
    e = np.sqrt(px * px + py * py + pz * pz + ms * ms)
    E, X, Y, Z = e.sum(1), px.sum(1), py.sum(1), pz.sum(1)
    m = np.sqrt(np.maximum(E * E - (X * X + Y * Y + Z * Z), 0.0))
    ptll = np.sqrt(X * X + Y * Y)
    L = {k: v.tolist() for k, v in (("n", n), ("m", m), ("ptll", ptll), ("pt", pt_all),
                                     ("eta", eta_all), ("phi", phi_all))}
    t2 = time.time()
    H = book()
    tf = {}
    s = time.time(); f = H["/CMS/nmuon"].fill
    for x in L["n"]:
        f(x)
    tf["nmuon"] = time.time() - s
    s = time.time(); f = H["/CMS/mass"].fill
    for x in L["m"]:
        f(x)
    tf["mass"] = time.time() - s
    s = time.time(); f = H["/CMS/pt_mass"].fill
    for x, y in zip(L["m"], L["ptll"]):
        f(x, y)
    tf["pt_mass"] = time.time() - s
    s = time.time(); f = H["/CMS/mu_pt"].fill
    for x in L["pt"]:
        f(x)
    tf["mu_pt"] = time.time() - s
    s = time.time(); f = H["/CMS/eta_phi"].fill
    for x, y in zip(L["eta"], L["phi"]):
        f(x, y)
    tf["eta_phi"] = time.time() - s
    t3 = time.time()
    if again:
        return {k: fp(fields(o)[1]) for k, o in H.items()}
    # numpy's reference for the same values, on each object's own edges; bincount sums its weights
    # in input order, which is the order the fills ran
    ref = {}
    for path, x, y in (("/CMS/nmuon", n, None), ("/CMS/mass", m, None), ("/CMS/pt_mass", m, ptll),
                       ("/CMS/mu_pt", pt_all, None)):
        ed = np.asarray(H[path].xEdges(), dtype=np.float64)
        i = gidx(ed, x)
        nb = len(ed) + 1
        terms = {"sumWX": x, "sumWX2": x * x}
        if y is not None:
            terms.update({"sumWY": y, "sumWY2": y * y, "sumWXY": x * y})
        r = {"count": np.bincount(i, minlength=nb)}
        for f, t in terms.items():
            r[f] = np.bincount(i, weights=t, minlength=nb)
            r["abs_" + f] = np.bincount(i, weights=np.abs(t), minlength=nb)
        ref[path] = r
    o2 = H["/CMS/eta_phi"]
    ex, ey = np.asarray(o2.xEdges(), dtype=np.float64), np.asarray(o2.yEdges(), dtype=np.float64)
    g = gidx(ex, eta_all) + (EP_NX + 2) * gidx(ey, phi_all)
    nb2 = (EP_NX + 2) * (EP_NY + 2)
    ref["/CMS/eta_phi"] = {"count": np.bincount(g, minlength=nb2)}
    for f, t in (("sumWX", eta_all), ("sumWY", phi_all), ("sumWXY", eta_all * phi_all)):
        ref["/CMS/eta_phi"][f] = np.bincount(g, weights=t, minlength=nb2)
        ref["/CMS/eta_phi"]["abs_" + f] = np.bincount(g, weights=np.abs(t), minlength=nb2)
    t4 = time.time()
    ser = {}
    for k, o in H.items():
        names, a2 = fields(o)
        ser[k] = (np.asarray(o.serializeContent(), dtype=np.float64), fp(a2))
    r1 = resource.getrusage(resource.RUSAGE_SELF)
    return {"lo": lo, "hi": hi, "ser": ser, "ref": ref, "fills": {k: len(v) for k, v in L.items()},
            "t_read": t1 - t0, "t_prep": t2 - t1, "t_fill": tf, "t_ref": t4 - t3,
            "cpu": (r1.ru_utime - r0.ru_utime) + (r1.ru_stime - r0.ru_stime),
            "rss_gib": r1.ru_maxrss / 1024.0 ** 2}


def main():
    faulthandler.dump_traceback_later(600, repeat=True)
    os.makedirs(OUT, exist_ok=True)
    rec("yoda", yoda.version())
    rec("uproot", ver(uproot))
    rec("awkward", ver(ak))
    rec("numpy", ver(np))
    rec("workers", W)
    N = uproot.open(SRC)["Events"].num_entries
    rec("events", N)
    check(N == 61540413, "record 12341 has 61,540,413 events, the file %d" % N)
    jobs = [(lo, min(lo + CHUNK, N), False) for lo in range(0, N, CHUNK)]
    rec("chunks", len(jobs))
    t0 = time.time()
    print("starting %d workers" % W, flush=True)
    # numpy references, summed over chunks in chunk order -- the order the merge below adds them
    REF = {}
    # ProcessPoolExecutor, not Pool: a worker that dies (OOM) raises BrokenProcessPool here
    # instead of leaving its task unanswered forever
    with ProcessPoolExecutor(W, initializer=init, max_tasks_per_child=4) as pool:
        res = []
        for r in pool.map(work, jobs):
            for k, d in r.pop("ref").items():
                if k not in REF:
                    REF[k] = {f: v.copy() for f, v in d.items()}
                else:
                    for f, v in d.items():
                        REF[k][f] = REF[k][f] + v
            res.append(r)
            if len(res) % 20 == 0 or len(res) == len(jobs):
                print("chunk %d of %d: read %.1f s, fill %.1f s, worker rss %.2f GiB, %.0f s"
                      % (len(res), len(jobs), r["t_read"], sum(r["t_fill"].values()), r["rss_gib"],
                         time.time() - t0), flush=True)
        again = list(pool.map(work, [(jobs[0][0], jobs[0][1], True)]))[0]
    t_pass = time.time() - t0
    rec("wall_s_pass", "%.1f" % t_pass)
    for k, name in (("t_read", "read"), ("t_prep", "prep"), ("t_ref", "numpy_ref"), ("cpu", "workers")):
        rec("cpu_s_sum_" + name, "%.1f" % sum(r[k] for r in res))
    tfill = {k: sum(r["t_fill"][k] for r in res) for k in res[0]["t_fill"]}
    for k, v in tfill.items():
        rec("cpu_s_sum_fill_" + k, "%.1f" % v)
    rec("cpu_s_sum_fill_all", "%.1f" % sum(tfill.values()))
    rec("peak_rss_gib_worker", "%.2f" % max(r["rss_gib"] for r in res))
    fills = {k: sum(r["fills"][k] for r in res) for k in res[0]["fills"]}
    nfill = {"/CMS/nmuon": fills["n"], "/CMS/mass": fills["m"], "/CMS/pt_mass": fills["m"],
             "/CMS/mu_pt": fills["pt"], "/CMS/eta_phi": fills["eta"]}
    for k, v in nfill.items():
        rec("fills_" + k.split("/")[-1], v)
    rec("fills_total", sum(nfill.values()))
    rec("fill_rate_mfills_per_cpu_s", "%.2f" % (sum(nfill.values()) / sum(tfill.values()) / 1e6))
    check(fills["n"] == N and fills["pt"] == 149322456 and fills["m"] == 24067843,
          "fill counts %s: root-real saw 61,540,413 events, 149,322,456 muons, 24,067,843 OS pairs" % fills)

    # ---- lossless transfer and the merge ------------------------------------------------------
    chunks = []
    for i, r in enumerate(res):
        H = book()
        for k, o in H.items():
            o.deserializeContent(r["ser"][k][0].tolist())
            # every chunk: what arrived re-serializes to the same bytes; the first and last also
            # through the bin API, against the worker's own fingerprint of its bins
            check(np.array_equal(np.asarray(o.serializeContent(), dtype=np.float64), r["ser"][k][0]),
                  "%s chunk %d: deserialize -> serialize changed the content" % (k, r["lo"]))
            if i in (0, len(res) - 1):
                check(fp(fields(o)[1]) == r["ser"][k][1], "%s chunk %d: deserializeContent did not "
                      "reproduce the worker's bins bit for bit" % (k, r["lo"]))
        chunks.append(H)
    rec("identity_transfer", "serializeContent -> deserializeContent lossless for %d objects "
        "(bin API fingerprint checked on the first and last chunk)" % (len(chunks) * 5))
    check(all(fp(fields(chunks[0][k])[1]) == v for k, v in again.items()),
          "the first chunk filled twice differs")
    rec("determinism_first_chunk", "identical")
    t1 = time.time()
    M = {k: o.clone() for k, o in chunks[0].items()}
    for H in chunks[1:]:
        for k in M:
            M[k] += H[k]
    t_merge = time.time() - t1
    rec("wall_s_merge", "%.1f" % t_merge)
    R = {k: o.clone() for k, o in chunks[-1].items()}
    for H in reversed(chunks[:-1]):
        for k in R:
            R[k] += H[k]


    # ---- conservation, binning, sums ----------------------------------------------------------
    for k, o in M.items():
        tag = k.split("/")[-1]
        names, a = fields(o)
        col = {nm: a[:, i] for i, nm in enumerate(names)}
        nf = nfill[k]
        check(o.numEntries() == nf and o.sumW() == nf and col["numEntries"].sum() == nf
              and col["sumW"].sum() == nf, "%s: numEntries %r, sumW %r, bins+flows %r, fills %d"
              % (k, o.numEntries(), o.sumW(), col["numEntries"].sum(), nf))
        check(np.array_equal(col["sumW"], col["numEntries"]) and np.array_equal(col["sumW2"], col["sumW"]),
              "%s: a unit-weight bin with sumW != numEntries or sumW2 != sumW" % k)
        cnt = REF[k]["count"]
        check(len(cnt) == len(col["numEntries"]), "%s: %d bins, numpy %d" % (k, len(col["numEntries"]), len(cnt)))
        bad = int((col["numEntries"] != cnt).sum())
        check(bad == 0, "%s: %d bins differ from numpy's count on the same edges" % (k, bad))
        inr = o.sumW(False)
        rec("%s_in_range_flows" % tag, "%d in range, %d in flows, of %d" % (inr, nf - inr, nf))
        for f in [x for x in ("sumWX", "sumWX2", "sumWY", "sumWY2", "sumWXY") if x in REF[k]]:
            y, r = col[f], REF[k][f]
            same = int((y == r).sum())
            # two float64 sums of the same n terms, each term itself rounded once (or fused):
            # |yoda - numpy| <= 3 n u sum|t| (Higham's (n-1) u sum|t| per sum, plus the terms)
            tol = 3.0 * np.maximum(cnt, 1) * U * REF[k]["abs_" + f] + 1e-300
            ok = np.abs(y - r) <= tol
            rec("%s_%s_vs_numpy" % (tag, f), "bit-equal %d of %d bins, worst %.3e of the bound"
                % (same, len(r), float(np.max(np.abs(y - r) / tol))))
            check(ok.all(), "%s %s: a bin outside the summation bound" % (k, f))
    rec("identity_fill_conservation", "all 5 objects: bins + flows == fills; every bin == numpy on the object's edges")
    # fingerprints for the cross-chip comparison: counts (a fixed point if no value moves bin),
    # every bin field (float sums: a path), and the edges YODA computed from (lo, hi, n)
    for k, o in M.items():
        tag = k.split("/")[-1]
        names, a = fields(o)
        ed = [np.asarray(o.xEdges(), dtype=np.float64)]
        if o.dim() == 3:
            ed.append(np.asarray(o.yEdges(), dtype=np.float64))
        rec("fp_counts_" + tag, fp(a[:, names.index("numEntries")]))
        rec("fp_bin_fields_" + tag, fp(a))
        rec("fp_edges_" + tag, fp(np.concatenate(ed)))
    e = np.asarray(M["/CMS/mass"].xEdges(), dtype=np.float64)
    rec("mass_edges_ne_numpy_linspace", "%d of %d" % (int((e != np.linspace(M_LO, M_HI, M_NB + 1)).sum()), len(e)))
    rec("eta_phi_x_edge_at_zero", repr(float(np.asarray(M["/CMS/eta_phi"].xEdges())[24])))

    root_m = np.array([int(x) for x in open(ROOT_MASS).read().split()], dtype=np.int64)
    root_n = np.array([int(x) for x in open(ROOT_NMU).read().split()], dtype=np.int64)
    ym = fields(M["/CMS/mass"])[1][:, F1.index("numEntries")]
    yn = fields(M["/CMS/nmuon"])[1][:, F1.index("numEntries")]
    check(len(root_m) == len(ym) == M_NB + 2, "mass: ROOT %d bins, YODA %d" % (len(root_m), len(ym)))
    rec("mass_bins_differ_from_root", int((ym != root_m).sum()))
    rec("nmuon_bins_differ_from_root", int((yn != root_n).sum()))
    check(np.array_equal(ym, root_m), "the mass histogram differs from ROOT's TH1D in %d bins" % int((ym != root_m).sum()))
    check(np.array_equal(yn, root_n), "the nMuon histogram differs from ROOT's TH1D")
    rec("identity_vs_root", "mass: all 30002 bins incl. under %d / over %d equal ROOT's; nMuon: all 66"
        % (root_m[0], root_m[-1]))

    # ---- merge order --------------------------------------------------------------------------
    nsame = ntot = 0
    for k in M:
        names, a = fields(M[k])
        _, b = fields(R[k])
        ic = names.index("numEntries")
        check(np.array_equal(a[:, ic], b[:, ic]) and np.array_equal(a[:, 0], b[:, 0]),
              "%s: counts depend on the merge order" % k)
        nsame += int((a == b).sum())
        ntot += a.size
    rec("merge_reverse_order", "counts identical; %d of %d bin fields bit-identical" % (nsame, ntot))

    # ---- transformations ----------------------------------------------------------------------
    h = M["/CMS/mass"].clone()
    a = fields(h)[1]
    h.rebinXBy(100)
    b = fields(h)[1]
    check(h.numBins() == 300, "rebinXBy(100) left %d bins" % h.numBins())
    grp = a[1:-1, :].reshape(300, 100, a.shape[1]).sum(1)
    check(np.array_equal(b[1:-1, 4], grp[:, 4]) and np.array_equal(b[[0, -1], 4], a[[0, -1], 4]),
          "rebinXBy(100) did not conserve every group of 100 bins")
    rec("rebinXBy100", "30000 -> 300 bins, each == the sum of its 100, flows unchanged")
    k_lumi = 1.0 / 11.6
    h = M["/CMS/mu_pt"].clone()
    a = fields(h)[1]
    h.scaleW(k_lumi)
    b = fields(h)[1]
    check(np.array_equal(b[:, 0], a[:, 0] * k_lumi), "scaleW: sumW != fl(sumW k)")
    s2a, s2b = a[:, 1] * (k_lumi * k_lumi), (a[:, 1] * k_lumi) * k_lumi
    rec("scaleW_sumW2", "== sumW2 (k k) in %d, == (sumW2 k) k in %d, of %d bins"
        % (int((b[:, 1] == s2a).sum()), int((b[:, 1] == s2b).sum()), len(b)))
    check(np.array_equal(b[:, 4], a[:, 4]), "scaleW changed numEntries")
    check(np.array_equal(b[:, 1], s2a), "scaleW: sumW2 != fl(sumW2 fl(k k))")
    rec("scaleW", "sumW == fl(sumW k) and sumW2 == fl(sumW2 fl(k k)) bitwise in all bins; numEntries unchanged")
    rec("normalize_doc", (yoda.Histo1D.normalize.__doc__ or "").strip().split("\n")[0])
    h = M["/CMS/mass"].clone()
    h.normalize(1.0)
    # normalize scales by 1/S, S an n-term sum, and integral() re-sums n scaled terms: each sum is
    # within (n-1) u of exact and each scaled term within u, so |integral - 1| <= 2 n u
    nb = h.numBins(True)
    dev = abs(h.integral(True) - 1.0)
    rec("normalize_integral_with_flows", "%r: %.1f ULP of 1, %.3f of the 2 n u bound (n = %d)"
        % (h.integral(True), dev / 2.0 ** -52, dev / (2 * nb * U), nb))
    rec("normalize_integral_in_range", "%r" % h.integral(False))
    check(dev <= 2 * nb * U, "normalize(1.0): integral with flows %r" % h.integral(True))

    # ---- the text writer (reported) -----------------------------------------------------------
    objs = list(M.values())
    t2 = time.time()
    yoda.write(objs, os.path.join(OUT, "cms.yoda.gz"))
    rec("wall_s_write_gz", "%.2f" % (time.time() - t2))
    yoda.writeYODA(objs, "/tmp/w-1.yoda", precision=-1)
    yoda.writeYODA(objs, "/tmp/w17.yoda", precision=17)
    h1 = hashlib.sha256(open("/tmp/w-1.yoda", "rb").read()).hexdigest()
    h17 = hashlib.sha256(open("/tmp/w17.yoda", "rb").read()).hexdigest()
    rec("writer_precision_17_bytes_equal_default", h1 == h17)
    t2 = time.time()
    back = yoda.read("/tmp/w17.yoda")
    rec("wall_s_read", "%.2f" % (time.time() - t2))
    ch = tot = 0
    worst = 0.0
    big_counts_lost = 0
    for k, o in M.items():
        names, a = fields(o)
        _, b = fields(back[k])
        ch += int((a != b).sum())
        tot += a.size
        nz = a != 0
        worst = max(worst, float(np.max(np.abs(a[nz] - b[nz]) / np.abs(a[nz]))))
        ic = names.index("numEntries")
        big_counts_lost += int(((a[:, ic] != b[:, ic]) & (a[:, ic] >= 1e7)).sum())
        check(int(((a[:, ic] != b[:, ic]) & (a[:, ic] < 1e7)).sum()) == 0,
              "%s: a count below 1e7 changed in the text round trip" % k)
        ed = [(np.asarray(o.xEdges(), dtype=np.float64), np.asarray(back[k].xEdges(), dtype=np.float64))]
        if o.dim() == 3:
            ed.append((np.asarray(o.yEdges(), dtype=np.float64), np.asarray(back[k].yEdges(), dtype=np.float64)))
        rec("text_roundtrip_edges_changed_" + k.split("/")[-1],
            "%d of %d" % (sum(int((x != y).sum()) for x, y in ed), sum(len(x) for x, _ in ed)))
    rec("text_roundtrip", "%d of %d bin fields changed; worst relative %.2e; counts >= 1e7 changed %d"
        % (ch, tot, worst, big_counts_lost))
    check(worst <= 5e-7 * (1 + 1e-9), "the text round trip moved a value by %.3e, more than %%.6e allows" % worst)

    # yodamerge, the CLI a batch of Rivet jobs is merged with, over per-chunk files
    os.makedirs("/var/tmp/chunks", exist_ok=True)   # root disk: the tmpfs is half the RAM
    names = []
    for i, H in enumerate(chunks):
        fn = "/var/tmp/chunks/c%03d.yoda" % i
        yoda.writeYODA(list(H.values()), fn)
        names.append(fn)
    hp = subprocess.run(["yodamerge", "--help"], capture_output=True, text=True)
    with open(os.path.join(OUT, "yodamerge-help.txt"), "w") as fh:
        fh.write(hp.stdout + hp.stderr)
    for tag, extra in (("default", []),):
        t2 = time.time()
        p = subprocess.run(["yodamerge"] + extra + ["-o", "/var/tmp/ym.yoda"] + names, capture_output=True, text=True)
        rec("wall_s_yodamerge_" + tag, "%.1f" % (time.time() - t2))
        if p.returncode != 0:
            rec("yodamerge_" + tag, "exit %d: %s" % (p.returncode, (p.stderr or p.stdout)[-300:].replace("\n", " ")))
            continue
        ym = yoda.read("/var/tmp/ym.yoda")
        dc = dfl = 0
        for k, o in M.items():
            names_, a = fields(o)
            _, b = fields(ym[k])
            ic = names_.index("numEntries")
            dc += int((a[:, ic] != b[:, ic]).sum())
            dfl += int((a != b).sum())
        rec("yodamerge_%s_vs_in_memory" % tag, "%d count bins differ, %d bin fields differ" % (dc, dfl))
    rec("wall_s_fill_py", "%.1f" % (time.time() - t0))
    faulthandler.cancel_dump_traceback_later()
    write_score()
    print("FILL OK")


if __name__ == "__main__":
    main()
