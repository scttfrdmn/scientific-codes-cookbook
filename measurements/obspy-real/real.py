"""ObsPy on a real network-day: every CI broadband (HH?) channel on 2019-07-06, Ridgecrest Mw 7.1.

Per station (all its HH channels), in a worker pool:
  decode    every MiniSEED record; decoded samples == sum of the record headers' nsamp, parsed here
            independently of libmseed
  roundtrip re-encode every trace STEIM2 and decode it again: bit-identical, dtype preserved
  time      endtime - starttime == (npts-1)/rate for every trace
  split     cut the longest trace into three and merge: bit-identical, npts unchanged
  stage 1   evalresp (C) vs a numpy evaluation of the stage-1 poles and zeros, 200 frequencies
  ppsd      McNamara-Buland PPSD over the day; histogram total == n_psd * n_period_bins
  Z only    remove_response to velocity on the longest contiguous trace; superposition and Parseval
            on the 8.6M-sample result
Then TauP (iasp91, ak135) against the SCSN analyst picks for the mainshock: reported, not asserted.

Writes channels.tsv (one row per channel), results.tsv (one row per check) and a final sentinel.
"""
import glob
import hashlib
import io
import os
import resource
import struct
import sys
import threading
import time
import warnings
from multiprocessing import Pool

import numpy as np

DATA = os.environ.get("OBSPY_DIR", "/tmp/day187")
THREADS = int(os.environ.get("OBSPY_THREADS", "8"))
LIMIT = int(os.environ.get("OBSPY_LIMIT", "0"))  # stations; 0 = all
DAY0 = "2019-07-06T00:00:00"
PRE_FILT = (0.005, 0.01, 40.0, 45.0)
A, B = 3.7, -1.9  # superposition weights

warnings.filterwarnings("ignore")


def ver(mod):
    return str(getattr(mod, "__version__", "unknown"))


# ---------------------------------------------------------------- independent header parse
def header_nsamp(raw):
    """Sum nsamp over the data records of a MiniSEED 2 file, reading only the fixed header and
    blockette 1000 -- no libmseed. Returns (records, samples, record_lengths)."""
    off, recs, nsamp, lens = 0, 0, 0, set()
    n = len(raw)
    while off + 48 <= n:
        h = raw[off:off + 48]
        if h[6:7] not in (b"D", b"R", b"Q", b"M"):
            raise ValueError("not a data record at byte %d" % off)
        # byte order: the year field must be plausible
        bo = ">" if 1900 <= struct.unpack(">H", h[20:22])[0] <= 2100 else "<"
        ns = struct.unpack(bo + "H", h[30:32])[0]
        boff = struct.unpack(bo + "H", h[46:48])[0]
        reclen = None
        while boff:
            btype, bnext = struct.unpack(bo + "HH", raw[off + boff:off + boff + 4])
            if btype == 1000:
                reclen = 2 ** raw[off + boff + 6]
                break
            boff = bnext
        if reclen is None:
            raise ValueError("no blockette 1000 at byte %d" % off)
        recs += 1
        nsamp += ns
        lens.add(reclen)
        off += reclen
    if off != n:
        raise ValueError("trailing %d bytes" % (n - off))
    return recs, nsamp, sorted(lens)


def stage1_check(resp, freqs):
    """evalresp on stage 1 alone vs the stage evaluated here from its poles and zeros.

    A StationXML stage carries two normalisations: A0 makes |A0 prod(s-z)/prod(s-p)| == 1 at the
    normalisation frequency, and the stage gain G is defined at its OWN frequency. So the stage is
    G * T(f) / |T(f_gain)| with T = A0 prod(s-z)/prod(s-p); where the two frequencies differ, the
    naive A0 * G * prod/prod is off by the constant 1/|T(f_gain)|. Returns (type, rel, naive_rel).
    """
    st1 = resp.response_stages[0]
    kind = st1.pz_transfer_function_type
    if kind == "LAPLACE (RADIANS/SECOND)":
        w = 2j * np.pi
    elif kind == "LAPLACE (HERTZ)":
        w = 1j
    else:
        return kind, np.nan, np.nan

    def T(f):
        s = w * np.atleast_1d(np.asarray(f, dtype=float))
        h = np.full(s.shape, st1.normalization_factor, dtype=complex)
        for zz in st1.zeros:
            h *= s - complex(zz)
        for pp in st1.poles:
            h /= s - complex(pp)
        return h

    naive = st1.stage_gain * T(freqs)
    h = naive / abs(T(st1.stage_gain_frequency)[0])
    ev = resp.get_evalresp_response_for_frequencies(
        freqs, output="DEF", start_stage=1, end_stage=1,
        hide_sensitivity_mismatch_warning=True)
    return (kind, float(np.max(np.abs(ev - h)) / np.max(np.abs(h))),
            float(np.max(np.abs(ev - naive)) / np.max(np.abs(h))))


