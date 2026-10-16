#!/usr/bin/env python3
"""MNE-Python on Graviton4: planted truths that MNE must recover, and identities it must satisfy.

`mne-base` ships neither its tests nor its sample data (those come down through pooch at run
time), so every signal here is built in-run with a fixed seed, and each check is a statement
that holds for a mathematical reason, with the planted answer known before MNE runs:

 1. Spectrum: a sine at a bin centre peaks at exactly its frequency, a flat channel is silent,
    and Welch's PSD integrates to the signal's mean power (Parseval).
 2. Filtering: a zero-phase FIR is linear and has no phase shift; a passband sine keeps its
    amplitude and a stopband sine is attenuated, both to the window design's own spec.
 3. Average reference: every sample then sums to zero over channels, and equals X - mean(X).
 4. Events: find_events returns exactly the planted trigger samples and codes.
 5. Epochs: averaging is the mean, and a (None, 0) baseline zeroes the pre-stimulus mean.
 6. ICA: three planted non-Gaussian sources, mixed by a known matrix, come back one-to-one.
 7. Dipole fit: a dipole planted in a sphere model is recovered at its position and orientation
    from the field it produces.

Run with `python3 -u` so a killed run still has its output in command.log.
"""
import sys
import warnings

import numpy as np

ROWS = []


def rec(k, v):
    """Always str() the value -- `"%s" % obj` unpacks a tuple-like object as format args."""
    v = v if isinstance(v, str) else str(v)
    print("%s\t%s" % (k, v))
    ROWS.append((k, v))


def die(msg):
    print("FAIL: %s" % msg)
    sys.exit(1)


def ver(label, fn):
    """A descriptive line must never be able to fail the run."""
    try:
        rec(label, fn())
    except Exception as exc:
        rec(label, "unknown (%s)" % type(exc).__name__)


def finite(name, *vals):
    """`nan > tol` is False, so a one-sided bound cannot catch a NaN; this can."""
    for v in vals:
        if not np.all(np.isfinite(np.asarray(v, dtype="float64"))):
            die("%s is not finite" % name)


warnings.simplefilter("ignore", category=FutureWarning)
import mne  # noqa: E402

mne.set_log_level("ERROR")
ver("mne", lambda: mne.__version__)
ver("numpy", lambda: np.__version__)
ver("scipy", lambda: __import__("scipy").__version__)
rng = np.random.default_rng(20261016)
SF = 1000.0

# --- 1. spectrum -----------------------------------------------------------------------------
n = 2000                                      # 2 s at 1 kHz; n_fft = n gives 0.5 Hz bins
t = np.arange(n) / SF
x = np.vstack([np.sin(2 * np.pi * 10.0 * t), 0.5 * np.sin(2 * np.pi * 37.0 * t), np.zeros(n)])
psd, freqs = mne.time_frequency.psd_array_welch(x, SF, n_fft=n, n_per_seg=n, n_overlap=0,
                                                window="boxcar", average="mean")
finite("psd", psd)
peak0, peak1 = freqs[np.argmax(psd[0])], freqs[np.argmax(psd[1])]
rec("psd_peaks_hz", "%s and %s (planted 10.0 and 37.0; bins %.1f Hz)" % (float(peak0), float(peak1), freqs[1] - freqs[0]))
if not (peak0 == 10.0 and peak1 == 37.0):
    die("the PSD peaks are at %r / %r, not the planted 10.0 / 37.0 Hz" % (peak0, peak1))
if not np.all(psd[2] == 0):
    die("a flat channel has spectral power %r" % psd[2].max())
# Parseval for a one-sided density: sum(psd) * df == mean(x**2) for a zero-mean, boxcar, one-segment estimate
y = rng.standard_normal((4, n))
y -= y.mean(axis=1, keepdims=True)
py, fy = mne.time_frequency.psd_array_welch(y, SF, n_fft=n, n_per_seg=n, n_overlap=0, window="boxcar")
parseval = np.max(np.abs(py.sum(axis=1) * (fy[1] - fy[0]) / (y ** 2).mean(axis=1) - 1))
finite("parseval", parseval)
rec("psd_parseval", "max |sum(psd) df / mean(x^2) - 1| = %.2e over 4 noise channels" % parseval)
if not parseval < 1e-12:
    die("Welch's PSD does not integrate to the signal power (%.3e)" % parseval)

