#!/usr/bin/env python3
"""root-real, step 1 (ROOT): the dimuon analysis of ROOT's df102 tutorial over the whole of CMS
Open Data record 12341 -- 61,540,413 events of 2012 DoubleMuParked data -- in ONE multi-threaded
RDataFrame event loop.

    python3 -u analysis.py <threads> <file.root>

Booked on the one loop, so every number below comes from the same pass over the data:
  * the event count, sum(nMuon) and the nMuon distribution;
  * an order-free bit fingerprint of every decoded value of every muon branch,
    sum over muons of (uint32 bits) * (index in event + 1), mod 2^64 -- exact whatever order
    the threads visit the clusters in, so check.py (uproot, no ROOT) can demand equality;
  * df102's selection (exactly two muons, opposite charge), the pair mass in double precision
    with ROOT::Math::PtEtaPhiMVector, and df102's 30,000-bin histogram of it;
  * a lazy Snapshot of the selected events (jagged muon branches + the mass) for check.py to
    read back -- the recipe's format round-trip, now at scale and with variable-length arrays.
Then fits the J/psi, psi(2S), Upsilon(1S) and Z peaks in that histogram.

Writes /tmp/out/root.tsv, /tmp/out/hist-mass.txt, /tmp/out/hist-nmuon.txt; /tmp/os.root.
"""
import os
import resource
import sys
import time

T, SRC = int(sys.argv[1]), sys.argv[2]
O = "/tmp/out"
os.makedirs(O, exist_ok=True)
out = {}


def rec(k, v):
    v = str(v)
    out[k] = v
    print("%s\t%s" % (k, v), flush=True)


def dump():
    with open(O + "/root.tsv", "w") as fh:
        for k, v in out.items():
            fh.write("%s\t%s\n" % (k, v))


def die(msg):
    dump()
    raise SystemExit("FAIL: %s" % msg)


T_START = time.time()
import ROOT  # noqa: E402

ROOT.gROOT.SetBatch(True)
rec("root_version", ROOT.gROOT.GetVersion())
ROOT.EnableImplicitMT(T)
rec("imt_pool_size", ROOT.GetThreadPoolSize())
if ROOT.GetThreadPoolSize() != T:
    die("asked for %d threads, ROOT's pool has %d" % (T, ROOT.GetThreadPoolSize()))

ROOT.gInterpreter.Declare(r"""
#include <cstdint>
#include <cstring>
#include "ROOT/RVec.hxx"
#include "Math/Vector4D.h"
template <typename T>
ULong64_t rr_bitsum(const ROOT::RVec<T>& v) {
  static_assert(sizeof(T) == 4, "4-byte branches only");
  ULong64_t s = 0;
  for (std::size_t j = 0; j < v.size(); ++j) {
    std::uint32_t b; std::memcpy(&b, &v[j], 4);
    s += (ULong64_t)b * (ULong64_t)(j + 1);
  }
  return s;
}
double rr_mass(const ROOT::RVec<float>& pt, const ROOT::RVec<float>& eta,
               const ROOT::RVec<float>& phi, const ROOT::RVec<float>& m) {
  ROOT::Math::PtEtaPhiMVector a(pt[0], eta[0], phi[0], m[0]), b(pt[1], eta[1], phi[1], m[1]);
  return (a + b).M();
}
""")

MU = ["Muon_pt", "Muon_eta", "Muon_phi", "Muon_mass", "Muon_charge"]
df = ROOT.RDataFrame("Events", SRC)
types = {str(c): str(df.GetColumnType(c)) for c in df.GetColumnNames()}
rec("columns", ";".join("%s:%s" % kv for kv in sorted(types.items())))
for c in ["nMuon"] + MU:
    if c not in types:
        die("the file has no %s column" % c)

n_all = df.Count()
sum_n = df.Define("rr_n64", "(ULong64_t)nMuon").Sum["ULong64_t"]("rr_n64")
NB_N = 64
h_n = df.Histo1D(ROOT.RDF.TH1DModel("h_n", "", NB_N, 0, NB_N), "nMuon")
bits = {c: df.Define("rr_b_" + c, "rr_bitsum(%s)" % c).Sum["ULong64_t"]("rr_b_" + c) for c in MU}

d2 = df.Filter("nMuon == 2", "two muons")
n2 = d2.Count()
dos = d2.Filter("Muon_charge[0] != Muon_charge[1]", "opposite charge")
nss = d2.Filter("Muon_charge[0] == Muon_charge[1]", "same charge").Count()
nos = dos.Count()
dm = dos.Define("Dimuon_mass", "rr_mass(Muon_pt, Muon_eta, Muon_phi, Muon_mass)")
NB, LO, HI = 30000, 0.25, 300.0          # df102's binning
h = dm.Histo1D(ROOT.RDF.TH1DModel("Dimuon_mass", "", NB, LO, HI), "Dimuon_mass")
opts = ROOT.RDF.RSnapshotOptions()
opts.fLazy = True
snap = dm.Snapshot("Events", "/tmp/os.root", ["nMuon"] + MU + ["Dimuon_mass"], opts)

