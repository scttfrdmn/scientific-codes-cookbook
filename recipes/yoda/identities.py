#!/usr/bin/env python3
"""YODA on Graviton4: histogram bookkeeping held to exact identities, cross-checked against numpy.

YODA is Rivet's analysis-object format, so what it must get right is accounting: every fill
counted once, every transformation conserving what it should, every write/read lossless.
All of that is exact, and one half of it is checkable against an independent implementation
(numpy.histogram) rather than against YODA itself.

THREE QUANTITIES THAT ARE NOT THE SAME, each measured before anything was asserted. Getting
these confused is how a correct library fails a wrong assertion:

 1. `h.numEntries()` counts EVERY fill -- 5000 here, including the 1444 that land outside
    [0,10). Summing the bins gives 3556, the in-range fills. yoda 2.x exposes no
    `underflow`/`overflow`/`totalDbn`, and `bins(includeFlows=True)` raises, so the only
    correct identity is: sum over bins == in-range fills.
 2. `xMean`/`xStdDev` describe the WHOLE fill distribution, out-of-range included -- they
    match numpy on all 5000 values (uniform(-2,12): mean 5.0, sd 4.0415), not on the
    in-range subset, where they look 41% wrong. Both numbers are right; they answer
    different questions.
 3. `copy.deepcopy` raises on these Cython objects ("self._ptr cannot be converted to a
    Python object for pickling"). The API's own `clone()` is the copy.

Run with `python3 -u` so a killed run still has its output in command.log.
"""
import os
import sys

import numpy as np
import yoda

ROWS = []


def rec(k, v):
    print("%s\t%s" % (k, v))
    ROWS.append((k, v))


def die(msg):
    print("FAIL: %s" % msg)
    sys.exit(1)


rec("yoda", yoda.version())
rec("numpy", np.__version__)

NBINS, LO, HI = 10, 0.0, 10.0
SEED, NFILL = 20261011, 5000
rng = np.random.default_rng(SEED)
# Deliberately wider than the binning, so the in-range/total distinction is exercised
# rather than hidden. A fixture that fitted inside the axis would make identity 1 vacuous.
DATA = rng.uniform(-2.0, 12.0, NFILL)
INSIDE = int(((DATA >= LO) & (DATA < HI)).sum())
rec("fills", "%d" % NFILL)
rec("fills_in_range", "%d" % INSIDE)
rec("fills_out_of_range", "%d" % (NFILL - INSIDE))
if not (0 < INSIDE < NFILL):
    die("the fixture puts %d of %d fills in range; it must straddle the axis so the "
        "in-range identity is not vacuous" % (INSIDE, NFILL))


def build(path="/h"):
    h = yoda.Histo1D(NBINS, LO, HI, path)
    for x in DATA:
        h.fill(x)
    return h


h = build()
rec("num_bins", "%d" % h.numBins())
if h.numBins() != NBINS:
    die("asked for %d bins, got %d" % (NBINS, h.numBins()))

# ------------------------- 1. the accounting identity, with the right two quantities
bin_sum_w = sum(b.sumW() for b in h.bins())
bin_sum_n = sum(b.numEntries() for b in h.bins())
rec("total_numEntries", "%.0f" % h.numEntries())
rec("sum_over_bins_numEntries", "%.0f" % bin_sum_n)
rec("sum_over_bins_sumW", "%.6f" % bin_sum_w)
if h.numEntries() != float(NFILL):
    die("numEntries is %.0f but %d values were filled; it counts every fill, in range or "
        "not" % (h.numEntries(), NFILL))
if bin_sum_n != float(INSIDE):
    die("the bins hold %.0f entries but %d fills are in range; yoda 2.x has no flow bins "
        "to absorb the rest, so these must be equal" % (bin_sum_n, INSIDE))
if bin_sum_w != float(INSIDE):
    die("the bins' weights sum to %.6f, not the %d in-range fills (all weights are 1)"
        % (bin_sum_w, INSIDE))
rec("accounting", "numEntries counts all %d fills; the bins hold the %d in range"
    % (NFILL, INSIDE))

