# specutils on a DESI DR1 healpix group: Graviton4, Intel and AMD

> **specutils re-measures DESI DR1's own emission-line catalogue for 40,800 spectra in 2–4 minutes
> on 16 vCPUs. On 99.98% of well-posed fits it lands on DESI's χ² minimum, and agrees there to
> 0.012 σ at worst.**
> Graviton4 is the cheapest per result ($0.085 billed) and AMD the fastest (125 s). The specutils
> default fitter misses that minimum on 97% of [O III] fits, by a median 1.9% in flux, because
> its analytic Jacobian ignores tied parameters. Intel and AMD agree bit for bit. Graviton4 differs
> from them only along optimiser paths, never in where a well-posed fit converges.
> **One assertion fails on Intel and AMD, reported not loosened:** on one Hγ non-detection (pixel 10084,
> row 257, S/N −0.2) Graviton4's fit ends with a singular covariance and is marked failed, as DESI's
> catalogue has it, while x86's same code from the same start ends with a valid one and is marked a success.

Workload: all 100 nside-64 pixels of healpix group 100 (`10000`–`10099`) of DESI DR1 (`iron`),
main survey, dark program. That is 40,800 coadded spectra (33,530 targets and 7,270 sky fibres) in
18.2 GB of `coadd-*.fits`. Next to each coadd sits DESI's own catalogue of the same spectra,
`emline-*.fits`, written by desispec's emission-line afterburner. It fits [O II], Hδ, Hγ, Hβ, [O III]
and Hα with Gaussians at the redrock redshift and catalogues each line's flux, width, continuum and
EW. For every spectrum, this run:

- reads the coadd with specutils' own `DESI coadd` loader;
- rebuilds emlinefit's input: the three cameras concatenated and corrected for Galactic extinction
  at the SFD E(B−V);
- fits all six lines with `specutils.fitting.fit_lines`, on emlinefit's pixels, from its
  starting point, under its model and bounds;
- also runs DESI's own `emlines_gaussfit`, at the iron commit, on the same arrays. That places any
  disagreement: in the inputs, or in specutils.

## Run it

```sh
export AWS_PROFILE=aws B=<your bucket>
bash measurements/specutils-real/stage-inputs.sh "$B"      # server-side copy from RODA, pinned; stages the scripts
spawn task run --wait --spec "$(python3 scripts/spec_arch.py specutils arm64 "$B" r1 measurements/specutils-real/stage-sfd.task.json)"
for a in arm64 x86_64 amd; do
  spawn task run --wait --spec "$(python3 scripts/spec_arch.py specutils $a "$B" r1 measurements/specutils-real/real.task.json)"
done
spawn task run --wait --spec "$(python3 scripts/spec_arch.py specutils arm64 "$B" r1 measurements/specutils-real/compare.task.json)"
bash measurements/specutils-real/collect.sh "$B" r1
```

[`real.py`](real.py) runs one process per pixel. [`check.py`](check.py) asserts the identities.
[`run.sh`](run.sh) checks every staged byte against its pin, records the host and runs both.
[`mkspec.py`](mkspec.py) writes the specs and pins the staged scripts. [`compare.py`](compare.py)
diffs the three chips' per-fit outputs on AWS. [`probe_flip.py`](probe_flip.py) is the one-spectrum
probe behind "Across chips" below.

```python
sl   = SpectrumList.read(coadd, format="DESI coadd")                 # b, r, z; flux, ivar, mask
g    = models.Gaussian1D(F0 / (s0 * sqrt(2 * pi)), obs, s0, fixed={"mean": True}, bounds={"stddev": (1e-5, 10)})
fit  = fit_lines(spec, models.Const1D(cont, fixed={"amplitude": True}) + g,
                 window=(lo * u.AA, hi * u.AA), weights="unc", maxiter=100000, acc=1e-12)
# tied components ([O III] 4960 to 5007): pass estimate_jacobian=True, or the fit is wrong
```

## Results

