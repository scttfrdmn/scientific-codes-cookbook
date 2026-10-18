#!/usr/bin/env python3
"""mne-real: one whole MEG session of ds000117 (Wakeman & Henson), the recipe's checks taken to it.

    python3 -u real.py <bids meg dir> <out dir> <run> [<run> ...]

The analysis is the one MNE-BIDS-Pipeline runs on this dataset: per run, read the raw FIF, find
the triggers, Maxwell-filter (SSS, fine calibration and cross-talk, every run moved to run 01's
head position) after marking noisy and flat sensors, band-pass 1-40 Hz; then over the session,
epoch, fit ICA and remove the EOG/ECG components, average three conditions, decode faces from
scrambled faces over time, and fit an equivalent current dipole to the face response.

Each step carries an assertion that holds for a reason other than "it looked right":
  events   == the dataset's own events.tsv, sample for sample and code for code (published truth)
  PSD      Parseval on whole-run real channels; FIR linear on real channels
  EEG      the average reference sums to zero over the 70 real electrodes
  SSS      linear in the data; its output has rank == the internal moments it kept (nfree)
  epochs   average == numpy mean; baseline mean == 0
  ICA      with nothing excluded, apply() returns the data it was given
  decode   faces vs scrambled beats every label-permuted null (same CV, same seed)
  dipole   a dipole planted in the real sensor geometry (head-fitted sphere), whitened
           by the real noise covariance, is recovered at its position and orientation
Run with `python3 -u` so a killed run still has its output in command.log.
"""
import os
import sys
import time
import warnings

import numpy as np

BIDS, OUT = sys.argv[1], sys.argv[2]
RUNS = sys.argv[3:]
os.makedirs(OUT, exist_ok=True)
ROWS, FAILS, PHASES = [], [], []
T0 = time.time()


def rec(k, v):
    """Always str() the value -- `"%s" % obj` unpacks a tuple-like object as format args."""
    v = v if isinstance(v, str) else str(v)
    print("%s\t%s" % (k, v), flush=True)
    ROWS.append((k, v))


def check(ok, msg):
    """Never `ok is False`: a numpy bool is not the False singleton."""
    if not bool(ok):
        print("FAIL: %s" % msg, flush=True)
        FAILS.append(msg)


def finite(name, *vals):
    """`nan > tol` is False, so a one-sided bound cannot catch a NaN; this can."""
    for v in vals:
        if not np.all(np.isfinite(np.asarray(v, dtype="float64"))):
            check(False, "%s is not finite" % name)


def ver(label, fn):
    """A descriptive line must never be able to fail the run."""
    try:
        rec(label, fn())
    except Exception as exc:
        rec(label, "unknown (%s)" % type(exc).__name__)


class phase:
    def __init__(self, name):
        self.name = name

    def __enter__(self):
        self.t = time.time()

    def __exit__(self, *a):
        PHASES.append((self.name, time.time() - self.t))
        print("phase\t%s\t%.1f s" % (self.name, time.time() - self.t), flush=True)


warnings.simplefilter("ignore")
import mne  # noqa: E402
import pandas as pd  # noqa: E402

mne.set_log_level("ERROR")
ver("mne", lambda: mne.__version__)
ver("numpy", lambda: np.__version__)
ver("scipy", lambda: __import__("scipy").__version__)
ver("sklearn", lambda: __import__("sklearn").__version__)


def blas():
    from threadpoolctl import threadpool_info
    return "; ".join("%s %s %s threads=%s" % (d.get("internal_api"), d.get("version"),
                                              d.get("architecture"), d.get("num_threads"))
                     for d in threadpool_info())


ver("threadpools", blas)

stem = os.path.join(BIDS, "sub-01_ses-meg_task-facerecognition_run-%s")
CAL = os.path.join(BIDS, "sub-01_ses-meg_acq-calibration_meg.dat")
CTC = os.path.join(BIDS, "sub-01_ses-meg_acq-crosstalk_meg.fif")
STIM = {"Famous": [5, 6, 7], "Unfamiliar": [13, 14, 15], "Scrambled": [17, 18, 19]}
CODES = sorted(c for v in STIM.values() for c in v)
DELAY = 37
raws, evs, ref_info, origin = [], [], None, None
n_stim_total = 0

