#!/usr/bin/env python3
"""ObsPy checks: the Java TauP reference first, then exact I/O and signal identities.

Staged as a pinned input rather than inlined in the TaskSpec because a spawn task command
travels in EC2 user data, capped at 16,384 bytes. Run with `python3 -u` so a killed task still
has this output in command.log.
"""
import os
import sys

import numpy as np
import obspy

out = {}


def rec(k, v):
    out[k] = v
    print("%s\t%s" % (k, v))


def fail(msg):
    sys.exit("FAIL: %s" % msg)


rec("obspy_version", getattr(obspy, "__version__", "unknown"))
rec("numpy_version", getattr(np, "__version__", "unknown"))

# =============================================================================================
# 1. THE CROSS-IMPLEMENTATION REFERENCE.
#
# obspy's taup is a Python reimplementation of the original Java TauP (Crotwell et al.), and
# obspy commits the Java tool's OWN output as test data. Reproducing it is therefore both a
# published-reference check and a cross-code check against an unrelated codebase in another
# language -- strictly stronger than any identity obspy could satisfy on its own.
#
# The filenames encode the Java command line, e.g.
#   taup_time_-h_10_-ph_ttall_-deg_35   ->  taup_time -h 10 -ph ttall -deg 35
# =============================================================================================
from obspy.taup import TauPyModel   # noqa: E402


def parse_taup_table(path):
    """Rows of Java TauP's `taup_time` table: (phase, time, ray_param, takeoff, incident).

    Columns are whitespace separated; header lines are skipped by requiring the first field to
    be a number, which is more robust than counting header rows.
    """
    rows = []
    for line in open(path):
        f = line.split()
        if len(f) < 8:
            continue
        try:
            dist = float(f[0])
            depth = float(f[1])
        except ValueError:
            continue
        try:
            rows.append({"phase": f[2], "time": float(f[3]), "p": float(f[4]),
                         "takeoff": float(f[5]), "incident": float(f[6]),
                         "dist": dist, "depth": depth})
        except ValueError:
            continue
    return rows


# The reference prints travel time to 0.01 s, so two IDENTICAL computations can differ by up to
# half a quantum in the printed value. One quantum is therefore the tightest bound that can be
# asserted -- the tolerance is set by the reference's precision, not by how close the two
# happen to land.
TIME_TOL, P_TOL = 0.01, 0.01
total_ref = total_matched = 0
worst_time = worst_p = 0.0

for model_name, fname in (("iasp91", "taup_time_-h_10_-ph_ttall_-deg_35"),
                          ("ak135", "taup_time_-h_10_-ph_ttall_-deg_35_-mod_ak135")):
    ref = parse_taup_table(fname)
    if len(ref) < 20:
        fail("parsed only %d arrivals from %s -- the table format changed" % (len(ref), fname))
    depth = ref[0]["depth"]
    dist = ref[0]["dist"]
    rec("ref_%s_arrivals" % model_name, len(ref))
    rec("ref_%s_geometry" % model_name, "depth %.1f km, distance %.2f deg" % (depth, dist))

    model = TauPyModel(model=model_name)
    got = model.get_travel_times(source_depth_in_km=depth, distance_in_degree=dist,
                                 phase_list=["ttall"])
    rec("obspy_%s_arrivals" % model_name, len(got))

    # Match each REFERENCE arrival to the nearest obspy arrival of the SAME phase name. The
    # claim asserted is "obspy reproduces everything Java TauP reported"; an extra branch found
    # by obspy is reported, not failed, because it is not evidence either tool is wrong.
    # Phase names repeat (PP arrives five times here), so each obspy arrival is consumed once.
    used = set()
    unmatched = []
    for r in ref:
        best, bestd = None, None
        for i, a in enumerate(got):
            if i in used or a.name != r["phase"]:
                continue
            d = abs(a.time - r["time"])
            if bestd is None or d < bestd:
                best, bestd = i, d
        if best is None or bestd > TIME_TOL:
            unmatched.append((r["phase"], r["time"], bestd))
            continue
        used.add(best)
        a = got[best]
        worst_time = max(worst_time, bestd)
        pa = getattr(a, "ray_param_sec_degree", None)
        if pa is not None:
            worst_p = max(worst_p, abs(pa - r["p"]))
        total_matched += 1
    total_ref += len(ref)

    if unmatched:
        for ph, t, d in unmatched[:8]:
            print("   unmatched  %-8s ref %.2f s  nearest obspy diff %s"
                  % (ph, t, "none" if d is None else "%.4f s" % d))
        fail("%d of %d %s arrivals from Java TauP were not reproduced"
             % (len(unmatched), len(ref), model_name))
    rec("match_%s" % model_name, "all %d Java TauP arrivals reproduced" % len(ref))
    extra = len(got) - len(ref)
    rec("obspy_%s_extra_arrivals" % model_name, extra)