16 vCPU / 32 GiB (`c8g/c8i/c8a.4xlarge`), 16 worker processes, one OpenBLAS thread each. The same
`astro` env runs on all three chips: aarch.science's arm64 image (the recipe's pin), and its
`astro-e2ee19affec3` linux-64 twin on both x86 chips. The staged bytes are the same too.

| chip | instance | tool wall | billed | peak RSS | $/hr | compute $/result | billed $/result |
|---|---|---|---|---|---|---|---|
| **Graviton4** | `c8g.4xlarge` | 169 s | 481 s | 1.08 GiB × 16 | 0.6381 | 0.0300 | **0.0853** |
| Intel (Xeon 6975P-C) | `c8i.4xlarge` | 248 s | 527 s | 1.09 GiB × 16 | 0.7497 | 0.0516 | 0.1097 |
| AMD (EPYC 9R45) | `c8a.4xlarge` | **125 s** | **401 s** | 1.09 GiB × 16 | 0.8622 | **0.0299** | 0.0960 |

A *result* is the whole group: 40,800 spectra, 244,800 line slots, 149,477 Gaussian fits by each of
specutils and DESI's function, and every check. *Tool wall* is `real.py`. *Billed* runs from EC2
launch to terminate. On x86 it includes installing the env twin. *$/hr* is spawn's
`cost_estimate` over its task window. *Peak RSS* is the largest worker's `ru_maxrss`. The cgroup
`memory.peak` was 9.4 / 13.6 / 13.6 GiB (Graviton4 / Intel / AMD).

- **Graviton4 is the cheapest per result billed:** 11% under AMD and 22% under Intel. AMD runs the
  fits 1.35× faster but costs 35% more per hour, so compute $/result is a tie ($0.0299 vs $0.0300).
  n = 1 per chip. Every count and every converged fit agrees across chips (below), so a repeat
  would only measure timing noise.
- **Intel's 16 vCPUs are 8 cores × 2 threads** (`Thread(s) per core: 2`). Graviton4's and AMD's are
  16 full cores. The rental is like for like; the cores are not.
- **Boot, staging and termination are 53–69% of every bill.** 18 GB of coadds land on the root
  disk (`disk_gib: 60`), not the half-RAM tmpfs. These timings are not compute cost. The work is
  ~1 CPU-second per 15 spectra, so a whole DR1 program is the same per-pixel job, many times over.
- **The box is sized from a measured run.** A two-pixel sizing run on `m8g.large` measured
  15–22 s per pixel per core at 0.65 GB per worker, so this is a `c` box. The 16 workers here peaked at 9–14 GiB.

## Why this size

A healpix group is the unit DESI files its spectra in: 100 pixels, about 84 deg² of sky. A
line-measurement study takes a sample like this and re-measures it, often with a different model.
That means tens of thousands of spectra, not a handful. These 40,800 cover the dark program's mix of
ELGs, LRGs and QSOs, plus sky fibres, so every one of the six lines is fitted thousands of times. The coadd is the product DESI releases for this, and the emline
file is DESI's own measurement of it.

## What held at scale, on all three chips

| assertion | why it must hold | observed |
|---|---|---|
| specutils' `DESI coadd` loader == the raw HDUs (wavelength, flux, ivar, mask, TARGETID) | it is the same bytes | bit-identical, 100 of 100 pixels |
| every spectrum's TARGETID unique and the same set in coadd and catalogue | one row per spectrum | 40,800; 244,800 line slots |
| **`CONT` from the rebuilt input == the catalogue's** | a median of the same float32 fluxes | **149,477 of 149,477 bit-identical** |
| catalogue `EW` == `FLUX / ((1+z) CONT)` | emlinefit's rest-frame definition | ≤ 1.6e-7 relative (float32) |
| DESI's own function, re-run here, == the catalogue `FLUX` on well-posed fits | same code, same inputs | ≤ 6.5e-4 σ (x86: 2.1e-4 σ) |
| fit_lines' window pixels − free parameters == the catalogue's `NDOF` | the same pixels | every one of 149,477 fits |
| **specutils and DESI reach the same χ² minimum** on ≥ 99.9% of well-posed fits | the same least-squares problem | **34,363 of 34,369** (99.98%) |
| at that minimum, specutils `F` == DESI's, ≤ 2√(10·ftol·χ²) σ | both stop at scipy's ftol = 1e-8 | max 0.012 σ, 0.75 of the bound |
| at that minimum, specutils `EW` == the catalogue's | follows from F | ≤ 1.2e-3 relative |
| the catalogue's success flag (`NDOF ≥ 0`) reproduced, sub-pixel fits (σ < 0.34 Å) exempt | the same decision | Graviton4: 1 sub-pixel flip, **passes**. Intel and AMD: **fails**, see below |

