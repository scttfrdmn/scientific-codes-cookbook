#!/usr/bin/env python3
"""ROOT on Graviton, against an independent reader of its own file format.

THE HEADLINE: uproot is a pure-Python reimplementation of the ROOT file format that does not
link ROOT at all. So ROOT-writes/uproot-reads is a genuine cross-implementation check on the
FORMAT, not one library's I/O path exercised twice -- the thing that is unavailable elsewhere in
this catalog when terra meets rasterio over GDAL, or r-arrow meets pyarrow over libarrow.

TWO THINGS THE PROBE SETTLED, both of which would have been bad assertions:

 1. TH1::GetMean/GetStdDev are the UNBINNED statistics. TH1 accumulates raw moments as you
    Fill, so they are computed from the actual values and not from bin centres -- and GetStdDev
    uses the POPULATION form (ddof=0). A first version of this check asserted the opposite,
    having mis-read a probe discrepancy that was really just ddof: ROOT 0.999424 against
    numpy(ddof=1) 0.999924 is exactly a factor sqrt((n-1)/n). The run failed loudly rather than
    passing, which is the point of comparing against a value derived independently in staging.
    So ROOT is compared against numpy on the RAW data, and the BINNED estimate is reported as
    the measured cost of discretisation.
 2. TMath::Erf and TMath::LnGamma are BIT-IDENTICAL to scipy.special, but TMath::BesselI0 is
    only 2.3e-08 -- ROOT uses a polynomial approximation there, about single precision. So erf
    and lgamma are asserted exactly and BesselI0 is reported with its number, rather than one
    loose bound being applied to all three.

Run with `python3 -u`.
"""
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


import ROOT
import scipy
import scipy.special as sp
import uproot

ROOT.gROOT.SetBatch(True)
rec("root", ROOT.gROOT.GetVersion())
rec("uproot", uproot.__version__)
rec("scipy", scipy.__version__)
rec("numpy", np.__version__)

SEED = 20261011
N = 20000
MU, SIGMA = 0.35, 1.25
rng = np.random.default_rng(SEED)
x = rng.normal(MU, SIGMA, N).astype(np.float64)
i = np.arange(N, dtype=np.int64)
w = rng.uniform(0.5, 1.5, N).astype(np.float64)

# ---- 1. the cross-implementation format check ----------------------------------------------
cols = {"x": x, "i": i, "w": w}
ROOT.RDF.FromNumpy(cols).Snapshot("t", "/tmp/out.root", ["x", "i", "w"])
with uproot.open("/tmp/out.root") as f:
    keys = [k.split(";")[0] for k in f.keys()]
    rec("uproot_objects", keys)
    if "t" not in keys:
        die("uproot cannot see the tree ROOT wrote; it found %s" % keys)
    t = f["t"]
    rec("uproot_branches", sorted(t.keys()))
    if sorted(t.keys()) != ["i", "w", "x"]:
        die("branch set is %s" % sorted(t.keys()))
    got = t.arrays(library="np")
    rec("uproot_entries", int(len(got["x"])))
    if len(got["x"]) != N:
        die("uproot read %d entries, ROOT wrote %d" % (len(got["x"]), N))
    for k in ("x", "i", "w"):
        if got[k].dtype != cols[k].dtype:
            die("%s came back as %s, was written as %s" % (k, got[k].dtype, cols[k].dtype))
        if not bool((got[k] == cols[k]).all()):
            die("%s is not bit-identical through the round-trip (max|diff| %.3e)"
                % (k, float(np.abs(got[k] - cols[k]).max())))
rec("identity_format_roundtrip",
    "all %d entries of float64 and int64 branches survive ROOT->uproot bit-identically" % N)

# ---- 2. a histogram partitions its entries -------------------------------------------------
NB, LO, HI = 100, -6.0, 6.0
h = ROOT.TH1D("h", "h", NB, LO, HI)
for v in x:
    h.Fill(float(v))
counts = np.array([h.GetBinContent(b) for b in range(1, NB + 1)])
under, over = h.GetBinContent(0), h.GetBinContent(NB + 1)
rec("hist_entries", int(h.GetEntries()))
rec("hist_underflow_overflow", (under, over))
rec("hist_integral", h.Integral())
rec("hist_sum_of_bins", float(counts.sum()))
if int(h.GetEntries()) != N:
    die("histogram holds %d entries, %d were filled" % (int(h.GetEntries()), N))
if float(counts.sum()) + under + over != float(N):
    die("bins plus under/overflow = %.1f, not %d" % (counts.sum() + under + over, N))
if h.Integral() != float(counts.sum()):
    die("Integral() %.6f != sum of bin contents %.6f" % (h.Integral(), counts.sum()))
rec("identity_partition",
    "Integral() equals the sum of bin contents exactly, and bins plus under/overflow "
    "account for all %d fills" % N)

# ---- 3. ROOT's binned statistics, compared like with like ----------------------------------
# ROOT against numpy on the SAME (raw) values, with the same ddof. This is the like-with-like
# comparison; the binned estimate below is a different quantity.
raw_mean, raw_std = float(x.mean()), float(x.std(ddof=0))
dm = abs(h.GetMean() - raw_mean) / max(1.0, abs(raw_mean))
ds = abs(h.GetStdDev() - raw_std) / raw_std
rec("root_mean", "%.17f" % h.GetMean())
rec("numpy_mean_raw", "%.17f" % raw_mean)
rec("mean_relative_diff", "%.3e" % dm)
rec("root_stddev", "%.17f" % h.GetStdDev())
rec("numpy_std_raw_ddof0", "%.17f" % raw_std)
rec("stddev_relative_diff", "%.3e" % ds)
if dm > 1e-12 or ds > 1e-12:
    die("ROOT's statistics differ from numpy on the same raw values: mean %.3e, std %.3e"
        % (dm, ds))