for run in RUNS:
    with phase("read run-%s" % run):
        raw = mne.io.read_raw_fif(stem % run + "_meg.fif", preload=True)
    sf = raw.info["sfreq"]
    rec("run-%s" % run, "%d channels, %d samples (%.1f s) at %g Hz, first_samp %d"
        % (len(raw.ch_names), raw.n_times, raw.n_times / sf, sf, raw.first_samp))
    chans = pd.read_csv(stem % run + "_channels.tsv", sep="\t")
    bids_bads = chans.loc[chans["status"] == "bad", "name"].tolist()
    raw.info["bads"] = bids_bads

    # --- events against the dataset's own events.tsv ------------------------------------------
    with phase("events run-%s" % run):
        tsv = pd.read_csv(stem % run + "_events.tsv", sep="\t")
        want = tsv[tsv["value"].isin(CODES)]
        ev = mne.find_events(raw, stim_channel="STI101", shortest_event=1, mask=255, mask_type="and")
        ev = ev[np.isin(ev[:, 2], CODES)]
        # events.tsv carries stimulus ONSET: the trigger plus the fixed 34 ms projector delay that
        # Wakeman & Henson (2015) measured, round(0.034 * 1100) = 37 samples. So every stimulus is
        # exactly 37 samples after its trigger, with the same code; button presses have no projector.
        off = want["sample"].to_numpy() - (ev[:, 0] - raw.first_samp) if len(ev) == len(want) else np.array([-1])
        same = (len(ev) == len(want) and np.array_equal(ev[:, 2], want["value"].to_numpy())
                and np.all(off == DELAY))
        rec("events run-%s" % run, "%d stimulus triggers from STI101 (low byte); events.tsv has %d; codes equal "
            "and every onset trigger + %d samples: %s (offsets seen %s)"
            % (len(ev), len(want), DELAY, same, sorted(set(off.tolist()))[:6]))
        check(same, "run-%s: stimulus triggers differ from events.tsv" % run)
        btn = mne.find_events(raw, stim_channel="STI101", shortest_event=1, mask=256 | 4096, mask_type="and")
        wb = tsv[tsv["value"].isin([256, 4096])]
        # A left and a right press on the same sample is one STI101 value, 256|4096 = 4352;
        # events.tsv lists such a press under one code. So: same samples, and the listed code's bit is set.
        bsame = (len(btn) == len(wb) and np.array_equal(btn[:, 0] - raw.first_samp, wb["sample"].to_numpy())
                 and np.all((btn[:, 2] & wb["value"].to_numpy()) == wb["value"].to_numpy()))
        rec("buttons run-%s" % run, "%d button presses in STI101 bits 256|4096 (%d both at once); events.tsv %d; "
            "same samples, listed code's bit set, no delay: %s" % (len(btn), int(np.sum(btn[:, 2] == 4352)), len(wb), bsame))
        if not bsame:
            a = set(map(tuple, np.c_[btn[:, 0] - raw.first_samp, btn[:, 2]].tolist()))
            b = set(map(tuple, wb[["sample", "value"]].to_numpy().tolist()))
            rec("buttons run-%s diff" % run, "only in STI101 %s; only in events.tsv %s" % (sorted(a - b)[:10], sorted(b - a)[:10]))
        check(bsame, "run-%s: button presses differ from events.tsv" % run)
        ev = ev.copy()
        ev[:, 0] += DELAY          # epoch on stimulus onset, as events.tsv does
        n_stim_total += len(ev)

    if run == RUNS[0]:
        with phase("real-channel identities"):
            # Parseval: a one-segment boxcar Welch estimate of a zero-mean channel integrates to its power.
            x = raw.get_data(picks=["MEG0111", "MEG0112", "MEG2443", "EEG001"])
            x = x - x.mean(axis=1, keepdims=True)
            n = x.shape[1]
            p, f = mne.time_frequency.psd_array_welch(x, sf, n_fft=n, n_per_seg=n, n_overlap=0, window="boxcar")
            par = np.max(np.abs(p.sum(axis=1) * (f[1] - f[0]) / (x ** 2).mean(axis=1) - 1))
            finite("parseval", par)
            rec("psd_parseval", "max |sum(psd) df / mean(x^2) - 1| = %.2e, 4 channels x %d samples" % (par, n))
            check(par < 1e-12, "Welch's PSD of a real channel does not integrate to its power (%.3e)" % par)
            filt = lambda a: mne.filter.filter_data(a, sf, 1.0, 40.0, method="fir", phase="zero",
                                                    fir_window="hamming", fir_design="firwin")
            a, b = x[0:1] / np.abs(x[0]).max(), x[3:4] / np.abs(x[3]).max()
            mix, fa, fb = filt(2.5 * a - 0.7 * b), filt(a), filt(b)
            lin = np.max(np.abs(mix - (2.5 * fa - 0.7 * fb))) / np.max(np.abs(mix))
            finite("filter linearity", lin)
            rec("filter_linearity", "real MEG + EEG channel, whole run: rel %.2e" % lin)
            check(lin < 1e-12, "the FIR filter is not linear on real data (%.3e)" % lin)
            eeg = raw.copy().pick("eeg")
            X = eeg.get_data()
            D = mne.set_eeg_reference(eeg, "average", projection=False)[0].get_data()
            s0 = np.max(np.abs(D.sum(axis=0))) / np.max(np.abs(X))
            cf = np.max(np.abs(D - (X - X.mean(axis=0)))) / np.max(np.abs(X))
            rec("avg_reference", "%d real EEG channels: max |sum| %.2e, max |D - (X - mean X)| %.2e (rel)"
                % (X.shape[0], s0, cf))
            check(s0 < 1e-13 and cf < 1e-13, "the average reference is not exact on real EEG")
            del eeg, X, D, x
            ref_info = raw.info
            # one head-fitted origin for every run, as MNE-BIDS-Pipeline does
            _, origin, _ = mne.bem.fit_sphere_to_headshape(raw.info, units="m")
            rec("sss_origin_head_mm", str(np.round(1e3 * origin, 2).tolist()))

    raw.pick(["meg", "eog", "ecg", "stim"])
    with phase("bad sensors run-%s" % run):
        noisy, flat = mne.preprocessing.find_bad_channels_maxwell(
            raw, calibration=CAL, cross_talk=CTC, origin=origin, coord_frame="head")
        raw.info["bads"] = sorted(set(bids_bads + noisy + flat))
        rec("bads run-%s" % run, "noisy %s flat %s" % (noisy, flat))

    if run == RUNS[0]:
        with phase("sss linearity"):
            seg = [raw.copy().crop(t, t + 20.0, include_tmax=False) for t in (10.0, 30.0)]
            A, B = seg[0].get_data(), seg[1].get_data()
            mixr = mne.io.RawArray(2.5 * A - 0.7 * B, seg[0].info, first_samp=seg[0].first_samp)
            sss = lambda r: mne.preprocessing.maxwell_filter(
                r, calibration=CAL, cross_talk=CTC, origin=origin, coord_frame="head",
                destination=ref_info["dev_head_t"]).get_data(picks="meg")
            mixo = mne.io.RawArray(A, seg[0].info, first_samp=seg[0].first_samp)
            fo = sss(mixo)
            mixo2 = mne.io.RawArray(B, seg[0].info, first_samp=seg[0].first_samp)
            go = sss(mixo2)
            ho = sss(mixr)
            lin = np.max(np.abs(ho - (2.5 * fo - 0.7 * go))) / np.max(np.abs(ho))
            finite("sss linearity", lin)
            rec("sss_linearity", "maxwell_filter(2.5a - 0.7b) vs 2.5 f(a) - 0.7 f(b), two real 20 s segments: rel %.2e" % lin)
            check(lin < 1e-12, "SSS is not linear in the data (%.3e)" % lin)
            del seg, A, B, mixr, mixo, mixo2, fo, go, ho

    with phase("maxwell run-%s" % run):
        raw = mne.preprocessing.maxwell_filter(raw, calibration=CAL, cross_talk=CTC, origin=origin,
                                               coord_frame="head", destination=ref_info["dev_head_t"])
    nfree = int(raw.info["proc_history"][0]["max_info"]["sss_info"]["nfree"])
    seg = raw.get_data(picks="meg", start=0, stop=int(60 * sf))
    s = np.linalg.svd(seg, compute_uv=False)
    gap = s[nfree] / s[nfree - 1]
    finite("sss singular values", s)
    rec("sss_rank run-%s" % run, "nfree %d; singular values s[nfree-1]/s0 %.2e, s[nfree]/s0 %.2e, gap %.2e"
        % (nfree, s[nfree - 1] / s[0], s[nfree] / s[0], gap))
    check(gap < 1e-8, "run-%s: SSS output is not rank nfree=%d (gap %.3e)" % (run, nfree, gap))
    if run == RUNS[0]:
        np.save(os.path.join(OUT, "sss-sv-run01.npy"), s)
    del seg
    with phase("filter run-%s" % run):
        raw.filter(1.0, 40.0, method="fir", phase="zero", fir_window="hamming", fir_design="firwin",
                   picks=["meg", "eog", "ecg"])
    raws.append(raw)
    evs.append(ev)

