#!/usr/bin/env python3
"""root-real, step 2 (uproot + numpy, no ROOT imported): the same file through an independent
reader, and ROOT's output read back.

    python3 -u check.py <threads> <file.root>

uproot is a pure-Python reimplementation of the ROOT format that links no ROOT. Against
/tmp/out/root.tsv (step 1) this asserts:
  * the source file decodes to the SAME BITS in both readers: the event count, sum(nMuon), the
    nMuon distribution and the order-free bit fingerprint of all five muon branches;
  * the Snapshot ROOT wrote (jagged branches, multi-threaded, merged) reads back in uproot to
    exactly the source's selected events -- same count, same fingerprints as the same selection
    made by uproot on the source;
  * per event, numpy's pair mass from those muons (its own formula) agrees with ROOT's
    PtEtaPhiMVector mass to within the double-precision forward-error bound of E^2 - |p|^2,
    |m_R^2 - m_N^2| <= 64 eps (E1+E2)^2 -- an error in decoding, or a swapped column, misses
    it by many orders of magnitude.
Reported, not asserted: how many of the 30,000 histogram bins numpy's histogram of the same
masses puts differently (bin-edge rounding).

Writes /tmp/out/check.tsv.
"""
import os
import resource
import sys
import time
from concurrent.futures import ThreadPoolExecutor

import awkward as ak
import numpy as np
import uproot

T, SRC = int(sys.argv[1]), sys.argv[2]
O = "/tmp/out"
out = {}
M64 = 1 << 64
EPS = float(np.finfo(np.float64).eps)


def rec(k, v):
    v = str(v)
    out[k] = v
    print("%s\t%s" % (k, v), flush=True)


def dump():
    with open(O + "/check.tsv", "w") as fh:
        for k, v in out.items():
            fh.write("%s\t%s\n" % (k, v))


def die(msg):
    dump()
    raise SystemExit("FAIL: %s" % msg)


root = dict(line.rstrip("\n").split("\t", 1) for line in open(O + "/root.tsv"))
rec("uproot_version", uproot.__version__)
rec("awkward_version", ak.__version__)
rec("numpy_version", np.__version__)
MU = ["Muon_pt", "Muon_eta", "Muon_phi", "Muon_mass", "Muon_charge"]
NB_N = 64
ex = ThreadPoolExecutor(T)


def bitsum(jag):
    """sum over muons of uint32 bits * (index in event + 1), mod 2^64 -- analysis.py's rr_bitsum."""
    flat = ak.to_numpy(ak.flatten(jag))
    if flat.dtype.itemsize != 4:
        die("expected a 4-byte branch, got %s" % flat.dtype)
    idx = ak.to_numpy(ak.flatten(ak.local_index(jag))).astype(np.uint64) + np.uint64(1)
    return int(np.sum(flat.view(np.uint32).astype(np.uint64) * idx, dtype=np.uint64))


# ---- 1. the source, decoded by uproot ---------------------------------------------------------
t0 = time.time()
tree = uproot.open(SRC)["Events"]
rec("uproot_types", ";".join("%s:%s" % (c, tree[c].typename) for c in ["nMuon"] + MU))
N = 0
sum_n = 0
hist_n = np.zeros(NB_N + 2, dtype=np.int64)
bits = {c: 0 for c in MU}
bits_os = {c: 0 for c in MU}
n_os = 0
for arr in tree.iterate(["nMuon"] + MU, step_size="400 MB", library="ak",
                        decompression_executor=ex, interpretation_executor=ex):
    n = ak.to_numpy(arr["nMuon"]).astype(np.int64)
    if not bool((ak.to_numpy(ak.num(arr["Muon_pt"])) == n).all()):
        die("a muon branch's length disagrees with nMuon")
    N += len(n)
    sum_n += int(n.sum())
    hist_n[1:NB_N + 1] += np.bincount(n[n < NB_N], minlength=NB_N)
    hist_n[NB_N + 1] += int((n >= NB_N).sum())
    for c in MU:
        bits[c] = (bits[c] + bitsum(arr[c])) % M64
    two = arr[n == 2]
    q = ak.to_numpy(two["Muon_charge"])
    os_ = two[q[:, 0] != q[:, 1]]
    n_os += len(os_)
    for c in MU:
        bits_os[c] = (bits_os[c] + bitsum(os_[c])) % M64
src_s = time.time() - t0
rec("uproot_source_read_s", "%.1f" % src_s)
rec("n_events", N)
rec("sum_nMuon", sum_n)
rec("nmuon_hist", ",".join(map(str, hist_n)))
for c in MU:
    rec("bits_" + c, bits[c])
rec("n_os", n_os)

for k in ["n_events", "sum_nMuon", "nmuon_hist", "n_os"] + ["bits_" + c for c in MU]:
    if root[k] != out[k]:
        die("%s: ROOT says %s, uproot says %s" % (k, root[k], out[k]))