def parseval(v):
    n = len(v)
    e_t = float(np.sum(v * v))
    X = np.fft.rfft(v)
    w = np.full(len(X), 2.0)
    w[0] = 1.0
    if n % 2 == 0:
        w[-1] = 1.0
    e_f = float(np.sum(w * (X.real ** 2 + X.imag ** 2)) / n)
    return e_t, abs(e_t - e_f) / e_t


def sha(arr):
    return hashlib.sha256(np.ascontiguousarray(arr).tobytes()).hexdigest()[:16]


# ---------------------------------------------------------------- per station
def station(args):
    sta, files = args
    from obspy import read, read_inventory, Stream, UTCDateTime
    from obspy.signal import PPSD
    from obspy.signal.spectral_estimation import get_nlnm, get_nhnm

    out, timing = [], {}

    def tick(k, t0):
        timing[k] = timing.get(k, 0.0) + time.time() - t0

    t0 = time.time()
    inv = read_inventory(os.path.join(DATA, "stationxml", "CI_%s.xml" % sta))
    tick("stationxml", t0)
    day0 = UTCDateTime(DAY0)
    noon = day0 + 43200
    freqs = np.logspace(-3, np.log10(50.0), 200)
    nlnm_p, nlnm_db = get_nlnm()
    nhnm_p, nhnm_db = get_nhnm()

    for path in files:
        r = {"file": os.path.basename(path)}
        t0 = time.time()
        raw = open(path, "rb").read()
        recs, hdr_ns, lens = header_nsamp(raw)
        tick("header", t0)
        t0 = time.time()
        st = read(io.BytesIO(raw), format="MSEED")
        tick("decode", t0)
        r["id"] = st[0].id
        r["records"] = recs
        r["reclen"] = ",".join(map(str, lens))
        r["hdr_samples"] = hdr_ns
        r["dec_samples"] = int(sum(tr.stats.npts for tr in st))
        r["traces"] = len(st)
        r["dtype"] = str(st[0].data.dtype)
        r["dec_sha"] = sha(np.concatenate([tr.data for tr in st]))
        r["gaps"] = len(st.get_gaps())

        # time arithmetic
        r["time_err_s"] = max(abs((tr.stats.endtime - tr.stats.starttime)
                                  - (tr.stats.npts - 1) / tr.stats.sampling_rate) for tr in st)

        # STEIM2 round trip of every trace, all of the day
        t0 = time.time()
        enc = "STEIM2" if all(tr.data.dtype == np.int32 for tr in st) else None
        buf = io.BytesIO()
        st.write(buf, format="MSEED", encoding=enc) if enc else st.write(buf, format="MSEED")
        buf.seek(0)
        back = read(buf, format="MSEED")
        ok = len(back) == len(st) and all(
            np.array_equal(a.data, b.data) and a.data.dtype == b.data.dtype
            and a.stats.starttime == b.stats.starttime and a.stats.npts == b.stats.npts
            for a, b in zip(st, back))
        r["roundtrip"] = "STEIM2" if (ok and enc) else ("ok" if ok else "FAIL")
        tick("roundtrip", t0)

        # split/merge on the longest trace
        t0 = time.time()
        longest = max(st, key=lambda tr: tr.stats.npts)
        n = longest.stats.npts
        i1, i2 = n // 3 + 17, 2 * n // 3 - 29
        dt = longest.stats.delta
        t_s = longest.stats.starttime
        pieces = Stream([longest.slice(t_s, t_s + (i1 - 1) * dt),
                         longest.slice(t_s + i1 * dt, t_s + (i2 - 1) * dt),
                         longest.slice(t_s + i2 * dt)])
        merged = pieces.merge(method=1)
        r["split_merge"] = ("ok" if len(merged) == 1 and merged[0].stats.npts == n
                            and np.array_equal(merged[0].data, longest.data) else "FAIL")
        r["longest_npts"] = n
        tick("split_merge", t0)

        # response: the epoch must cover the whole day
        t0 = time.time()
        sel = inv.select(network=longest.stats.network, station=longest.stats.station,
                         location=longest.stats.location, channel=longest.stats.channel)
        chans = [c for net in sel for s in net for c in s
                 if c.start_date <= day0 and (c.end_date is None or c.end_date >= day0 + 86400)]
        r["epochs_covering_day"] = len(chans)
        resp = chans[0].response if len(chans) == 1 else inv.get_response(longest.id, noon)
        kind, rel, naive = stage1_check(resp, freqs)
        r["stage1_type"] = kind.split()[1].strip("()") if " " in kind else kind
        r["stage1_rel"] = rel
        r["stage1_naive_rel"] = naive
        sens = resp.instrument_sensitivity
        gprod = float(np.prod([stg.stage_gain for stg in resp.response_stages]))
        r["gain_product_over_sens"] = gprod / sens.value
        tick("response_eval", t0)

        # PPSD over the day
        t0 = time.time()
        pp = PPSD(longest.stats, metadata=resp)
        pp.add(st)
        npsd = len(pp._times_processed)
        r["n_psd"] = npsd
        if npsd:
            pp.calculate_histogram()
            hist = pp.current_histogram
            r["hist_total"] = int(hist.sum())
            r["hist_expected"] = npsd * len(pp.period_bin_centers)
            psd = np.array(pp._binned_psds, dtype=np.float64)
            r["psd_sha"] = sha(np.array(pp._binned_psds))
            lo = np.interp(np.log10(pp.period_bin_centers), np.log10(nlnm_p), nlnm_db)
            hi = np.interp(np.log10(pp.period_bin_centers), np.log10(nhnm_p), nhnm_db)
            inside = (psd >= lo) & (psd <= hi)
            r["frac_in_nlnm_nhnm"] = float(inside.mean())
        else:
            r["hist_total"] = r["hist_expected"] = 0
            r["psd_sha"] = "-"
            r["frac_in_nlnm_nhnm"] = np.nan
        tick("ppsd", t0)

        # Z channels: the full-day response removal and the identities on its output
        if longest.stats.channel.endswith("Z"):
            t0 = time.time()
            cont = st.copy().merge(method=1, fill_value=None).split()
            tr = max(cont, key=lambda t: t.stats.npts)
            x = tr.data.astype(np.float64)
            y = x[::-1].copy()

            def rr(d):
                t = tr.copy()
                t.data = d.copy()
                t.stats.response = resp
                t.remove_response(output="VEL", pre_filt=PRE_FILT, water_level=60)
                return t.data

            vx = rr(x)
            tick("remove_response", t0)
            t0 = time.time()
            vy = rr(y)
            vc = rr(A * x + B * y)
            ref = A * vx + B * vy
            r["superpos_rel"] = float(np.max(np.abs(vc - ref)) / np.max(np.abs(ref)))
            tick("superposition", t0)
            t0 = time.time()
            e_t, r["parseval_rel"] = parseval(vx)
            r["energy"] = repr(e_t)
            r["pgv_m_s"] = repr(float(np.max(np.abs(vx))))
            r["vel_sha"] = sha(vx)
            r["vel_npts"] = len(vx)
            tick("parseval", t0)
        out.append(r)

    return sta, out, timing, resource.getrusage(resource.RUSAGE_SELF).ru_maxrss