rec("stimulus_events", str(n_stim_total))

# --- session: epochs, ICA, evoked, decoding, dipole --------------------------------------------
with phase("concatenate + epochs"):
    raw, events = mne.concatenate_raws(raws, events_list=evs)
    del raws
    event_id = {"%s/%d" % (k, c): c for k, v in STIM.items() for c in v}
    reject = dict(grad=4000e-13, mag=4e-12)
    epochs = mne.Epochs(raw, events, event_id, tmin=-0.2, tmax=0.8, baseline=(None, 0), picks=["meg", "eog", "ecg"],
                        reject=reject, preload=True)
    counts = {k: len(epochs[k]) for k in STIM}
    rec("epochs", "%d of %d kept after peak-to-peak rejection: %s" % (len(epochs), len(events), counts))
    check(len(epochs) > 0.5 * len(events), "fewer than half the epochs survived rejection")
    A = epochs.get_data(picks="meg")
    av = np.max(np.abs(epochs.average(picks="meg").data - A.mean(axis=0))) / np.max(np.abs(A))
    pre = epochs.times <= 0
    base = np.max(np.abs(A[:, :, pre].mean(axis=2))) / np.max(np.abs(A))
    finite("epoch identities", av, base)
    rec("epoch_average", "average vs numpy mean %.2e; baseline-period mean %.2e (rel), %d epochs x %d ch x %d times"
        % (av, base, *A.shape))
    check(av < 1e-14 and base < 1e-14, "epoch averaging or baseline correction is not exact")
    del A
    # analysis rate 220 Hz (40 Hz low-pass already applied). Baseline was removed at 1100 Hz, so the
    # decimated samples' pre-stimulus mean is no longer exactly zero: reported, not asserted.
    epochs.decimate(5)
    A = epochs.get_data(picks="meg")
    rec("baseline_after_decimate", "pre-stimulus mean of the 220 Hz samples %.2e (rel, reported)"
        % (np.max(np.abs(A[:, :, epochs.times <= 0].mean(axis=2))) / np.max(np.abs(A))))
    del A