*Well-posed* means DESI's fit has σ between DESI's own 0.34 Å resolution floor and 9.99 Å, an
[O II] ratio off its bounds, and S/N ≥ 3. That leaves 34,369 fits. The tolerance on F comes from the
reference's own stopping rule. curve_fit stops when χ² moves by less than ftol = 1e-8 relative. That
leaves a fit up to √(ftol·χ²) parameter-σ from the true minimum, and two fits up to twice that apart.
With 10× margin, the bound is 0.005–0.08 σ per fit. My first bound left out the factor of two. Two
fits on the first full run exceeded it by up to 1.5× while their χ² agreed within the ftol rule,
which is what that error predicts.

The `CONT` row needed one correction to the input, and it is the strongest check here. The first
full run took E(B−V) from the fibermap's `EBV` column. It reproduced only 45,248 of 120,438 `CONT`
values bit for bit, with residuals up to 3.5e-6. emlinefit instead looks E(B−V) up on the SFD maps
at `TARGET_RA, TARGET_DEC`. The fibermap column differs from that lookup by up to 1.3e-6 mag on
targets, and is 0 for sky fibres, which emlinefit also fits. Staging the SFD maps and calling
desiutil's lookup made every `CONT` bit-identical, sky fibres included.

## What is reported, not asserted

- **specutils' default fitter is wrong on tied models.** `fit_lines` defaults to
  `TRFLSQFitter`, which uses the compound model's analytic derivative, and that derivative ignores
  `tied` constraints. For [O III], with 4960 tied to 5007 in amplitude and width, it stops off the
  minimum on **6,608 of 6,812** well-posed fits. Its χ² is above DESI's on 6,797 of them. Flux is off
  by a median **1.9%** (p90 9.6%, median 0.25 σ). `estimate_jacobian=True`
  fixes it: the [O III] row above is that run. The same default with no ties (the four Balmer
  lines) is exact. [O II]'s free ratio is bounded to [0.1, 1], which a sum of `Gaussian1D` cannot
  express, so it is emlinefit's own parametrisation as an astropy `custom_model`, which has no
  analytic derivative.
- **The covariance convention differs.** astropy treats `weights` as absolute errors. curve_fit,
  as emlinefit calls it, rescales by χ²/dof. specutils' `FLUX_IVAR` matches the catalogue only after
  that factor: median 2.8e-5 relative, p99 4.0e-4. Each fitter's own Jacobian sets the residual.
- **6 well-posed fits found different local minima** from the same start. specutils was lower in 3
  and DESI in 3. In four, specutils' σ collapses below the 0.34 Å floor. The problem is not convex, so neither
  answer is wrong. The same 6 appear on every chip.
- **specutils' `line_flux` over ±3σ** of the fitted line, against the Gaussian flux, at S/N ≥ 20:
  median 1.010 ([O II], 2,515 fits), 1.027 ([O III]), 1.014–1.018 (Balmer). A pure Gaussian gives
  0.9973. Real profiles carry more flux in their wings than the Gaussian model. The 1–3% excess is
  that difference, and why a boxcar and a fit disagree.
- **`centroid` of the continuum-subtracted line against the redrock redshift**, at S/N ≥ 20: median
  −0.4 km/s ([O III], MAD 3.2) and −0.6 km/s (Hα, MAD 1.7). Hβ is −2.3 and Hγ −3.9. A pixel is
  ~48 km/s.