# ---------------------------------------------------------------- memory sampler
PEAK = {"used_gib": 0.0}


def sampler():
    while True:
        m = {}
        for line in open("/proc/meminfo"):
            k, v = line.split(":")
            m[k] = int(v.split()[0])
        used = (m["MemTotal"] - m["MemAvailable"]) / 2 ** 20
        PEAK["used_gib"] = max(PEAK["used_gib"], used)
        time.sleep(1.0)


# ---------------------------------------------------------------- TauP vs analyst picks
def taup_vs_picks(path):
    from obspy import UTCDateTime
    from obspy.geodetics import gps2dist_azimuth, kilometers2degrees
    from obspy.taup import TauPyModel
    lines = open(path).read().splitlines()
    h = lines[0].split()
    lat, lon, dep = float(h[4]), float(h[5]), float(h[6])
    picks = []
    for ln in lines[1:]:
        f = ln.split()
        if len(f) < 13 or f[7] not in ("P", "S"):
            continue
        picks.append((f[0], f[1], f[2], float(f[4]), float(f[5]), f[7], float(f[11]), float(f[12])))
    rows = []
    for model in ("iasp91", "ak135"):
        m = TauPyModel(model=model)
        for net, sta, cha, slat, slon, ph, dkm, tt in picks:
            d_m, _, _ = gps2dist_azimuth(lat, lon, slat, slon)
            deg = kilometers2degrees(d_m / 1000.0)
            plist = ["p", "P", "Pn"] if ph == "P" else ["s", "S", "Sn"]
            arr = m.get_travel_times(source_depth_in_km=dep, distance_in_degree=deg,
                                     phase_list=plist)
            if not arr:
                continue
            rows.append((model, ph, net, sta, cha, dkm, d_m / 1000.0, tt, arr[0].time))
    return (lat, lon, dep, len(picks)), rows