with phase("ica fit"):
    ica = mne.preprocessing.ICA(n_components=0.999, method="fastica", random_state=97, max_iter=1000)
    ica.fit(raw, picks="meg", decim=5, reject=reject)
    eog_idx, eog_scores = ica.find_bads_eog(raw, ch_name=["EOG061", "EOG062"])
    ecg_idx, ecg_scores = ica.find_bads_ecg(raw, ch_name="ECG063", method="ctps")
    npca = ica.pca_components_.shape[0]
    rec("ica", "%d components (0.999 of variance), %d PCA components held; EOG %s, ECG %s"
        % (ica.n_components_, npca, [int(i) for i in eog_idx], [int(i) for i in ecg_idx]))
    check(len(eog_idx) > 0, "no ICA component correlates with the EOG")
    probe = raw.copy().crop(0, 60).pick(["meg", "eog", "ecg"])
    X = probe.get_data(picks="meg")
    # unmix and remix through every PCA component: nothing excluded, nothing truncated, so lossless
    Y = ica.apply(probe.copy(), exclude=[], n_pca_components=npca).get_data(picks="meg")
    rt = np.max(np.abs(Y - X)) / np.max(np.abs(X))
    # the default keeps only the n_components_ ICA dimensions: report what that discards
    Z = ica.apply(probe.copy(), exclude=[]).get_data(picks="meg")
    tr = np.linalg.norm(Z - X) / np.linalg.norm(X)
    finite("ica round trip", rt, tr)
    rec("ica_roundtrip", "apply(exclude=[], all %d PCA components) vs input, 60 s x %d MEG channels: rel %.2e; "
        "default apply (%d dims) discards %.2e of the norm (reported)" % (npca, X.shape[0], rt, ica.n_components_, tr))
    check(rt < 1e-10, "ICA with nothing excluded does not return its input (%.3e)" % rt)
    del probe, X, Y, Z
    # apply()'s `exclude` is a union with ica.exclude, so the round trip must run before this
    ica.exclude = sorted(set(eog_idx) | set(ecg_idx))