## Across chips

- **Intel and AMD are bit-identical** in every output: every catalogue reproduction, every
  specutils and DESI fit, every flux, χ², boxcar and centroid. They run the same linux-64 build and
  the same OpenBLAS kernel (`Cooperlake` on both: AMD falls back to an Intel-named one).
- **Graviton4 vs both x86 chips, fixed points.** `CONT`, `line_flux` and `centroid` (a median and
  direct sums) are bit-identical in all 149,849 / 143,612 / 109,440 values. No well-posed fit
  converges to a different minimum on any chip.
- **Graviton4 vs both x86 chips, paths.** The fitted parameters differ only in the optimiser's last
  steps. specutils' F differs by a median 6e-13 σ (max 2.2e-7 σ). DESI's curve_fit, with its
  finite-difference Jacobian, differs by a median 1.7e-8 σ (max 6.5e-4 σ). Both are far inside the
  convergence bound.
- **The one x86 failure is a path.** On pixel 10084, row 257, Hγ is a non-detection (S/N −0.2).
  On Graviton4 (`neoversev2`), curve_fit walks to σ = 0.054 Å with an all-zero covariance, so
  emlinefit marks the fit failed. The catalogue, computed at NERSC, says the same. On Intel and AMD
  (`Cooperlake`), the same code from the same start walks to σ = 1.08 Å with a valid covariance,
  and the fit "succeeds". The exemption for sub-pixel fits could not see this case: the catalogue
  stores no σ for a failed fit. So the assertion fails on both x86 chips, and it is recorded here,
  not loosened. A success flag decided by which local minimum a non-detection falls into is a
  path, not a fixed point. [`probe_flip.py`](probe_flip.py) shows both hosts' `popt` and `pcov`
  (`results/` holds each chip's `check.txt`).

## Pins

| | |
|---|---|
| spectra + catalogue | `dr1/spectro/redux/iron/healpix/main/dark/100/100{00..99}/{coadd,emline}-main-dark-*.fits` in `s3://desidata` (RODA tier, us-west-2), 200 objects |
| pin | the sha256 DESI publishes in each pixel's `redux_iron_healpix_main_dark_100_<pix>.sha256sum` ([`inputs.tsv`](inputs.tsv)). desidata is not versioned, so the content hash is the pin. It is checked at copy time (S3's SHA256 of what CopyObject wrote) and again in-task against [`pins.sha256`](pins.sha256) |
| SFD maps | `SFD_dust_4096_{ngp,sgp}.fits` from `kbarbary/sfddata@7a5fe7fa`, checked by git blob id on AWS ([`stage_sfd.py`](stage_sfd.py)), then by sha256 ([`sfd.sha256`](sfd.sha256)) |
| emlinefit's definition | desispec `py/desispec/emlinefit.py` @ `952a9a29d0` (unchanged in code from 2022-10-21 to iron's run on 2023-01-30) and desiutil `dust.py` @ `8b51cfbaffcb`, by sha256 in [`mkspec.py`](mkspec.py) |
| image | arm64 `quay.io/aarchsci/astro@sha256:c09e88de…` (the recipe's pin); x86 its `astro-e2ee19affec3` twin |
| versions (all chips) | specutils 2.4.0, astropy 8.0.1, numpy 2.5.4, scipy 1.18.1 |

**Data tier: RODA.** `stage-inputs.sh` copies server-side, so nothing passes through a laptop. The
bucket is in-region and `Payer: BucketOwner`, so the copy costs only requests. The SFD maps
(134 MB) are fetched inside a spawn task.

## Caveats

One healpix group, one program, one size (`4xlarge`), and one worker per vCPU. Neither the core
count nor the generation was swept. The per-pixel work is independent, so the cost scales linearly
with pixels. The spend for this write-up was **about $0.55**. That covers two sizing runs, a
first full Graviton4 run that failed on fibermap-EBV `CONT` and on my convergence bound, three
diagnostic tasks, the SFD staging, a second Graviton4 run, the three runs above, two comparisons
and two probes.