# ------------- 2. the cross-check: an independent binning implementation on the same data
yc = np.array([b.numEntries() for b in h.bins()], dtype="int64")
nc, edges = np.histogram(DATA, bins=NBINS, range=(LO, HI))
rec("yoda_counts", ",".join(str(v) for v in yc))
rec("numpy_counts", ",".join(str(v) for v in nc))
diff = int(np.abs(yc - nc).max())
rec("yoda_vs_numpy_max_diff", "%d" % diff)
if diff != 0:
    die("yoda and numpy.histogram disagree by %d counts on identical data and identical "
        "edges; these are independent binning implementations and must agree exactly"
        % diff)
rec("cross_validation_binning", "all %d bins identical to numpy.histogram" % NBINS)

# THE CONTROL: the comparison must be able to fail. Shifted data must not match.
nc_shift, _ = np.histogram(DATA + 1.0, bins=NBINS, range=(LO, HI))
ctl = int(np.abs(yc - nc_shift).max())
rec("control_shifted_data_max_diff", "%d" % ctl)
if ctl == 0:
    die("binning data shifted by a whole bin width produced identical counts, so the "
        "agreement above is not sensitive to the data at all")

# ---------------------------------------------- 3. write and read back without loss
yoda.write([h], "h.yoda")
size = os.path.getsize("h.yoda")
back = yoda.read("h.yoda")
rec("file_bytes", "%d" % size)
rec("file_keys", ",".join(sorted(back.keys())))
if "/h" not in back:
    die("the round-trip lost the object path: keys %s" % sorted(back.keys()))
h2 = back["/h"]
same = (h2.sumW() == h.sumW() and h2.numEntries() == h.numEntries()
        and [b.sumW() for b in h2.bins()] == [b.sumW() for b in h.bins()]
        and [b.numEntries() for b in h2.bins()] == [b.numEntries() for b in h.bins()])
rec("roundtrip", "identical" if same else "DIFFERS")
if not same:
    die("writing and re-reading the histogram changed it")

# ------------------- 4. scaling: sumW by k, sumW2 by k^2, entry count untouched
K = 3.0
s = h.clone()
s.scaleW(K)
r_w = s.sumW() / h.sumW()
r_w2 = s.sumW2() / h.sumW2()
rec("scaleW_factor", "%g" % K)
rec("scaleW_sumW_ratio", "%.15f" % r_w)
rec("scaleW_sumW2_ratio", "%.15f" % r_w2)
rec("scaleW_numEntries_unchanged", "%s" % (s.numEntries() == h.numEntries()))
if r_w != K:
    die("scaleW(%g) changed sumW by %.15f, not exactly %g" % (K, r_w, K))
if r_w2 != K * K:
    die("scaleW(%g) changed sumW2 by %.15f; weights are squared there so it must be %g"
        % (K, r_w2, K * K))
if s.numEntries() != h.numEntries():
    die("scaling the weights changed the entry count; a reweight is not a refill")

# ------------------------------------- 5. addition doubles weights and entries exactly
t = h.clone() + h.clone()
rec("add_sumW_ratio", "%.15f" % (t.sumW() / h.sumW()))
rec("add_numEntries_ratio", "%.15f" % (t.numEntries() / h.numEntries()))
if t.sumW() != 2.0 * h.sumW() or t.numEntries() != 2.0 * h.numEntries():
    die("h + h gave sumW %.6f and numEntries %.0f; both should be exactly doubled"
        % (t.sumW(), t.numEntries()))
if not all(a.sumW() == 2.0 * b.sumW() for a, b in zip(t.bins(), h.bins())):
    die("h + h did not double every bin")

# ------------------------------- 6. rebinning conserves what is inside the axis, exactly
for fn, arg, label in (("rebinXBy", 2, "merge pairs"),
                       ("rebinXTo", [0.0, 2.0, 5.0, 10.0], "explicit edges")):
    r = h.clone()
    before = sum(b.sumW() for b in r.bins())
    getattr(r, fn)(arg)
    after = sum(b.sumW() for b in r.bins())
    rec("rebin_%s" % fn, "%d -> %d bins, sum %.6f -> %.6f (%s)"
        % (h.numBins(), r.numBins(), before, after, label))
    if after != before:
        die("%s changed the in-range total from %.6f to %.6f; rebinning regroups bins, "
            "it does not create or destroy weight" % (fn, before, after))
    if r.numBins() >= h.numBins():
        die("%s did not reduce the bin count (%d -> %d)" % (fn, h.numBins(), r.numBins()))
rec("conservation_rebinning", "both rebinnings conserve the in-range sum exactly")