with phase("ica apply + evoked"):
    ica.apply(epochs)
    epochs.apply_baseline((None, 0))   # removing components moves the pre-stimulus mean; restore it
    ep = epochs.copy().pick("meg")
    A = ep.get_data()
    pre = ep.times <= 0
    evk = {k: ep[k].average() for k in STIM}
    faces = mne.combine_evoked([evk["Famous"], evk["Unfamiliar"]], weights="nave")
    diff = mne.combine_evoked([faces, evk["Scrambled"]], weights=[1, -1])
    g = diff.copy().pick("grad").data
    gfp = np.sqrt((g ** 2).mean(axis=0))
    win = (ep.times >= 0.1) & (ep.times <= 0.25)
    t_peak = float(ep.times[win][np.argmax(gfp[win])])
    rec("faces_minus_scrambled", "grad RMS peak at %.0f ms in 100-250 ms (reported; M170 expected)" % (1e3 * t_peak))
    np.savez(os.path.join(OUT, "evoked.npz"), times=ep.times, **{k: v.data for k, v in evk.items()})
    np.save(os.path.join(OUT, "ica-mixing.npy"), ica.mixing_matrix_)
    np.save(os.path.join(OUT, "epoch-selection.npy"), epochs.selection)

with phase("decoding"):
    from sklearn.linear_model import LogisticRegression
    from sklearn.model_selection import StratifiedKFold
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler
    from mne.decoding import SlidingEstimator, cross_val_multiscore
    y = np.isin(ep.events[:, 2], STIM["Famous"] + STIM["Unfamiliar"]).astype(int)
    clf = SlidingEstimator(make_pipeline(StandardScaler(), LogisticRegression(solver="liblinear")),
                           scoring="roc_auc", n_jobs=1)
    cv = StratifiedKFold(5, shuffle=True, random_state=0)
    auc = cross_val_multiscore(clf, A, y, cv=cv).mean(axis=0)
    rng = np.random.default_rng(20261018)
    null = np.array([cross_val_multiscore(clf, A, rng.permutation(y), cv=cv).mean(axis=0) for _ in range(5)])
    finite("auc", auc, null)
    rec("decoding", "faces %d vs scrambled %d: peak AUC %.4f at %.0f ms; pre-stimulus mean %.4f; "
        "5 permuted-label nulls peak %.4f" % (y.sum(), (1 - y).sum(), auc.max(), 1e3 * ep.times[np.argmax(auc)],
                                              auc[pre].mean(), null.max()))
    check(auc.max() > null.max(), "faces vs scrambled is no more decodable than permuted labels")
    check(ep.times[np.argmax(auc)] > 0, "peak decoding is before the stimulus")
    np.savez(os.path.join(OUT, "decoding.npz"), auc=auc, null=null)

