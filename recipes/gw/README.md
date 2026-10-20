---
tool: bilby
tool_version: "2.8.2"
env: gw
image: quay.io/aarchsci/gw@sha256:18e0856fcb6cdde0725acbed3f4efcaa34a7654eaf42f372ddac55ecd2f052d5
env_x86_64: gw-ab3a5857bd21
spawn_version: 0.127.0
last_verified: 2026-10-16
last_verified_x86_64: 2026-10-17
last_verified_amd: 2026-10-18
---
# Gravitational waves — PyCBC finds a planted chirp, Bilby recovers its chirp mass

A binary-black-hole signal is planted in simulated LIGO noise; PyCBC's matched filter finds it at the planted sample and Bilby's sampler brackets the planted chirp mass, with LALSuite underneath both. For anyone running CBC searches or parameter estimation on ARM.

## Run it

```bash
make stage RECIPE=gw          # once: the checks only — signal, noise and PSD are synthesised
spawn task run --spec "$(make -s spec RECIPE=gw)" --wait
make ls RECIPE=gw

snr = pycbc.filter.matched_filter(template, data, psd=psd, low_frequency_cutoff=20.0)
bilby.run_sampler(likelihood, priors, sampler="dynesty", nlive=200, sample="rwalk",
                  rstate=np.random.default_rng(SEED), check_point=False)
```

## Make it yours

| In the recipe | Swap for | What to know |
|---|---|---|
| `rstate=np.random.default_rng(SEED)` | `seed=SEED` | **Bilby drops `seed=` for dynesty** with a warning (`Kwargs contain both 'rstate' and 'seed', ignoring 'seed'`) and runs unseeded: two "seeded" runs gave different posteriors. Pass the generator and the run is byte-identical — here across a laptop and Graviton4. |
| chirp mass, mass ratio, distance free | the full 15-parameter BBH prior | the checks (truth inside the 99% interval, a 2% offset outside it) carry over unchanged; wall time does not — budget hours, and raise `npool`. |
| simulated noise from `aLIGOZeroDetHighPower` | real strain | stage a GWOSC file pinned by sha256 to S3 rather than fetching it in the task — [measured on GW150914](../../measurements/gw-real/README.md). To compare with a GWTC-2.1 posterior, set `BILBY_INCORRECT_PSD_NORMALIZATION=TRUE`: bilby 2.x dropped a window-power PSD scaling the release used, which moves every SNR by ×0.935. |
| `import pycbc` | `import pycbc.waveform`, `.filter`, `.psd`, … | a bare `import pycbc` succeeds even when its submodules are broken; import what you use. |

**Leave the fixture** for a first run: a planted signal is the only data whose answer is known exactly. **Scale it** by freeing parameters — nothing in the assertions depends on the problem size.

## Which box — [measured](../../measurements/gw-real/README.md) on GW150914, real GWOSC strain

**Billed $/event: Graviton4 `c8g.16xlarge` $3.17 (4,476 s), Intel `c8i.16xlarge` $3.86 (4,636 s), AMD `c8a.16xlarge` $3.39 (3,543 s).** One event is a PyCBC search of the GWOSC hour, which finds GW150914 blind in both detectors, plus full 15-parameter Bilby PE with XPHM and calibration. The sampler is 96% of the run and scales with cores, so **take the most cores per dollar: Graviton4 is cheapest and AMD fastest.** The rebuilt GWTC-2.1 likelihood matches the release to 0.041 in ln L, and the Bayes factor and medians match. The posterior's tails come out narrower than the published ones on every chip: a 5-SE quantile test fails, and that failure is reported, not loosened.

## Shape, size, cost

One task on `c8g.large` (2 vCPU / 4 GiB; peak RSS 273 MiB), TTL 30m as a **backstop** with `cost_limit` $0.10 as the real guard. The science takes 61 s of a ~2.5-minute task; boot, Docker and the image pull are the rest, so these timings are not compute cost ([layout](../../patterns/layout-and-effective-cost.md)).

<details>
<summary>As shipped: the SNR peak one sample from the planted merger, the injected chirp mass at posterior quantile 0.517, three wrappers of LALSimulation bit-identical</summary>

### The checks

Observed on Graviton4 (`c8g.large`) and x86_64 (`c8i.large`, the `gw` env's linux-64 twin, all
215 locked versions identical), both 2026-10-16; "same" is identical to the printed digits.