# ------------------------------------------------------ 7. normalisation is exact
n = h.clone()
n.normalize(1.0)
# NOT asserted with ==, and the reason is worth recording: normalize divides by a sum of
# 3556 terms, so the result lands one ULP low -- 0.99999999999999989. A probe printing
# "%.15f" showed 1.000000000000000 and I asserted exact equality from that formatted
# value, which failed the run. A printed number at fixed precision is not a bit-level
# claim. The bound below is ~4.5 ULP of 1.0, from float64 and the division.
ULP = sys.float_info.epsilon                  # 2.220446049250313e-16
dev = abs(n.integral() - 1.0)
rec("normalize_integral", "%.17g" % n.integral())
rec("normalize_deviation", "%.3e (%.1f ULP)" % (dev, dev / ULP))
if dev > 1e-15:
    die("normalize(1.0) gave an integral of %.17g, %.1f ULP from 1.0; a single division "
        "by a 3556-term sum should land within a few ULP"
        % (n.integral(), dev / ULP))

# ------- 8. moments describe ALL fills, not the binned subset -- compared accordingly
full_mean, full_sd = float(DATA.mean()), float(DATA.std(ddof=0))
ins = DATA[(DATA >= LO) & (DATA < HI)]
rec("yoda_xMean", "%.15f" % h.xMean())
rec("numpy_mean_all_fills", "%.15f" % full_mean)
rec("numpy_mean_in_range_only", "%.15f" % float(ins.mean()))
# yoda's xStdDev is the SAMPLE standard deviation (ddof=1), not the population one.
# Measured: yoda 4.029955989911502 against numpy ddof=0 4.029552974160717, a ratio of
# sqrt(N/(N-1)). Comparing against ddof=0 looks like a 1e-4 disagreement and is not one --
# the same ddof trap that bit the ROOT recipe in this catalog.
full_sd1 = float(DATA.std(ddof=1))
rec("yoda_xStdDev", "%.15f" % h.xStdDev())
rec("numpy_std_all_fills_ddof0", "%.15f" % full_sd)
rec("numpy_std_all_fills_ddof1", "%.15f" % full_sd1)
d_sd1 = abs(h.xStdDev() - full_sd1) / full_sd1
d_sd0 = abs(h.xStdDev() - full_sd) / full_sd
rec("std_rel_diff_vs_ddof1", "%.3e" % d_sd1)
rec("std_rel_diff_vs_ddof0", "%.3e" % d_sd0)
if d_sd1 > 1e-12:
    die("yoda's xStdDev %.15f does not match numpy's sample sd (ddof=1) %.15f (rel "
        "%.3e); if it matched ddof=0 instead the convention has changed"
        % (h.xStdDev(), full_sd1, d_sd1))
if d_sd0 < d_sd1:
    die("xStdDev is closer to the population sd than the sample sd, so the ddof=1 "
        "identification above is wrong and the page would misstate the convention")
rec("ddof_convention", "xStdDev is the SAMPLE sd (ddof=1); ddof=0 is %.1e away" % d_sd0)
d_mean = abs(h.xMean() - full_mean) / abs(full_mean)
rec("mean_rel_diff_vs_all_fills", "%.3e" % d_mean)
if d_mean > 1e-12:
    die("yoda's xMean %.15f does not match the mean of all %d fills (%.15f); it tracks "
        "the whole distribution, not the binned subset" % (h.xMean(), NFILL, full_mean))
# The in-range mean is recorded, not asserted: it is a DIFFERENT quantity and comparing
# against it made yoda look 1.1e-02 out on the mean and 41% out on the width.
rec("moments_note", "xMean/xStdDev cover all fills; the in-range mean differs by %.1e"
    % (abs(h.xMean() - float(ins.mean())) / abs(float(ins.mean()))))

# ------------------------------------------------------------------ 9. determinism
again = build()
det = (again.numEntries() == h.numEntries()
       and [b.sumW() for b in again.bins()] == [b.sumW() for b in h.bins()])
rec("determinism", "identical" if det else "DIFFERS")
if not det:
    die("filling the same values twice gave different histograms")

with open("score.tsv", "w") as fh:
    fh.write("observable\tvalue\n")
    for k, v in ROWS:
        fh.write("%s\t%s\n" % (k, v))

print("YODA OK")