# --- 2. filtering ----------------------------------------------------------------------------
n = 20000
t = np.arange(n) / SF
lo, hi = 1.0, 40.0
pas, stop = np.sin(2 * np.pi * 10.0 * t), np.sin(2 * np.pi * 150.0 * t)
noise = rng.standard_normal(n)
filt = lambda a: mne.filter.filter_data(a[None, :], SF, lo, hi, method="fir", phase="zero",
                                        fir_window="hamming", fir_design="firwin")[0]
fp, fs, fn = filt(pas), filt(stop), filt(noise)
mix = filt(2.5 * pas - 0.7 * noise)
finite("filtered", fp, fs, fn, mix)
lin = np.max(np.abs(mix - (2.5 * fp - 0.7 * fn))) / np.max(np.abs(mix))
rec("filter_linearity", "max |f(2.5a - 0.7b) - (2.5f(a) - 0.7f(b))| / max = %.2e" % lin)
if not lin < 1e-12:
    die("the FIR filter is not linear (%.3e)" % lin)
core = slice(5000, 15000)                     # clear of the edges, where padding acts
amp_pass = np.max(np.abs(fp[core]))
att_db = 20 * np.log10(np.max(np.abs(fs[core])))
# Hamming-window FIR design: passband ripple 0.0194 dB (amplitude 0.22%), stopband >= 53 dB.
rec("filter_passband", "10 Hz amplitude %.6f (hamming spec: within 0.22%% of 1)" % amp_pass)
rec("filter_stopband", "150 Hz attenuated %.1f dB (hamming spec: >= 53 dB)" % -att_db)
if not abs(amp_pass - 1) < 0.0022:
    die("a passband sine changed amplitude to %.6f" % amp_pass)
if not -att_db >= 53:
    die("a stopband sine is attenuated only %.1f dB" % -att_db)
lags = np.arange(-50, 51)
xc = [np.dot(fp[core], pas[core.start + L:core.stop + L]) for L in lags]
lag0 = int(lags[int(np.argmax(xc))])
rec("filter_zero_phase", "cross-correlation with the input peaks at lag %d samples" % lag0)
if lag0 != 0:
    die("the zero-phase filter shifted the signal by %d samples" % lag0)

# --- 3. average reference --------------------------------------------------------------------
names = ["Fz", "Cz", "Pz", "Oz", "C3", "C4", "T7", "T8"]
X = rng.standard_normal((8, 5000)) * 1e-5
raw = mne.io.RawArray(X, mne.create_info(names, SF, "eeg"))
raw.set_montage(mne.channels.make_standard_montage("standard_1020"))
rr, _ = mne.set_eeg_reference(raw.copy(), "average", projection=False)
D = rr.get_data()
s0 = np.max(np.abs(D.sum(axis=0))) / np.max(np.abs(X))
cf = np.max(np.abs(D - (X - X.mean(axis=0)))) / np.max(np.abs(X))
rec("avg_reference", "max |sum over channels| %.2e; max |D - (X - mean X)| %.2e (relative)" % (s0, cf))
if not (s0 < 1e-13 and cf < 1e-13):
    die("the average reference does not sum to zero / is not X - mean(X)")

# --- 4. events, 5. epochs --------------------------------------------------------------------
n = 30000
stim = np.zeros(n)
planted = np.sort(rng.choice(np.arange(500, n - 1500, 700), 30, replace=False))
codes = rng.choice([1, 2, 4], 30)
for s, c in zip(planted, codes):
    stim[s:s + 20] = c
eeg = rng.standard_normal((4, n)) * 1e-5 + 3e-6
info = mne.create_info(["E1", "E2", "E3", "E4", "STI"], SF, ["eeg"] * 4 + ["stim"])
raw = mne.io.RawArray(np.vstack([eeg, stim]), info)
ev = mne.find_events(raw, stim_channel="STI", shortest_event=1)
ok = len(ev) == 30 and np.array_equal(ev[:, 0], planted) and np.array_equal(ev[:, 2], codes)
rec("find_events", "%d events; samples and codes identical to the planted ones: %s" % (len(ev), ok))
if not ok:
    die("find_events did not return exactly the planted triggers")
ep = mne.Epochs(raw, ev, tmin=-0.2, tmax=0.5, baseline=(None, 0), picks="eeg", preload=True)
A = ep.get_data()
avg = np.max(np.abs(ep.average().data - A.mean(axis=0))) / np.max(np.abs(A))
pre = ep.times <= 0
base = np.max(np.abs(A[:, :, pre].mean(axis=2))) / np.max(np.abs(A))
rec("epochs", "%d epochs; average vs numpy mean %.2e; baseline-period mean %.2e (relative)" % (len(ep), avg, base))
if len(ep) != 30:
    die("%d epochs survived, not 30" % len(ep))