r0 = resource.getrusage(resource.RUSAGE_SELF)
t0 = time.time()
N = int(n_all.GetValue())                # triggers the one event loop
loop_s = time.time() - t0
r1 = resource.getrusage(resource.RUSAGE_SELF)
cpu_s = (r1.ru_utime - r0.ru_utime) + (r1.ru_stime - r0.ru_stime)
snap.GetValue()
rec("event_loop_s", "%.2f" % loop_s)
rec("event_loop_cpu_s", "%.2f" % cpu_s)
rec("event_loop_avg_cores", "%.2f" % (cpu_s / loop_s))
rec("event_loop_mevents_per_s", "%.3f" % (N / loop_s / 1e6))
rec("event_loops_run", df.GetNRuns())
if df.GetNRuns() != 1:
    die("expected one event loop for every booked result, ROOT ran %d" % df.GetNRuns())

rec("n_events", N)
rec("sum_nMuon", int(sum_n.GetValue()))
for c in MU:
    rec("bits_" + c, int(bits[c].GetValue()) % (1 << 64))
counts_n = [int(h_n.GetBinContent(b)) for b in range(NB_N + 2)]   # 0 = underflow, NB_N+1 = overflow
rec("nmuon_hist", ",".join(map(str, counts_n)))
with open(O + "/hist-nmuon.txt", "w") as fh:
    fh.write("\n".join(map(str, counts_n)) + "\n")
rec("n_two_muons", int(n2.GetValue()))
rec("n_os", int(nos.GetValue()))
rec("n_ss", int(nss.GetValue()))

# ---- identities on ROOT's own numbers --------------------------------------------------------
if N != 61540413:
    die("%d events; CERN Open Data record 12341 says 61540413" % N)
if sum(counts_n) != N:
    die("the nMuon histogram holds %d entries, the loop saw %d" % (sum(counts_n), N))
if counts_n[0] != 0:
    die("nMuon underflow is %d" % counts_n[0])
if counts_n[-1] == 0 and sum(b * counts_n[b + 1] for b in range(NB_N)) != int(sum_n.GetValue()):
    die("sum(nMuon) %d disagrees with its own distribution" % int(sum_n.GetValue()))
if counts_n[3] != int(n2.GetValue()):
    die("the nMuon==2 bin holds %d, the filter passed %d" % (counts_n[3], int(n2.GetValue())))
if int(nos.GetValue()) + int(nss.GetValue()) != int(n2.GetValue()):
    die("opposite %d + same %d charge != %d two-muon events"
        % (int(nos.GetValue()), int(nss.GetValue()), int(n2.GetValue())))
rec("identity_cutflow", "61540413 events = the nMuon distribution; nMuon==2 bin = the filter; OS + SS = 2-muon")

hc = [int(h.GetBinContent(b)) for b in range(NB + 2)]
with open(O + "/hist-mass.txt", "w") as fh:
    fh.write("\n".join(map(str, hc)) + "\n")
rec("mass_hist_entries", int(h.GetEntries()))
rec("mass_hist_underflow_overflow", "%d,%d" % (hc[0], hc[-1]))
if sum(hc) != int(nos.GetValue()) or int(h.GetEntries()) != int(nos.GetValue()):
    die("mass histogram holds %d (entries %d), %d pairs were selected"
        % (sum(hc), int(h.GetEntries()), int(nos.GetValue())))
rec("identity_mass_partition", "bins + under/overflow = %d selected pairs" % sum(hc))

# ---- the resonances: Gaussian + linear background, each in a window around its PDG mass -------
PDG = [("jpsi", 3.0969, 2.95, 3.25, 0.03), ("psi2s", 3.6861, 3.55, 3.82, 0.035),
       ("ups1s", 9.4603, 9.20, 9.70, 0.08), ("z", 91.1880, 86.0, 96.0, 2.5)]
worst = 0.0
for name, m, lo, hi, s0 in PDG:
    f = ROOT.TF1("f_" + name, "gaus(0)+pol1(3)", lo, hi)
    b_lo, b_hi = h.FindBin(lo), h.FindBin(hi)
    side = 0.5 * (h.GetBinContent(b_lo) + h.GetBinContent(b_hi))
    f.SetParameters(max(1.0, h.GetBinContent(h.FindBin(m)) - side), m, s0, side, 0.0)
    res = h.Fit(f, "QSRN0")
    mu, emu, sg = res.Parameter(1), res.ParError(1), abs(res.Parameter(2))
    dev = (mu - m) / m
    worst = max(worst, abs(dev))
    nsig = h.Integral(h.FindBin(mu - 2 * sg), h.FindBin(mu + 2 * sg))
    rec("fit_" + name, "mean %.6f +/- %.6f  sigma %.6f  status %d  chi2/ndf %.3f  pdg %.4f  dev %+.3e  pairs(+-2s) %d"
        % (mu, emu, sg, res.Status(), res.Chi2() / max(1, res.Ndf()), m, dev, int(nsig)))
    if res.Status() != 0:
        die("the %s fit did not converge (status %d)" % (name, res.Status()))
    if abs(dev) > 0.01:
        die("the %s peak sits at %.5f, %.2f%% from the PDG %.4f" % (name, mu, 100 * dev, m))
rec("identity_resonances", "J/psi, psi(2S), Upsilon(1S) and Z fitted within %.2e of PDG (bound 1e-2)" % worst)

rec("wall_s_analysis", "%.1f" % (time.time() - T_START))
rec("peak_rss_gib", "%.3f" % (resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0 ** 2))
rec("os_root_bytes", os.path.getsize("/tmp/os.root"))
dump()
print("ROOT ANALYSIS OK")