rec("identity_unbinned_stats",
    "GetMean/GetStdDev match numpy on the raw values to %.1e / %.1e, confirming TH1 keeps "
    "raw moments rather than re-deriving from bins" % (dm, ds))

# Reported, not asserted: what you get if you estimate the same two numbers FROM THE BINS. That
# is a different quantity, and this is the measured size of the discretisation -- useful because
# a histogram read back from a file has only bin contents, so this is what you are left with.
centres = np.array([h.GetBinCenter(b) for b in range(1, NB + 1)])
tw = counts.sum()
binned_mean = float((counts * centres).sum() / tw)
binned_std = float((counts * centres ** 2).sum() / tw - binned_mean ** 2) ** 0.5
rec("binned_estimate_mean", "%.17f" % binned_mean)
rec("binned_estimate_std", "%.17f" % binned_std)
rec("observation_binning_cost",
    "estimating from bin centres instead costs %.3e on the mean and %.3e on the std at "
    "%.2f-wide bins -- a different quantity, not an error"
    % (abs(binned_mean - raw_mean) / abs(raw_mean), abs(binned_std - raw_std) / raw_std,
       (HI - LO) / NB))

# ---- 4. special functions against an independent implementation ----------------------------
# erf and lgamma are asserted EXACTLY because the probe measured them bit-identical. BesselI0 is
# a polynomial approximation in ROOT and is reported, not asserted to the same bar.
exact_pairs = (("Erf", ROOT.TMath.Erf, sp.erf, (0.1, 0.7, 1.5, 3.0, 5.0)),
               ("LnGamma", ROOT.TMath.LnGamma, sp.gammaln, (0.5, 2.5, 7.0, 20.0, 100.0)))
for name, rf, sf, xs in exact_pairs:
    worst = 0.0
    nbit = 0
    for v in xs:
        a, b = float(rf(v)), float(sf(v))
        if a == b:
            nbit += 1
        worst = max(worst, abs(a - b) / max(1.0, abs(b)))
    rec("tmath_%s_vs_scipy" % name, "%.3e  (%d of %d bit-identical)" % (worst, nbit, len(xs)))
    if worst != 0.0:
        die("TMath::%s differs from scipy.special by %.3e" % (name, worst))
rec("identity_special_functions",
    "TMath::Erf and TMath::LnGamma are bit-identical to scipy.special at every point tested")

worst_b = 0.0
for v in (0.3, 1.0, 4.0, 8.0):
    worst_b = max(worst_b, abs(float(ROOT.TMath.BesselI0(v)) - float(sp.i0(v)))
                  / float(sp.i0(v)))
rec("tmath_BesselI0_vs_scipy", "%.3e" % worst_b)
rec("observation_besseli0_precision",
    "REPORTED -- TMath::BesselI0 is a polynomial approximation, %.1e relative against scipy "
    "(about single precision); do not rely on it for double-precision work" % worst_b)
if worst_b > 1e-5:
    die("BesselI0 is %.3e off, far worse than the ~1e-8 measured" % worst_b)

# ---- 5. invariant mass is invariant -------------------------------------------------------
# A physical identity needing no reference: a Lorentz boost cannot change an invariant mass. A
# sign error in the boost matrix cannot satisfy this however plausible the output looks.
v4 = ROOT.Math.PxPyPzEVector(30.0, 40.0, 120.0, 200.0)
m0 = v4.M()
worst_m = 0.0
for bx, by, bz in ((0.3, -0.1, 0.45), (0.0, 0.0, 0.9), (-0.5, 0.2, -0.2)):
    moved = ROOT.Math.Boost(bx, by, bz)(v4)
    worst_m = max(worst_m, abs(moved.M() - m0) / m0)
rec("invariant_mass", "%.17f" % m0)
rec("mass_worst_relative_change_under_boost", "%.3e" % worst_m)
if worst_m > 1e-13:
    die("a boost changed the invariant mass by %.3e relative" % worst_m)
rec("identity_boost_invariance",
    "M = %.6f is unchanged to %.1e relative across three boosts" % (m0, worst_m))

# ---- 6. a fit, bounded by the uncertainty ROOT itself reports -------------------------------
# The data came from a known Gaussian, so the fitted parameters are expected within their own
# errors -- not at the exact values. The bound is taken from the fit, like asserting against a
# sampler's reported MCSE rather than a number chosen to fit.
res = h.Fit("gaus", "QSN")
fitted = {1: ("mean", MU), 2: ("sigma", SIGMA)}
worst_sig = 0.0
for ip, (label, truth) in fitted.items():
    val, err = res.Parameter(ip), res.ParError(ip)
    nsig = abs(val - truth) / err
    worst_sig = max(worst_sig, nsig)
    rec("fit_%s" % label, "%.6f +/- %.6f   truth %.6f   %.2f sigma" % (val, err, truth, nsig))
rec("fit_ndf", int(res.Ndf()))
rec("fit_chi2_per_ndf", "%.4f" % (res.Chi2() / max(1, res.Ndf())))
rec("fit_worst_sigma", "%.3f" % worst_sig)
if worst_sig > 4.0:
    die("a fitted parameter is %.2f sigma from the truth it was generated from" % worst_sig)
rec("identity_fit",
    "mean and sigma recovered within %.2f of ROOT's own reported errors" % worst_sig)

dump()
print("ROOT OK")