if not (avg < 1e-14 and base < 1e-14):
    die("epoch averaging or baseline correction is not exact")

# --- 6. ICA ----------------------------------------------------------------------------------
n = 60000
t = np.arange(n) / SF
S = np.vstack([np.sign(np.sin(2 * np.pi * 3.0 * t)),          # square wave (sub-Gaussian)
               rng.laplace(size=n),                           # super-Gaussian
               ((t * 7.0) % 1.0) * 2 - 1])                    # sawtooth (sub-Gaussian)
S = (S - S.mean(axis=1, keepdims=True)) / S.std(axis=1, keepdims=True)
Amix = np.array([[1.0, 0.6, 0.3], [0.4, 1.0, 0.5], [0.2, 0.7, 1.0]])
M = Amix @ S * 1e-5
raw = mne.io.RawArray(M, mne.create_info(["M1", "M2", "M3"], SF, "eeg"))
ica = mne.preprocessing.ICA(n_components=3, method="fastica", random_state=0, max_iter=1000)
ica.fit(raw)
R = ica.get_sources(raw).get_data()
C = np.abs(np.corrcoef(np.vstack([R, S]))[:3, 3:])
finite("ica", C)
match = C.max(axis=1)
perm = C.argmax(axis=1)
Cmix = np.abs(np.corrcoef(np.vstack([M, S]))[:3, 3:]).max(axis=1)
rec("ica_recovery", "|corr(component, source)| best match %s, one-to-one: %s; mixed channels alone: %s"
    % (np.round(match, 6).tolist(), sorted(perm.tolist()) == [0, 1, 2], np.round(Cmix, 3).tolist()))
if sorted(perm.tolist()) != [0, 1, 2]:
    die("ICA components do not map one-to-one onto the planted sources")
# A failed separation leaves components that look like the mixtures (best match ~0.8, measured on
# the mixed channels below); a converged one on 60,000 samples is within ~1e-3 of the source.
# 0.99 sits between the two regimes rather than next to the observed value.
if not np.all(match > 0.99):
    die("an ICA component matches its source only to %.6f" % match.min())
if np.all(Cmix > 0.999):
    die("control: the mixed channels already match the sources, so ICA was not tested")

# --- 7. dipole fit ---------------------------------------------------------------------------
mont = mne.channels.make_standard_montage("standard_1020")
chs = [c for c in mont.ch_names if c[-1] in "0123456789zZ" and c not in ("Fpz", "Iz")][:60]
info = mne.create_info(chs, SF, "eeg")
info.set_montage(mont)
sphere = mne.make_sphere_model("auto", "auto", info)
pos_true = np.array([[0.02, -0.01, 0.05]]) + sphere["r0"]
ori_true = np.array([[0.0, 0.6, 0.8]])
dip_true = mne.Dipole(np.array([0.0]), pos_true, np.array([50e-9]), ori_true, np.array([100.0]))
fwd, stc = mne.make_forward_dipole(dip_true, sphere, info)
evoked = mne.simulation.simulate_evoked(fwd, stc, info, mne.make_ad_hoc_cov(info), nave=np.inf)
evoked.set_eeg_reference("average", projection=True)
dip, _ = mne.fit_dipole(evoked, mne.make_ad_hoc_cov(info), sphere)
dpos = 1e3 * np.linalg.norm(dip.pos[0] - pos_true[0])
dori = np.degrees(np.arccos(min(1.0, abs(float(np.dot(dip.ori[0], ori_true[0]))))))
finite("dipole", dpos, dori, dip.gof)
rec("dipole_fit", "%d EEG channels; position error %.4f mm, orientation error %.4f deg, GOF %.4f%%, "
    "amplitude %.3f nAm (planted 50)" % (len(chs), dpos, dori, dip.gof[0], dip.amplitude[0] * 1e9))
# Noise-free data from the same forward model: the fit can only miss by its optimiser's tolerance.
if not dpos < 1.0:
    die("the planted dipole was recovered %.3f mm away" % dpos)
if not dori < 1.0:
    die("the planted dipole's orientation was recovered %.3f deg off" % dori)
if not abs(dip.amplitude[0] / 50e-9 - 1) < 0.01:
    die("the planted 50 nAm dipole was recovered at %.3f nAm" % (dip.amplitude[0] * 1e9))
if not dip.gof[0] > 99.9:
    die("a noise-free dipole field was fitted with GOF %.3f%%" % dip.gof[0])

with open("score.tsv", "w") as fh:
    for k, v in ROWS:
        fh.write("%s\t%s\n" % (k, v))
print("MNE IDENTITIES OK")