| observable | assertion | Graviton4 | x86_64 |
|---|---|---|---|
| staged bytes | match pin | 1 of 1 | same |
| bilby / lal / lalsimulation / pycbc / dynesty | read from the running install | **2.8.2** / 7.6.1 / 6.1.0 / 2.10.0 / 3.1.0 | same |
| **matched-filter peak vs planted merger** | **within 1 sample** | **+1 sample** (163841 vs 163840, at 4096 Hz) | same |
| **peak SNR vs optimal SNR from the PSD** | **\|ρ − ρ_opt\| < 4** | **24.953 vs 26.143** (−1.19) | same |
| control: same filter on the noise alone | max \|ρ\| < 8 | 4.454 over 196,608 samples | same |
| filter normalisation: mean \|ρ\|² on noise | in (1.8, 2.2) — expect 2 | **2.003** | same |
| **Bilby: injected chirp mass in the 99% interval** | **inside** | **28.0956 in [27.906, 28.210]**, quantile 0.517 | same |
| control: a chirp mass 2% off | **outside** the 99% interval | 28.658 — outside | same |
| ln Bayes factor, injection | > ρ²/4 = 199 | 391.3 (network ρ_opt 28.22) | same |
| control: ln Bayes factor, noise alone | < 5 | −14.67 | same |
| **Bilby ln L ratio at the injection vs ⟨d\|h⟩ − ⟨h\|h⟩/2 written out** | **rel < 1e-10** | **0** (403.344532) | 1.4e-16 |
| **IMRPhenomD h₊: PyCBC and Bilby vs LALSimulation direct** | **rel < 1e-10** | **0** over 4,016 bins, both | same |
| **optimal SNR: PyCBC `sigmasq`, Bilby, 4Δf Σ\|h\|²/S** | **rel < 1e-12** | **3.3e-16** | 0 |
| match: self, amplitude × 3700 | \|1 − m\| < 1e-12 | 2.2e-16, 2.2e-16 | 2.2e-16, 0 |
| control: match vs a 40+29 template | < 0.99 | 0.8226 | same |
| LAL constants vs GM☉ (`MTSUN`, `MRSUN`, `MSUN·G`) | ≤ 4 ulp | ≤ 0.77 ulp | same |
| chirp mass closed form; (M_c, q) → (m₁, m₂) | rel < 1e-13; < 1e-12 | 2.2e-16; 4.4e-16 | same |
| Bilby priors: cdf(rescale(u)) | < 1e-9 | 1.4e-15 over 5 priors | same |
| D_L → z → D_L (Planck15) | rel < 1e-5 | PyCBC 8.4e-7, Bilby 5.7e-9 | same |

**Across chips** the seeded sampler took the same path: all 661 posterior samples of every
sampled parameter are bit-identical, and only derived likelihoods move (ln L by ≤ 1.9e-9 on
~−8000, ln Z by 7e-12), too little to change an acceptance. The three differing rows are
round-off, at the last ulp.

### Why these bounds

**Matched filter.** With the true PSD, the matched-filter output at the true parameters is
ρ_opt plus unit-variance Gaussian noise, so the peak bound (4σ) and the noise-only ceiling
(P(|ρ| > 8) = e⁻³² per sample, ~1e-9 over the series) come from the noise statistics. Mean
|ρ|² = 2 on noise is the filter's normalisation in one number: a factor-of-2 convention error
in the inner product lands it at 1 or 4. The ±0.2 band is ~10σ for ~10⁴ effectively independent
samples.

**Sampler.** A correct posterior covers the truth at its stated rate, so a 99% interval misses
it 1% of the time per noise realisation; with the seed pinned the run is byte-identical, so that
1% is spent once, not every run. The 2%-off control proves the interval is narrow enough for
"inside" to mean something. The injection's ln BF must exceed ρ²/4 (maximum log-likelihood
ratio ≈ ρ²/2; the Occam factor of three parameters is tens, not hundreds); on noise it must stay
below 5 (150:1).

**Cross-code.** The waveform check is three wrappers around one C function, so it tests units
and conventions (solar masses vs kg, Mpc vs m) and the claim is equality. The optimal SNR is
three implementations of one sum, so the bound is round-off. PyCBC inverts D_L(z) through an
interpolant, so its round trip is held to interpolation error; a wrong cosmology (H₀ = 70 for
67.74) misses by 3%. Self-match measures one ulp from 1 in either direction, so that bound is
two-sided. The rules behind these: [cross-checks](../../practices/cross-checks.md).

### Pins

| | |
|---|---|
| data | **none staged** — IMRPhenomD (36 + 29 M☉) in coloured Gaussian noise from `aLIGOZeroDetHighPower`, H1+L1 for Bilby; all from seed `20261016` |
| checks | `identities.py`, pinned by sha256 |
| image | `quay.io/aarchsci/gw@sha256:18e0856fcb6c…` — bilby 2.8.2, lalsuite 7.25 (python-lal 7.6.1), pycbc 2.10.0, dynesty 3.1.0, python 3.13 |

cosign-verified against `playgroundlogic/aarchsci`; the signature covers the **manifest-list**
digest (`sha256:397f62ba7b40…`), so verify the tag and pin the arm64 digest. x86_64 runs the same versions from the locked linux-64 twin
`envs-x86_64/gw-ab3a5857bd21.linux-64.lock` (`make stage RECIPE=gw ARCH=x86_64`, then
`make -s spec RECIPE=gw ARCH=x86_64`; outputs under `runs-x86_64/`).

### Run + verify

```sh
make stage RECIPE=gw
spawn task run --spec "$(make -s spec RECIPE=gw)" --wait
aws s3 cp "s3://$(make -s print-bucket)/runs/gw/r1/score.tsv" -
```

Fails on a pin mismatch, an SNR peak more than a sample from the planted merger or more than 4σ
from the optimal SNR, a noise-only peak ≥ 8 or a mis-normalised filter, a chirp-mass interval
that misses the truth or admits a 2% offset, a Bayes factor that misses a loud signal or finds a
quiet one, any wrapper or inner product that disagrees with its definition — or a NaN anywhere a
bound is applied. Bilby's two result files (`inj_result.json`, `noise_result.json`) are staged
beside the score. Check the bucket regardless ([exit 0 isn't proof](../../practices/container-path.md)).

### Not covered

**Real strain** (GWOSC data, PSD estimation, glitches and gating). **Full-dimensional PE**:
spins, sky position, time and phase are fixed here, so marginalisation, precessing waveforms and
the hours-long runs are untouched. **Template-bank searches** (`pycbc_inspiral`, banks,
χ² vetoes, coincidence). **Multi-process sampling** (`npool > 1`), which changes the sampler's
path.

</details>
