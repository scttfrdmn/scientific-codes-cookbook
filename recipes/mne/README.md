---
tool: mne
tool_version: "1.13.2"
env: neuroimaging
image: quay.io/aarchsci/neuroimaging@sha256:f57c5cc3637cfcf20dfdf8a1627f3a57b56b397dde9f99805d35fa4b4a7a657c
env_x86_64: neuroimaging-d0a05a385703
spawn_version: 0.127.0
last_verified: 2026-10-16
last_verified_x86_64: 2026-10-17
---
# MNE-Python — spectra, filters, referencing, events, ICA and a dipole fit, each against a planted answer

Plants a known answer in each step of an EEG/MEG pipeline and has MNE recover it on Graviton4: frequencies, filter response, average reference, triggers, epochs, independent sources and a source dipole. For anyone analysing MEG/EEG on ARM.

## Run it

```bash
make stage RECIPE=mne     # once: the checks only — every signal is synthesised
spawn task run --spec "$(make -s spec RECIPE=mne)" --wait
make ls RECIPE=mne

raw.filter(1.0, 40.0, method="fir", phase="zero", fir_window="hamming", fir_design="firwin")
ica = mne.preprocessing.ICA(n_components=3, method="fastica", random_state=0).fit(raw)
dip, _ = mne.fit_dipole(evoked, mne.make_ad_hoc_cov(info), sphere)
```

## Make it yours

| In the recipe | Swap for | What to know |
|---|---|---|
| `mne-base` | `mne` | the env ships **`mne-base`** — the library without the GUI/3-D stack (no pyvista, Qt). Plotting to files works; interactive browsers and 3-D brain views do not. |
| synthetic signals | MNE's sample data | `mne-base` ships **no sample data and no tests**: `mne.datasets.*` downloads through pooch at run time. Stage what you need to S3 and read it from `/tmp` instead. |
| `random_state=0` | unseeded ICA | FastICA starts from a random unmixing, so an unseeded run returns its components **in a different order** each time. Seed it if anything downstream indexes components. |
| sphere model | a BEM | a BEM needs a FreeSurfer subject; the sphere model does not, which is what lets a dipole fit run here with nothing staged. |
| `phase="zero"` | `"minimum"` | zero-phase is non-causal (no lag, measured: 0 samples); use minimum phase when timing must not borrow from the future. |

**Leave the fixtures.** A planted answer is what turns "the step ran" into "the step was right" — the dipole's position is known before MNE fits it. **Scale it** to your recordings: plant a dipole or a source in real noise and every check transfers.

## Shape, size, cost

One task on `m8g.large` (2 vCPU / 8 GiB), TTL 30m as a **backstop** with `cost_limit` $0.12 as the real guard. The checks take seconds of a ~1.4-minute window ([layout](../../patterns/layout-and-effective-cost.md)).

<details>
<summary>As shipped: exact spectral peaks and Parseval, a linear zero-phase filter on spec, exact referencing and epoching, ICA sources back one-to-one, a dipole recovered to 0.03 mm</summary>

### The checks

| observable | assertion | observed |
|---|---|---|
| staged bytes | match pin | 1 of 1 |
| mne / numpy / scipy | read from the running install | **1.13.2** / 2.5.4 / 1.18.1 |
| **Welch PSD peaks, sines at 10 and 37 Hz** | **`==` the planted frequencies** | **10.0, 37.0** (0.5 Hz bins) |
| flat channel | `psd == 0` | 0 |
| **Parseval: `Σ psd · df / mean(x²)`** | **`|· − 1| < 1e-12`** | **2.2e-16** |
| **FIR linearity `f(2.5a − 0.7b)`** | **rel `< 1e-12`** | **8.4e-16** |
| passband amplitude, 10 Hz | within Hamming ripple (0.22%) | 0.998484 |
| stopband, 150 Hz | ≥ 53 dB (Hamming) | 75.7 dB |
| zero phase | xcorr peak at lag 0 | 0 |
| **average reference** | **channel sum and `X − mean X`, rel `< 1e-13`** | **4.7e-16, 0** |
| **`find_events`** | **samples and codes `==` planted** | **30 of 30** |
| **epochs: average vs `numpy.mean`, baseline mean** | **rel `< 1e-14`** | **0, 3.4e-17** |
| **ICA: best \|corr\| per planted source** | **one-to-one, `> 0.99`** | **0.999997, 0.999215, 0.999999** |
| control: the mixed channels | must not already match | 0.829, 0.846, 0.804 |
| **dipole: position / orientation** | **`< 1 mm` / `< 1°`** | **0.028 mm / 0.010°** |
| dipole: amplitude / GOF | within 1% of 50 nAm / > 99.9% | 49.997 nAm / 100.0000% |

