#!/usr/bin/env python3
"""PyCBC over the whole GWOSC hour around GW150914 (4096 s, H1 and L1), blind: a non-spinning
IMRPhenomD bank, PyCBC's own conditioning (the GW150914 tutorial's: 15 Hz high-pass, 2048 Hz, a
4 s median-Welch PSD truncated to 4 s), matched filter, power chi-squared and newSNR on the
loudest triggers. Asserts the loudest newSNR of the hour is GW150914 in each detector.
    search.py <data dir> <out dir> <worker processes>"""
import json
import multiprocessing as mp
import os
import sys
import time

import numpy as np

from gwpy.timeseries import TimeSeries as GwpyTS
from pycbc.events.ranking import newsnr
from pycbc.filter import highpass, matched_filter, resample_to_delta_t, sigma
from pycbc.psd import interpolate, inverse_spectrum_truncation
from pycbc.types import TimeSeries
from pycbc.vetoes import power_chisq
from pycbc.waveform import get_fd_waveform

D, OUT, NP = sys.argv[1], sys.argv[2], int(sys.argv[3])
EVENT = 1126259462.4          # GWOSC's GPS for GW150914
FLOW, CROP, NBINS = 20.0, 16, 16
FILES = {"H1": "H-H1_LOSC_4_V1-1126256640-4096.hdf5", "L1": "L-L1_LOSC_4_V1-1126256640-4096.hdf5"}
M = np.round(10 * 1.05 ** np.arange(43), 3)            # 10 .. 77.9 Msun, 5% steps
BANK = [(a, b) for i, a in enumerate(M) for b in M[: i + 1]]
FAIL = []
G = {}


def check(ok, msg):
    ok = bool(ok)
    print(("ok    " if ok else "FAIL  ") + msg, flush=True)
    if not ok:
        FAIL.append(msg)


def condition(det):
    ts = GwpyTS.read(os.path.join(D, FILES[det]), format="hdf5.gwosc")
    v = np.asarray(ts.value, dtype=np.float64)
    good = np.isfinite(v)
    # GWOSC marks samples outside the detector's science segments as NaN: analyse the contiguous
    # valid stretch that contains the event, as a search analyses each science segment
    e = int(round((EVENT - ts.t0.value) / ts.dt.value))
    check(good[e], "%s: the event lies in valid data" % det)
    bad = np.flatnonzero(~good)
    a = int(bad[bad < e].max()) + 1 if np.any(bad < e) else 0
    b = int(bad[bad > e].min()) if np.any(bad > e) else len(v)
    summary["detectors"][det] = {"nan_samples": int(len(bad)), "analysed_s": (b - a) * ts.dt.value,
                                 "analysed_from": ts.t0.value + a * ts.dt.value}
    print("%s: %d of %d samples NaN; analysing the valid stretch of %.1f s" % (det, len(bad), len(v),
                                                                           (b - a) * ts.dt.value), flush=True)
    x = TimeSeries(v[a:b], delta_t=ts.dt.value, epoch=ts.t0.value + a * ts.dt.value)
    s = resample_to_delta_t(highpass(x, 15.0), 1.0 / 2048).crop(2, 2)
    psd = s.psd(4)
    psd = interpolate(psd, s.delta_f)
    psd = inverse_spectrum_truncation(psd, int(4 * s.sample_rate), low_frequency_cutoff=15)
    return s, psd


def template(m1, m2, n, df):
    hp, _ = get_fd_waveform(approximant="IMRPhenomD", mass1=m1, mass2=m2, f_lower=FLOW, delta_f=df)
    hp.resize(n)
    return hp


def filt(k):
    """Per-1-second-window peak |rho| and its sample index, for bank entry k."""
    m1, m2 = BANK[k]
    st, psd, t0, fs = G["stilde"], G["psd"], G["t0"], G["fs"]
    hp = template(m1, m2, len(st), st.delta_f)
    rho = abs(matched_filter(hp, st, psd=psd, low_frequency_cutoff=FLOW).numpy())
    a, b = int(CROP * fs), len(rho) - int(CROP * fs)
    r = rho[a:b]
    w = int(fs)
    nw = len(r) // w
    r = r[: nw * w].reshape(nw, w)
    j = r.argmax(axis=1)
    return k, r[np.arange(nw), j].astype(np.float32), (a + np.arange(nw) * w + j).astype(np.int64), \
        float(np.mean(r ** 2)), float(np.median(r ** 2))


def chisq_at(args):
    k, idx = args
    m1, m2 = BANK[k]
    st, psd = G["stilde"], G["psd"]
    hp = template(m1, m2, len(st), st.delta_f)
    snr = matched_filter(hp, st, psd=psd, low_frequency_cutoff=FLOW)
    chi = power_chisq(hp, st, NBINS, psd, low_frequency_cutoff=FLOW)
    rc = float(chi[idx]) / (2 * NBINS - 2)
    rho = float(abs(snr[idx]))
    return k, idx, rho, rc, float(newsnr(rho, rc))