rec("taup_reference_arrivals_total", total_ref)
rec("taup_reference_matched_total", total_matched)
rec("taup_max_time_difference_s", "%.4f" % worst_time)
rec("taup_max_ray_param_difference", "%.4f" % worst_p)
if total_matched != total_ref:
    fail("matched %d of %d reference arrivals" % (total_matched, total_ref))
if worst_p > P_TOL:
    fail("ray parameter differs by %.4f s/deg, tolerance %.4f" % (worst_p, P_TOL))
rec("identity_taup",
    "obspy reproduces all %d Java TauP arrivals across 2 models to %.2f s" % (total_ref, TIME_TOL))

# =============================================================================================
# 2. FORMAT ROUND-TRIP: exact, with no tolerance at all.
#
# A waveform library's first duty is not to alter data it is only storing. Each encoding below
# represents its dtype exactly, so a round-trip must return BIT-IDENTICAL samples -- not close
# samples. Four MiniSEED encodings and SAC are checked, because "it round-trips" is a property
# of the encoding, not of the library.
# =============================================================================================
cases = [("MSEED", "int32", "STEIM2"), ("MSEED", "int32", None),
         ("MSEED", "float32", None), ("MSEED", "float64", None),
         ("SAC", "float32", None)]
rt_ok = 0
for fmt, dtype, enc in cases:
    st = obspy.read()
    for tr in st:
        tr.data = tr.data.astype(dtype)
    if fmt == "SAC":
        st = obspy.Stream([st[0]])          # SAC holds one trace per file
    path = "/tmp/rt_%s_%s_%s" % (fmt, dtype, enc or "default")
    kw = {"format": fmt}
    if enc:
        kw["encoding"] = enc
    st.write(path, **kw)
    back = obspy.read(path)
    if len(back) != len(st):
        fail("%s/%s: wrote %d traces, read %d" % (fmt, dtype, len(st), len(back)))
    for a, b in zip(st, back):
        if not np.array_equal(np.asarray(a.data), np.asarray(b.data)):
            d = float(np.max(np.abs(a.data.astype("float64") - b.data.astype("float64"))))
            fail("%s/%s/%s is not bit-identical after a round-trip (max|diff| %.3e)"
                 % (fmt, dtype, enc or "default", d))
        if np.asarray(b.data).dtype != np.dtype(dtype):
            fail("%s/%s came back as %s" % (fmt, dtype, np.asarray(b.data).dtype))
    rt_ok += 1
    print("   round-trip  %-6s %-8s enc=%-8s bit-identical, dtype preserved"
          % (fmt, dtype, enc or "default"))
rec("roundtrip_cases_exact", "%d of %d" % (rt_ok, len(cases)))
if rt_ok != len(cases):
    fail("only %d of %d round-trips were exact" % (rt_ok, len(cases)))
rec("identity_roundtrip", "every encoding returns bit-identical samples and dtype")

# =============================================================================================
# 3. LTI SUPERPOSITION: an exact invariance, which beats a band on any single filtered trace.
#
# A bandpass filter claims to be linear and time-invariant. Linearity is checkable with no
# reference value at all: filtering a linear combination must equal the same combination of the
# filtered parts. An implementation that normalised per-trace, or leaked state between calls,
# could not satisfy this however reasonable its output looked.
# =============================================================================================
ALPHA, BETA = 3.7, -1.9
src = obspy.read()
x, y = src[0], src[1]
x.data = x.data.astype("float64")
y.data = y.data.astype("float64")