rec("identity_two_readers",
    "ROOT and uproot decode all %d events to the same bits: count, sum(nMuon)=%d, the nMuon "
    "distribution and the fingerprint of all five muon branches" % (N, sum_n))

# ---- 2. ROOT's Snapshot, read back -----------------------------------------------------------
t1 = time.time()
snap = uproot.open("/tmp/os.root")["Events"]
rec("snapshot_types", ";".join("%s:%s" % (c, snap[c].typename) for c in ["nMuon"] + MU + ["Dimuon_mass"]))
NB, LO, HI = 30000, 0.25, 300.0
hist_root_m = np.zeros(NB + 2, dtype=np.int64)    # numpy's binning of ROOT's masses
hist_np_m = np.zeros(NB + 2, dtype=np.int64)      # numpy's binning of numpy's masses
sbits = {c: 0 for c in MU}
ns = 0
worst_ratio = 0.0
worst_rel = 0.0


def binfill(hst, m):
    hst[0] += int((m < LO).sum())
    hst[NB + 1] += int((m >= HI).sum())
    inr = m[(m >= LO) & (m < HI)]
    hst[1:NB + 1] += np.histogram(inr, bins=NB, range=(LO, HI))[0]


for arr in snap.iterate(["nMuon"] + MU + ["Dimuon_mass"], step_size="400 MB", library="ak",
                        decompression_executor=ex, interpretation_executor=ex):
    if not bool((ak.to_numpy(arr["nMuon"]) == 2).all()):
        die("the Snapshot holds an event without exactly two muons")
    ns += len(arr)
    for c in MU:
        sbits[c] = (sbits[c] + bitsum(arr[c])) % M64
    pt, eta, phi, ms = (ak.to_numpy(arr[c]).astype(np.float64) for c in MU[:4])
    px, py, pz = pt * np.cos(phi), pt * np.sin(phi), pt * np.sinh(eta)
    e = np.sqrt(px * px + py * py + pz * pz + ms * ms)
    E, X, Y, Z = e.sum(1), px.sum(1), py.sum(1), pz.sum(1)
    m2n = E * E - (X * X + Y * Y + Z * Z)
    mr = ak.to_numpy(arr["Dimuon_mass"])
    ratio = np.abs(mr * mr - m2n) / (EPS * E * E)
    worst_ratio = max(worst_ratio, float(ratio.max()))
    mn = np.sqrt(np.maximum(m2n, 0.0))
    worst_rel = max(worst_rel, float((np.abs(mr - mn) / mr).max()))
    if not bool(np.isfinite(ratio).all()):
        die("a non-finite mass")
    binfill(hist_root_m, mr)
    binfill(hist_np_m, mn)
rec("uproot_snapshot_read_s", "%.1f" % (time.time() - t1))
rec("snapshot_entries", ns)
for c in MU:
    rec("snapshot_bits_" + c, sbits[c])
if ns != int(root["n_os"]):
    die("the Snapshot holds %d events, ROOT selected %s" % (ns, root["n_os"]))
for c in MU:
    if sbits[c] != bits_os[c]:
        die("%s: the Snapshot's fingerprint %d != the source selection's %d" % (c, sbits[c], bits_os[c]))
rec("identity_snapshot_roundtrip",
    "ROOT's multi-threaded Snapshot of %d events (jagged muon branches) reads back in uproot "
    "bit-identical to the same selection made by uproot on the source" % ns)

rec("mass_worst_dm2_over_eps_E2", "%.3f" % worst_ratio)
rec("mass_worst_relative_diff", "%.3e" % worst_rel)
if not bool(worst_ratio <= 64.0):
    die("ROOT and numpy pair masses differ by %.1f eps (E1+E2)^2, bound 64" % worst_ratio)
rec("identity_mass_two_formulas",
    "per event, numpy's E^2-|p|^2 agrees with ROOT's PtEtaPhiMVector mass to %.2f eps (E1+E2)^2 "
    "(bound 64); worst relative mass difference %.1e" % (worst_ratio, worst_rel))

hr = np.array([int(x) for x in open(O + "/hist-mass.txt").read().split()], dtype=np.int64)
rec("bins_differ_numpy_binning_of_root_masses", int((hr != hist_root_m).sum()))
rec("bins_differ_numpy_masses", int((hr != hist_np_m).sum()))
rec("events_moved_numpy_masses", int(np.abs(hr - hist_np_m).sum() // 2))
if int(hist_root_m.sum()) != ns:
    die("numpy's histogram of ROOT's masses holds %d, not %d" % (int(hist_root_m.sum()), ns))

rec("wall_s_check", "%.1f" % (time.time() - t0))
rec("peak_rss_gib", "%.3f" % (resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0 ** 2))
dump()
print("UPROOT CHECK OK")