summary = {"bank_size": len(BANK), "detectors": {}}
t_all = time.time()
for det in ("H1", "L1"):
    t = time.time()
    s, psd = condition(det)
    G.update(stilde=s.to_frequencyseries(), psd=psd, t0=float(s.start_time), fs=float(s.sample_rate))
    print("%s conditioned in %.1f s: %d samples at %.0f Hz from %.1f" % (det, time.time() - t, len(s),
                                                                     s.sample_rate, s.start_time), flush=True)
    t = time.time()
    with mp.get_context("fork").Pool(NP) as pool:
        res = sorted(pool.map(filt, range(len(BANK)), chunksize=4))
    print("%s: %d templates filtered in %.1f s" % (det, len(BANK), time.time() - t), flush=True)
    peaks = np.array([r[1] for r in res])           # templates x windows
    where = np.array([r[2] for r in res])
    best_k = peaks.argmax(axis=0)
    win_snr = peaks.max(axis=0)
    win_idx = where[best_k, np.arange(peaks.shape[1])]
    win_t = G["t0"] + win_idx / G["fs"]
    mean2 = np.array([r[3] for r in res])
    med2 = np.array([r[4] for r in res])
    on = int(np.argmin(np.abs(win_t - EVENT)))
    # 20 loudest windows by raw SNR (always including the on-source one), then chi-squared on each
    top = list(np.argsort(win_snr)[::-1][:20])
    if on not in top:
        top.append(on)
    t = time.time()
    with mp.get_context("fork").Pool(NP) as pool:
        ch = pool.map(chisq_at, [(int(best_k[w]), int(win_idx[w])) for w in top])
    print("%s: chi-squared on %d triggers in %.1f s" % (det, len(top), time.time() - t), flush=True)
    trig = [{"t": float(win_t[w]), "snr": c[2], "rchisq": c[3], "newsnr": c[4],
             "m1": float(BANK[c[0]][0]), "m2": float(BANK[c[0]][1])} for w, c in zip(top, ch)]
    trig.sort(key=lambda x: -x["newsnr"])
    on_t = [x for x in trig if abs(x["t"] - EVENT) < 0.5][0]
    loud_snr = max(trig, key=lambda x: x["snr"])
    off = [x for x in trig if abs(x["t"] - EVENT) >= 0.5]
    summary["detectors"][det].update({"on_source": on_t, "loudest_raw_snr": loud_snr, "loudest_off_source_newsnr": off[0],
                                      "triggers": trig, "mean_rho2_median_template": float(np.median(mean2)),
                                      "median_rho2_over_2ln2_median_template": float(np.median(med2) / (2 * np.log(2)))})
    print("%s on-source: t=%.4f snr=%.2f rchisq=%.2f newsnr=%.2f template %.2f+%.2f" % (
        det, on_t["t"], on_t["snr"], on_t["rchisq"], on_t["newsnr"], on_t["m1"], on_t["m2"]), flush=True)
    print("%s loudest raw SNR of the hour: t=%.3f snr=%.2f newsnr=%.2f; loudest off-source newsnr %.2f at %.3f (snr %.2f, rchisq %.2f)" % (
        det, loud_snr["t"], loud_snr["snr"], loud_snr["newsnr"], off[0]["newsnr"], off[0]["t"], off[0]["snr"], off[0]["rchisq"]), flush=True)
    print("%s noise: mean |rho|^2 %.3f, median |rho|^2 / (2 ln 2) %.3f (median over templates)" % (
        det, np.median(mean2), np.median(med2) / (2 * np.log(2))), flush=True)
    check(abs(trig[0]["t"] - EVENT) < 0.5, "%s: the loudest newSNR of the hour is GW150914 (%.3f at %.3f)"
          % (det, trig[0]["newsnr"], trig[0]["t"]))
    check(on_t["rchisq"] < 2.0, "%s: GW150914 is signal-consistent, reduced chi^2 %.2f < 2" % (det, on_t["rchisq"]))
    check(np.isfinite(on_t["snr"]) and on_t["snr"] > 8, "%s: on-source SNR %.2f > 8" % (det, on_t["snr"]))
dt = summary["detectors"]["L1"]["on_source"]["t"] - summary["detectors"]["H1"]["on_source"]["t"]
summary["L1_minus_H1_s"] = dt
check(abs(dt) < 0.0101, "L1 - H1 arrival %.2f ms within the 10.0 ms light travel time" % (dt * 1e3))
summary["wall_s"] = time.time() - t_all
import resource  # noqa: E402
summary["maxrss_gib_main"] = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 2 ** 20
summary["maxrss_gib_largest_worker"] = resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss / 2 ** 20
print("maxrss GiB: main %.2f, largest worker %.2f" % (summary["maxrss_gib_main"], summary["maxrss_gib_largest_worker"]), flush=True)
with open(os.path.join(OUT, "search.json"), "w") as fh:
    json.dump(summary, fh, indent=1)
if FAIL:
    sys.exit("SEARCH FAILED: %d checks" % len(FAIL))
print("SEARCH OK")