# ---------------------------------------------------------------- main
def main():
    import obspy
    import scipy
    t_all = time.time()
    threading.Thread(target=sampler, daemon=True).start()
    print("obspy", ver(obspy), "numpy", ver(np), "scipy", ver(scipy), "python", sys.version.split()[0])
    files = sorted(glob.glob(os.path.join(DATA, "mseed", "*.ms")))
    by = {}
    for f in files:
        by.setdefault(os.path.basename(f)[2:7].strip("_"), []).append(f)
    jobs = sorted(by.items())
    if LIMIT:
        jobs = jobs[:LIMIT]
    print("files %d, stations %d, workers %d" % (sum(len(v) for _, v in jobs), len(jobs), THREADS))

    t0 = time.time()
    results, timing, maxrss = [], {}, 0
    with Pool(THREADS, maxtasksperchild=8) as pool:
        for i, (sta, out, tm, rss) in enumerate(pool.imap_unordered(station, jobs, chunksize=1)):
            results.extend(out)
            for k, v in tm.items():
                timing[k] = timing.get(k, 0.0) + v
            maxrss = max(maxrss, rss)
            if (i + 1) % 20 == 0:
                print("  %d/%d stations, %.0f s" % (i + 1, len(jobs), time.time() - t0), flush=True)
    t_pool = time.time() - t0
    results.sort(key=lambda r: r["file"])

    keys = []
    for r in results:
        for k in r:
            if k not in keys:
                keys.append(k)
    with open("channels.tsv", "w") as fh:
        fh.write("\t".join(keys) + "\n")
        for r in results:
            fh.write("\t".join(str(r.get(k, "")) for k in keys) + "\n")

    t0 = time.time()
    (elat, elon, edep, npick), trows = taup_vs_picks(os.path.join(DATA, "38457511.phase"))
    t_taup = time.time() - t0
    with open("taup_picks.tsv", "w") as fh:
        fh.write("model\tphase\tnet\tsta\tcha\tdist_km_scsn\tdist_km_obspy\ttt_pick\ttt_taup\n")
        for t in trows:
            fh.write("\t".join(repr(v) if isinstance(v, float) else str(v) for v in t) + "\n")

    # ------------------------------------------------------------ checks
    checks = []
    fails = 0

    def check(name, ok, observed):
        nonlocal fails
        ok = bool(ok)
        fails += 0 if ok else 1
        checks.append((name, "ok" if ok else "FAIL", observed))
        print("%-4s %-58s %s" % ("ok" if ok else "FAIL", name, observed), flush=True)

    def obs(name, observed):
        checks.append((name, "reported", observed))
        print("     %-58s %s" % (name, observed), flush=True)

    R = results
    Z = [r for r in R if "vel_sha" in r]
    n_ch = len(R)
    expected = sum(len(v) for _, v in jobs)
    check("channels processed == files staged", n_ch == expected, "%d of %d" % (n_ch, expected))
    check("ids unique", len({r["id"] for r in R}) == n_ch, "%d" % len({r["id"] for r in R}))
    tot_h = sum(r["hdr_samples"] for r in R)
    tot_d = sum(r["dec_samples"] for r in R)
    check("decoded samples == record-header nsamp, every file",
          all(r["hdr_samples"] == r["dec_samples"] for r in R),
          "%d == %d (%d records)" % (tot_d, tot_h, sum(r["records"] for r in R)))
    check("STEIM2 round trip bit-identical, every trace",
          all(r["roundtrip"] == "STEIM2" for r in R),
          "%d of %d files (%d traces)" % (sum(r["roundtrip"] == "STEIM2" for r in R), n_ch,
                                          sum(r["traces"] for r in R)))
    terr = max(r["time_err_s"] for r in R)
    check("endtime - starttime == (npts-1)/rate, every trace (<= 1 ns)", terr <= 1e-9, "%.3e s" % terr)
    check("split into 3 + merge bit-identical, every channel",
          all(r["split_merge"] == "ok" for r in R), "%d of %d" % (sum(r["split_merge"] == "ok" for r in R), n_ch))
    check("exactly one channel epoch covers the day, every channel",
          all(r["epochs_covering_day"] == 1 for r in R),
          "%d of %d" % (sum(r["epochs_covering_day"] == 1 for r in R), n_ch))
    s1 = np.array([r["stage1_rel"] for r in R], dtype=float)
    check("stage 1: evalresp == numpy poles/zeros (rel < 1e-10), every channel",
          np.all(np.isfinite(s1)) and np.all(s1 < 1e-10), "max %.3e over %d" % (np.nanmax(s1), np.isfinite(s1).sum()))
    nv = np.array([r["stage1_naive_rel"] for r in R], dtype=float)
    obs("stage 1: naive A0*G (gain frequency ignored), channels off by > 1e-10",
        "%d of %d, worst %.3e" % (int(np.sum(nv > 1e-10)), n_ch, np.nanmax(nv)))
    sr = np.array([r["gain_product_over_sens"] for r in R])
    obs("product of stage gains / reported sensitivity (metadata)", "min %.6f max %.6f" % (sr.min(), sr.max()))
    check("PPSD histogram total == n_psd * n_period_bins, every channel",
          all(r["hist_total"] == r["hist_expected"] for r in R),
          "%d PSDs" % sum(r["n_psd"] for r in R))
    check("PPSD: at least one hour-long PSD, every channel", all(r["n_psd"] > 0 for r in R),
          "min %d max %d" % (min(r["n_psd"] for r in R), max(r["n_psd"] for r in R)))
    fr = np.array([r["frac_in_nlnm_nhnm"] for r in R], dtype=float)
    obs("PSD values inside Peterson NLNM..NHNM (median over channels)", "%.3f" % np.nanmedian(fr))
    if Z:
        sp = np.array([r["superpos_rel"] for r in Z])
        pv = np.array([r["parseval_rel"] for r in Z])
        en = np.array([float(r["energy"]) for r in Z])
        check("velocity traces have energy (identities are not vacuous)", np.all(en > 0) and np.all(np.isfinite(en)),
              "%d Z channels, min %.3e" % (len(Z), en.min()))
        check("remove_response superposition (rel < 1e-10), every Z channel",
              np.all(np.isfinite(sp)) and np.all(sp < 1e-10), "max %.3e" % sp.max())
        check("Parseval on the velocity trace (rel < 1e-12), every Z channel",
              np.all(np.isfinite(pv)) and np.all(pv < 1e-12), "max %.3e" % pv.max())
        pg = max(Z, key=lambda r: float(r["pgv_m_s"]))
        obs("peak ground velocity, HHZ", "%s %.4f m/s" % (pg["id"], float(pg["pgv_m_s"])))
        obs("velocity samples processed", "%d" % sum(r["vel_npts"] for r in Z))
    obs("gaps (sum over channels)", "%d" % sum(r["gaps"] for r in R))
    obs("mainshock hypocentre (SCSN)", "%.4f %.4f %.2f km, %d P/S picks" % (elat, elon, edep, npick))
    if trows:
        dd = np.array([abs(t[5] - t[6]) for t in trows])
        obs("epicentral distance: SCSN vs obspy WGS84 geodesic, max |diff|", "%.3f km" % dd.max())
        for model in ("iasp91", "ak135"):
            for ph in ("P", "S"):
                res = np.array([t[7] - t[8] for t in trows if t[0] == model and t[1] == ph])
                if len(res):
                    obs("TauP %s %s residual pick - model (median, MAD, n)" % (model, ph),
                        "%+.3f s, %.3f s, %d" % (np.median(res), np.median(np.abs(res - np.median(res))), len(res)))

    tsum = " ".join("%s=%.0f" % (k, v) for k, v in sorted(timing.items()))
    obs("worker seconds by stage", tsum)
    obs("pool wall", "%.1f s" % t_pool)
    obs("taup wall", "%.1f s" % t_taup)
    obs("peak worker RSS", "%.2f GiB" % (maxrss / 2 ** 20))
    obs("peak system memory used (incl. tmpfs)", "%.2f GiB" % PEAK["used_gib"])
    obs("real.py wall", "%.1f s" % (time.time() - t_all))
    with open("results.tsv", "w") as fh:
        for c in checks:
            fh.write("\t".join(c) + "\n")
    if fails:
        print("FAILED: %d checks" % fails)
        sys.exit(1)
    print("OBSPY REAL OK")


if __name__ == "__main__":
    main()