def bandpassed(tr):
    t = tr.copy()
    t.filter("bandpass", freqmin=1.0, freqmax=10.0, corners=4, zerophase=True)
    return np.asarray(t.data, dtype="float64")


comb = x.copy()
comb.data = ALPHA * x.data + BETA * y.data
lhs = bandpassed(comb)
rhs = ALPHA * bandpassed(x) + BETA * bandpassed(y)
scale = float(np.max(np.abs(rhs)))
if scale <= 0:
    fail("the filtered signal is identically zero -- the check would be vacuous")
lin_rel = float(np.max(np.abs(lhs - rhs))) / scale
rec("superposition_scale", "%.4e" % scale)
rec("superposition_relative_error", "%.3e" % lin_rel)
# Float64 roundoff through a 4-corner zero-phase filter, nothing more.
if lin_rel > 1e-10:
    fail("filter superposition breaks at relative %.3e" % lin_rel)
rec("identity_superposition",
    "bandpass(%.1fx + %.1fy) == %.1f*bandpass(x) + %.1f*bandpass(y)" % (ALPHA, BETA, ALPHA, BETA))

# =============================================================================================
# 4. PARSEVAL: energy is conserved between the time and frequency domains.
# =============================================================================================
d = np.asarray(obspy.read()[0].data, dtype="float64")
d = d - d.mean()
n = len(d)
E_t = float(np.sum(d ** 2))
F = np.fft.rfft(d)
w = np.ones(len(F))
w[1:(-1 if n % 2 == 0 else None)] = 2.0      # interior bins count twice in a one-sided sum
E_f = float(np.sum(w * np.abs(F) ** 2) / n)
if E_t <= 0:
    fail("the trace has no energy -- the check would be vacuous")
par_rel = abs(E_t - E_f) / E_t
rec("parseval_time_energy", "%.10e" % E_t)
rec("parseval_spectral_energy", "%.10e" % E_f)
rec("parseval_relative_error", "%.3e" % par_rel)
if par_rel > 1e-12:
    fail("Parseval's identity breaks at relative %.3e" % par_rel)
rec("identity_parseval", "time-domain and spectral energy agree to %.3e" % par_rel)

# =============================================================================================
# 5. SPLIT / MERGE CONSERVATION, and the trace's own time arithmetic.
# =============================================================================================
orig = obspy.read()[0]
n = orig.stats.npts
pieces = obspy.Stream()
for lo, hi in ((0, n // 3), (n // 3, 2 * n // 3), (2 * n // 3, n)):
    p = orig.copy()
    p.data = orig.data[lo:hi]
    p.stats.starttime = orig.stats.starttime + lo / orig.stats.sampling_rate
    pieces += p
pieces.merge(method=1)
if len(pieces) != 1:
    fail("three contiguous pieces merged into %d traces, not 1" % len(pieces))
if pieces[0].stats.npts != n:
    fail("merge changed the sample count: %d -> %d" % (n, pieces[0].stats.npts))
if not np.array_equal(np.asarray(orig.data), np.asarray(pieces[0].data)):
    fail("merged samples are not bit-identical to the original")
rec("merge_npts", n)
rec("identity_merge", "split into 3 and merged returns bit-identical samples")

lhs_t = orig.stats.endtime - orig.stats.starttime
rhs_t = (orig.stats.npts - 1) / orig.stats.sampling_rate
rec("time_span_s", "%.12f" % lhs_t)
rec("time_arithmetic_difference", "%.3e" % abs(lhs_t - rhs_t))
if abs(lhs_t - rhs_t) > 1e-9:
    fail("endtime-starttime disagrees with (npts-1)/rate by %.3e" % abs(lhs_t - rhs_t))
rec("identity_time", "endtime == starttime + (npts-1)/rate")

with open("score.tsv", "w") as fh:
    fh.write("observable\tvalue\n")
    for k, v in out.items():
        fh.write("%s\t%s\n" % (k, v))
print("OBSPY OK")