### Why these bounds

Where the step is linear algebra on float64 — Parseval, linearity, referencing, epoch averaging —
the claim is equality and the bound is a few hundred machine epsilons. The filter's passband and
stopband bounds are the **Hamming window's own design spec** (ripple 0.0194 dB, attenuation ≥ 53
dB), so they test that MNE built the filter it says it built; the edges are excluded, where padding
acts. The two peaks are exact because 2000 samples at 1 kHz put 10 and 37 Hz on bin centres.

ICA and the dipole fit are estimators, so their bounds sit **between regimes**, not next to the
observed values. A failed separation leaves components that look like the mixtures — best match
~0.8, which the mixed channels show — while a converged one on 60,000 samples is within ~1e-3, so
`0.99` separates them. The dipole field is noise-free and computed from the same sphere model the
fit uses, so the fit can only miss by its optimiser's tolerance; `1 mm` is an order below a
millimetre-scale mislocation and three orders above what it does.

### Pins

| | |
|---|---|
| data | **none staged** — sines, white noise, a planted trigger channel, three non-Gaussian sources and a 60-channel 10-20 dipole field, all from `default_rng(20261016)` and MNE's bundled `standard_1020` montage |
| checks | `identities.py`, pinned by sha256 |
| image | `quay.io/aarchsci/neuroimaging@sha256:f57c5cc3637c…` — mne-base 1.13.2, nilearn 0.14.1, numpy 2.5.4, scipy 1.18.1, python 3.14.8 |

cosign-verified against `playgroundlogic/aarchsci`; the signature covers the **manifest-list**
digest (`6cd4f404…`), so verify the tag and pin the arm64 digest.

### On x86_64

The same spec on `m8i.large`, through the `neuroimaging` env's linux-64 twin
(`envs-x86_64/neuroimaging-d0a05a385703.linux-64.lock`: mne, numpy and scipy at the image's versions;
it differs only in `charset-normalizer`, `glib`, and `libsqlite`). Every assertion passes, and
everything exact stays exact: the PSD peaks, Parseval at 2.2e-16, the referencing and epoching
identities, the 30 events and the ICA correlations to six digits. Two values move:

| | arm64 | x86_64 | bound |
|---|---|---|---|
| FIR linearity, rel | 8.4e-16 | 9.1e-16 | < 1e-12 |
| dipole position / orientation | 0.0277 mm / 0.0101° | 0.0283 mm / 0.0122° | < 1 mm / < 1° |
| dipole amplitude | 49.997 nAm | 49.994 nAm | within 1% of 50 |

The filter difference is rounding. The dipole fit is an iterative optimiser, so it is a path, not
a fixed point: a different last bit takes it to a slightly different stopping point. Both chips
stay under 3% of the position bound and recover the planted dipole.

### Run + verify

```sh
make stage RECIPE=mne
spawn task run --spec "$(make -s spec RECIPE=mne)" --wait
aws s3 cp "s3://$(make -s print-bucket)/runs/mne/r1/score.tsv" -
make stage RECIPE=mne ARCH=x86_64   # also stages the env twin, once
spawn task run --spec "$(make -s spec RECIPE=mne ARCH=x86_64)" --wait   # writes runs-x86_64/
```

Fails on a pin mismatch, a PSD peak off its planted frequency or a silent channel with power, a
PSD that does not integrate to the signal power, a filter that is not linear, off its design spec
or phase-shifting, a reference or epoch average that is not exact, triggers that differ from the
planted ones, ICA components that do not map one-to-one onto the sources, or a dipole recovered
off position, orientation or amplitude — or a NaN anywhere a bound is applied. Check the bucket
regardless ([exit 0 isn't proof](../../practices/container-path.md)).

### Not covered

**MNE's own test suite**: `mne-base` ships no test files, and its tests need the `mne-testing-data`
download. **Real recordings and file readers** (FIF, EDF, BrainVision): every signal here is built
in memory. **Source imaging with a BEM** (`make_bem_model`, minimum-norm inverse): those need a
FreeSurfer subject.
**Time-frequency** (Morlet, multitaper), **statistics** (cluster permutation) and **decoding**.
Plotting and the interactive browser, which `mne-base` omits. [nilearn](../nilearn/README.md) is the
fMRI sibling on the same image.

</details>