with phase("dipole"):
    cov = mne.compute_covariance(ep, tmax=0.0, method="empirical")
    sphere = mne.make_sphere_model("auto", "auto", faces.info)
    fc = faces.copy().pick("meg").crop(0.15, 0.20)
    dip, _ = mne.fit_dipole(fc, cov, sphere)
    i = int(np.argmax(dip.gof))
    rec("dipole_m170", "best of %d fits at %.0f ms: GOF %.1f%%, %.1f nAm, head-frame position (mm) %s (reported)"
        % (len(dip.times), 1e3 * dip.times[i], dip.gof[i], 1e9 * dip.amplitude[i],
           np.round(1e3 * dip.pos[i], 1).tolist()))
    # Planted at a fixed, well-conditioned place -- 69 mm from the sphere centre, tangential (a
    # sphere is blind to radial currents) -- not at the real fit, whose position is the noisy part.
    r0 = sphere["r0"]
    off = np.array([0.04, -0.04, 0.04])
    ori = np.cross(off, [0.0, 0.0, 1.0])
    ori /= np.linalg.norm(ori)
    truth = mne.Dipole(np.array([0.0]), (r0 + off)[None], np.array([50e-9]), ori[None], np.array([100.0]))
    fwd, stc = mne.make_forward_dipole(truth, sphere, fc.info)
    sim = mne.simulation.simulate_evoked(fwd, stc, fc.info, cov, nave=np.inf)
    d2, _ = mne.fit_dipole(sim, cov, sphere)
    dpos = 1e3 * np.linalg.norm(d2.pos[0] - truth.pos[0])
    dori = np.degrees(np.arccos(min(1.0, abs(float(np.dot(d2.ori[0], truth.ori[0]))))))
    finite("planted dipole", dpos, dori, d2.gof, d2.amplitude)
    rec("dipole_planted", "planted in the real sensor geometry, %d MEG sensors, real noise covariance: position %.4f mm, "
        "orientation %.4f deg, %.3f nAm (planted 50), GOF %.4f%%"
        % (len(fc.ch_names), dpos, dori, 1e9 * d2.amplitude[0], d2.gof[0]))
    check(dpos < 1.0 and dori < 1.0, "the planted dipole was recovered off position or orientation")
    check(abs(d2.amplitude[0] / 50e-9 - 1) < 0.01, "the planted 50 nAm dipole came back at %.3f nAm" % (1e9 * d2.amplitude[0]))
    np.savez(os.path.join(OUT, "dipole.npz"), pos=dip.pos, ori=dip.ori, gof=dip.gof, amp=dip.amplitude,
             planted=np.r_[d2.pos[0], d2.ori[0], d2.amplitude, d2.gof])

with open(os.path.join(OUT, "score.tsv"), "w") as fh:
    for k, v in ROWS:
        fh.write("%s\t%s\n" % (k, v))
with open(os.path.join(OUT, "phases.tsv"), "w") as fh:
    for k, v in PHASES:
        fh.write("%s\t%.1f\n" % (k, v))
rec("python_wall_s", "%.1f" % (time.time() - T0))
try:  # the process's true peak RSS (Linux reports KiB); the 5 s sampler in real.sh misses transients
    import resource
    with open(os.path.join(OUT, "rss.txt"), "w") as fh:
        fh.write("peak_rss_gib %.2f\n" % (resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1048576))
except Exception as exc:
    print("peak rss unknown (%s)" % type(exc).__name__)
if FAILS:
    print("%d check(s) failed" % len(FAILS))
    sys.exit(1)
print("MNE REAL OK")
