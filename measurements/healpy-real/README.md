# healpy on the Planck PR3 SMICA CMB map at full resolution — Graviton4, Intel and AMD

> **healpy runs a full-resolution Planck analysis in 4.5–9 minutes on 8 vCPUs, and AMD is the
> cheapest place to run it: $0.054 billed, against $0.058 on Graviton4 and $0.077 on Intel.** The
> spherical-harmonic transforms decide the ranking: AMD does them 1.85× faster than Graviton4. All 31
> identities hold on all three chips. Intel and AMD give bit-identical outputs. Graviton4 matches them
> bit for bit in pixel space and to ≤1e-11 relative in harmonic space.

Workload: the Planck 2018 (PR3) **SMICA CMB map**, I, Q and U at Nside 2048, which is 50,331,648
pixels per field. It comes with Planck's common intensity and polarisation masks (fsky 0.779 and
0.781). These are the maps CMB analysis starts from, at the resolution Planck released them.
healpy does what a CMB user runs on them:

- **Read and reorder** the NESTED maps into RING ordering.
- **Pixel geometry** on every pixel in both orderings, checked against
  [astropy-healpix](https://github.com/astropy/astropy-healpix), an independent implementation.
  It also runs `query_disc` on 24 discs (13.9 M pixels) against brute force.
- **`ud_grade`** from 2048 down to 1, and up to 4096 (201 M pixels).
- **Harmonic analysis.** Polarised `map2alm` on the full sky and on the masked sky, to healpy's
  default ℓmax = 3·Nside − 1 = 6143 with `iter=3` (18.9 M a_ℓm per field). Then a
  Galactic → equatorial `rotate_alm`, `smoothalm`, a seeded `synalm` + `alm2map` + `anafast` from
  Planck's best-fit spectrum, and a measurement of the first acoustic peak.

## Run it

```sh
export AWS_PROFILE=aws B=<your bucket>
bash measurements/healpy-real/stage-inputs.sh "$B"     # fetch on AWS once (00-fetch), check pins, stage scripts
for a in arm64 x86_64 amd; do
  spawn task run --wait --spec "$(python3 scripts/spec_arch.py healpy $a "$B" r2 measurements/healpy-real/real.task.json)"
done
python3 measurements/healpy-real/collect.py <dir holding each chip's outputs as arm64/ x86_64/ amd/>
```

[`real.py`](real.py) is ordinary healpy. [`run.sh`](run.sh) checks the staged bytes against
[`pins.sha256`](pins.sha256), records the host and runs it with `OMP_NUM_THREADS=8`.
[`mkspec.py`](mkspec.py) writes both specs and pins the staged scripts by sha256.

```python
iqu = hp.read_map(smica, field=(0, 1, 2))                  # NESTED on disk -> RING
alm = hp.map2alm(iqu, lmax=3 * 2048 - 1, iter=3, pol=True) # 18.9 M a_lm per field
cl  = hp.anafast([iqu[0] * mask_t, iqu[1] * mask_p, iqu[2] * mask_p], lmax=6143, iter=3, pol=True)
```

## Results

8 vCPU / 32 GiB (`2xlarge`) of each chip. The same `astro` env runs on all three: aarch.science's
arm64 image, and its `astro-903b8d412bd4` linux-64 twin on both x86 chips. The staged bytes are the same too:

| chip | instance | tool wall | billed | peak RSS | $/hr | compute $/result | billed $/result |
|---|---|---|---|---|---|---|---|
| Graviton4 | `m8g.2xlarge` | 432 s | 586 s | 8.69 GiB | 0.3590 | 0.0431 | 0.0584 |
| Intel (Xeon 6975P-C) | `m8i.2xlarge` | 532 s | 651 s | 8.69 GiB | 0.4234 | 0.0626 | 0.0766 |
| **AMD (EPYC 9R45)** | `m8a.2xlarge` | **272 s** | **402 s** | 8.69 GiB | 0.4869 | **0.0368** | **0.0544** |

A *result* is one full-resolution analysis with every check. *Tool wall* is `real.py`, and *billed*
runs from EC2 launch to terminate at spawn's printed price. *Peak RSS* is `ru_maxrss`.

- **The harmonic transforms are the workload.** The two polarised `map2alm` passes at ℓmax 6143
  take 231 / 298 / 125 s (Graviton4 / Intel / AMD), which is 53%, 56% and 46% of the wall. AMD runs
  them 1.85× faster than Graviton4, and that is the whole cost ranking. Intel's 8 vCPUs are 4 cores ×
  2 threads (`Thread(s) per core: 2`). Graviton4's and AMD's are 8 full cores. So the rental is
  like for like, but a per-core comparison it is not.
- **The checks cost time too.** The direct `scipy` Y_ℓm sum is single-threaded: 49–65 s for 4,000
  pixels at ℓ ≤ 128. The exhaustive pixel geometry takes 38–52 s.
- **Memory sets the family.** Python peaks at 8.69 GiB on every chip, and the 2.4 GB of staged
  FITS sit in tmpfs (half the RAM). On x86, cgroup `memory.peak` is 12.0 GB because the env install's
  page cache counts. A 32 GiB `m` box leaves ~2.5× headroom. A 16 GiB `c8g.2xlarge` was not tried.
- **Boot, staging and termination are 119–154 s of each bill.** These timings are not compute cost.
- n = 1 per chip. Every check verdict is identical on all three chips, and the outputs agree to the
  last bits (below). So a repeat would only measure timing noise.

## Why this size

Nside 2048 is the resolution Planck released its CMB maps at, and ℓmax = 3·Nside − 1 is healpy's
own default for `anafast`. A downgraded map is a different job: at Nside 1024 the transforms are
~8× cheaper (they scale as ℓmax³), and every pixel-space check covers a quarter of the pixels. The
full-sky + masked polarised pair is the minimum a pseudo-C_ℓ analysis does.

## What held at scale, on all three chips

| assertion | why it must hold | observed (Graviton4; x86 the same unless shown) |
|---|---|---|
| every column has 12·2048² pixels; map and masks NESTED; I, Q, U finite | HEALPix and the file header | 50,331,648, 0 UNSEEN |
| `nest2ring` is a permutation; `reorder` == scatter by it; nest→ring→nest; `read_map(nest=False)` == reorder of the raw columns | a reordering is a bijection | bit-exact, all pixels |
| Σ pixel areas == 4π | equal-area pixels | `12.566370614359172` exactly |
| pix→ang→pix and pix→vec→pix, both orderings | pixel centres lie in their pixels | 0 misses of 50,331,648 × 4 |
| **astropy-healpix vs healpy**: pixel centres; each code's `ang2pix` of the other's centres | two independent implementations | ≤ 1.8e-15 rad apart; 0 misses |
| 20 M random positions, healpy vs astropy-healpix | any disagreement must be a neighbouring pixel | 0 disagreements |
| `query_disc` (24 discs, 0.5–20°) == brute force over every pixel centre | the definition of `inclusive=False` | 13,939,367 pixels, 0 differ |
| `ud_grade` 2048 → 1024 … 1: Σ out × ratio == fsum(in) | an average conserves the sum | worst 7.3e-18 rel (bound 4·npix·ε) |
| `ud_grade` in RING == reorder of NESTED | ordering is bookkeeping | bit-exact |
| 2048 → 4096 repeats each parent; → 2048 returns the map | the map is float32-valued, so 4x/4 is exact in float64 | bit-exact, 201 M pixels |
| `map2alm(iter=0)` a₀₀ == √(4π)·mean | equal-area quadrature of Y₀₀ | 1.1e-21 (x86 6.8e-22; bound 4.3e-18) |
| **first acoustic peak**, SMICA masked pseudo-C_ℓ vs Planck's published PR3 TT, the same quadratic fit (ℓ 170–270) | the same sky | **221.62 vs 221.39** (best-fit theory 220.35) |
| `alm2map` (ℓ ≤ 128, SMICA's own a_ℓm) == direct Σ a_ℓm Y_ℓm with `scipy.special.sph_harm_y` at 4,000 pixels | synthesis is exact, and scipy is an independent Y_ℓm | 3.9e-14 of max\|map\| (x86 3.8e-14) |
| **`map2alm(alm2map(a))` residual shrinks by a constant factor per iteration** | Jacobi iteration on a fixed grid is a linear contraction | ×35.37 / 35.37 / 35.37 at band limit 2048, ×8.32 / 8.28 / 8.28 at 4096 |
| Galactic → equatorial `rotate_alm` (ℓ ≤ 2048) leaves every C_ℓ unchanged; G → C → G returns the a_ℓm | rotations are unitary in each ℓ | 4.1e-13; 6.8e-14 (x86 8.0e-14) |
| `smoothalm(10′)` C_ℓ == C_ℓ · exp(−ℓ(ℓ+1)σ²), computed independently | a Gaussian beam is diagonal in ℓ | 1.6e-14 (x86 2.0e-14), all 6,144 ℓ |
| seeded `synalm` from Planck's best fit, ℓ 2–2500: Σ((2ℓ+1)Ĉ/C − (2ℓ+1)) in σ | cosmic variance is χ²₂ℓ₊₁ | TT 1.68σ, EE 0.15σ (\|z\| < 5) |
| `anafast(alm2map(a))` == `alm2cl(a)`, TT and EE, ℓ ≤ 2500 at Nside 2048 | ℓ ≤ 1.22·Nside converges in 3 iterations | 4.7e-9 (bound 1e-4) |

The convergence rate is the strongest harmonic check here. A residual value says only that the
iteration got somewhere small. A constant ratio per step says the iteration is the contraction it
should be. The factor depends only on band limit / Nside, and it agrees across the three chips to 10 digits.

The first-peak bound (5 in ℓ) is loose compared with what was measured (0.23). It is set by the
estimator: an unbinned quadratic fit to a masked, un-deconvolved pseudo-spectrum. The point of the
check is that a beam, pixel-window or unit error would move the peak by tens.

## What is reported, not asserted

- **SMICA's masked pseudo-D_ℓ vs Planck's published TT** (fsky-, beam- and pixel-window-corrected):
  within 1% for ℓ < 300, then rising to 1.27 by ℓ ≈ 1500. That is the expected shape of noise bias
  in a single map's auto-spectrum. Planck's published spectrum is built from cross-spectra, which
  carry no noise bias. This was not tested further.
- **SMICA's effective intensity beam** (the map file's HDU 2) matches a 5′ Gaussian to 1.3e-3 for
  ℓ ≤ 2000 and does not include the pixel window, so the window is applied separately.
- **The inpainted columns are a different map.** Wherever the file's inpainting mask is 1,
  `I_STOKES_INP` differs from `I_STOKES` on all but 8 of 49,572,744 pixels (by up to 8.1 mK, against
  an rms of 0.11 mK). Q and U differ on every pixel. Don't treat `*_INP` as "the raw map with
  holes filled".

## Across chips

- **Intel and AMD are bit-identical** in every output: all spectra, the synfast a_ℓm and every check
  detail. They run the same linux-64 build.
- **Graviton4 vs both x86 chips.** Pixel-space results are bit-identical: the RING-ordered map, the
  `ud_grade` products, every count and the seeded a_ℓm. The harmonic outputs agree to the last bits.
  The full-sky spectra are within 1.0e-11 relative (TT, the worst case), and the masked spectra
  within 3.9e-14. Cross spectra are scaled by √(auto × auto). The first-peak fit agrees to 1e-13 in
  ℓ, the convergence factors to 10 digits, and the synfast z-scores to 1e-14.
- **Fixed point or path.** Nothing here amplifies rounding. The transforms are direct sums, plus a
  fixed three-step iteration that contracts. So the arm64/x86 differences stay at rounding level,
  and every verdict is identical. OpenBLAS reports `neoversev2` on Graviton4 and `Cooperlake` on
  both x86 chips. healpy's transforms are its own C++ code, not BLAS calls, so those kernel names
  don't bear on these results. The arm64 build runs only on arm64, so build and architecture can't
  be separated. [`results/crosschip.txt`](results/crosschip.txt) has the full comparison.

## Pins

| | |
|---|---|
| map | `COM_CMB_IQU-smica_2048_R3.00_full.fits` (2,013,312,960 bytes) |
| masks | `COM_Mask_CMB-common-Mask-{Int,Pol}_2048_R3.00.fits` |
| spectra | `COM_PowerSpect_CMB-TT-full_R3.01.txt` (published TT), `…-base-plikHM-TTTEEE-lowl-lowE-lensing-minimum-theory_R3.01.txt` (best fit). The EE file is staged too and is not used. |
| all six | sha256 in [`pins.sha256`](pins.sha256), recorded by [`00-fetch.task.json`](00-fetch.task.json) on AWS and re-checked in-task (`results/<chip>/staged.sha256`) |
| image | arm64 `quay.io/aarchsci/astro@sha256:430fb49e…` (the recipe's pin); x86 its `astro-903b8d412bd4` twin |
| versions (all chips) | healpy 1.20.0, astropy-healpix 2.0.1, numpy 2.5.3, scipy 1.18.1 |

**Data tier: a versioned public release.** Planck has no dataset on the Registry of Open Data on AWS. The Planck Legacy Archive's
products carry their release tag in the name (R3.00 / R3.01), and `00-fetch` downloads them from the
PLA's IRSA mirror (`irsa.ipac.caltech.edu/data/Planck/release_3/`) inside a spawn task. Nothing is
downloaded to a laptop. IRSA publishes no checksums, so the sha256 recorded at fetch is the pin, and
a re-fetch that differs aborts `stage-inputs.sh`. Per-chip `check.txt`, `results.json`, `host.txt`
and `footprint.txt` are in [`results/`](results/). The spectra (`spectra.npz`) are in each run's
bucket prefix.

## Caveats

One map, one size (`2xlarge`), 8 threads. Neither the core count nor the generation was swept. The
spend for this write-up was **$0.27**: the fetch, one earlier Graviton4 run that computed the
full-sky spectrum twice and used a binned peak finder, and the three runs above.
