# photutils on a whole Pan-STARRS1 projection cell — Graviton4, Intel and AMD

> **photutils detects, deblends and measures 5.9 million sources in 3.9 billion pixels of real survey
> imaging in 12–20 minutes on 16 vCPUs, and Graviton4 is the cheapest place to run it: $0.280 billed,
> against $0.334 on AMD and $0.441 on Intel.** AMD is the fastest (719 s against 784 s). All 13 asserted
> identities hold on every one of the 100 images on all three chips. The segmentation and deblending maps
> are bit-identical across chips, and Intel and AMD agree bit for bit in every output.

Workload: all 100 r-band deep stacks of PS1 3π projection cell 1749 (`rings.v3.skycell.1749.000–099`,
centre RA 297.2, Dec +18.0, Sagitta/Vulpecula, b ≈ −5°). Each stack is 6274 × 6258 pixels with its
variance and mask images, which comes to 12.1 GB compressed. Next to them is IPP's own catalogue of the
same pixels (`.cmf`, 8.59 M psphot detections), which [astropy-real](../astropy-real/README.md) already
staged. This is the dense Galactic-plane field astropy-real cross-matched. For each image, photutils
runs the pipeline a photometry user runs:

- `Background2D` (128-px mesh, SExtractor estimator, 3×3 median filter, coverage mask);
- a convolution with a Gaussian at IPP's PSF FWHM, then `detect_sources` at 1.5σ with 10 connected pixels;
- `deblend_sources`, then `SourceCatalog`;
- exact-overlap `aperture_photometry` in four circles and three annuli at every IPP detection, with variance propagation.

## Run it

```sh
export AWS_PROFILE=aws B=<your bucket>
bash measurements/photutils-real/stage-inputs.sh "$B"     # server-side copy from RODA, pinned; stages the scripts
for a in arm64 x86_64 amd; do
  spawn task run --wait --spec "$(python3 scripts/spec_arch.py photutils $a "$B" r1 measurements/photutils-real/real.task.json)"
done
spawn task run --wait --spec "$(python3 scripts/spec_arch.py photutils arm64 "$B" r1 measurements/photutils-real/compare.task.json)"
```

[`real.py`](real.py) is ordinary photutils, one process per image (`multiprocessing`, one thread each).
[`run.sh`](run.sh) checks all 400 staged files against [`pins.sha256`](pins.sha256), records the host
and runs it. [`mkspec.py`](mkspec.py) writes the specs and pins the staged scripts by sha256.
[`compare.py`](compare.py) is a small task that diffs the three chips' per-image catalogues
(`cells.tar`) on AWS.

```python
bkg = Background2D(img, (128, 128), filter_size=3, coverage_mask=bad, bkg_estimator=SExtractorBackground())
cv  = convolve(img - bkg.background, make_2dgaussian_kernel(fwhm, size=2 * int(2 * fwhm) + 1), mask=bad)
seg = deblend_sources(cv, detect_sources(cv, 1.5 * bkg.background_rms, n_pixels=10, mask=bad), n_pixels=10)
cat = SourceCatalog(img - bkg.background, seg, convolved_data=cv, mask=bad)
```

## Results

16 vCPU / 128 GiB (`4xlarge`) of each chip, 16 worker processes. The same `astro` env runs on all
three: aarch.science's arm64 image, and its `astro-903b8d412bd4` linux-64 twin on both x86 chips. The
staged bytes are the same too:

| chip | instance | tool wall | billed | peak RSS | $/hr | compute $/result | billed $/result |
|---|---|---|---|---|---|---|---|
| **Graviton4** | `r8g.4xlarge` | 784 s | 1,068 s | 5.63 GiB × 16 | 0.9426 | **0.2053** | **0.2796** |
| Intel (Xeon 6975P-C) | `r8i.4xlarge` | 1,181 s | 1,427 s | 5.71 GiB × 16 | 1.1114 | 0.3646 | 0.4405 |
| AMD (EPYC 9R45) | `r8a.4xlarge` | **719 s** | 942 s | 5.63 GiB × 16 | 1.2781 | 0.2553 | 0.3344 |

A *result* is the whole projection cell: 100 images, 5,894,052 sources, and every check. *Tool wall* is
`real.py`. *Billed* runs from EC2 launch to terminate, at spawn's printed price. *Peak RSS* is each
worker's `ru_maxrss`. The cgroup `memory.peak` was 74.9 / 76.8 / 76.2 GB (Graviton4 / Intel / AMD).

- **Graviton4 is the cheapest per result: 16% under AMD and 37% under Intel.** AMD is 9% faster, but
  it costs 36% more per hour. n = 1 per chip. Every count and pixel map is identical across chips
  (below), so a repeat would only measure timing noise.
- **Deblending is the workload.** `deblend_sources` is 55% of the CPU time on Graviton4 (5,133 of
  9,285 CPU-seconds) and 36% on AMD. Next on Graviton4 come the exact apertures (20%) and the convolution (13%).
- **The x86 build's convolution is 3× slower on both x86 chips.** astropy's `convolve` takes 3,512
  CPU-seconds on AMD, against 1,177 on Graviton4, even though AMD is faster at every other stage (it
  deblends 1.6× faster). Intel is at 3,686. So the cause is the linux-64 build of `astropy.convolution`,
  not the silicon. The arm64 build runs only on arm64, so build and architecture can't be separated.
  `scipy.ndimage.convolve` or an FFT would likely remove the gap. That was not tested.
- **Intel's 16 vCPUs are 8 cores × 2 threads** (`Thread(s) per core: 2`). Graviton4's and AMD's are
  16 full cores. The rental is like for like, but a per-core comparison it is not.
- **Memory sets the family.** Each worker peaks at 5.6 GiB, so 16 of them need ~75 GB, which is why
  this is an `r` box. The 15 GB of staged FITS go to the root disk (`/work`, `disk_gib: 60`), not to
  tmpfs. A 64 GiB `m8g.4xlarge` would need fewer workers.
- **Boot, staging and termination take 223–284 s of each bill**, and staging the 15 GB alone takes
  47 s. These timings are not compute cost.

## Why this size

A projection cell is the unit PS1 tiles the sky into: 100 overlapping skycells, about 4° × 4°. That
is how much of a survey a photometry user takes at once. One skycell would give one background
regime. These 100 range from −149 to +92 counts median background, through the nebulosity of
Vulpecula. That range is where the detection results below come from. The deep stack is the
product people measure on, at the resolution it was released.

## What held at scale, on all three chips

Every per-image identity holds on all 100 images on every chip. The "e.g." values are skycell 000:

| assertion | why it must hold | observed (Graviton4; x86 the same unless shown) |
|---|---|---|
| `Background2D(2x + 1024)` == 2 `Background2D(x)` + 1024, background and rms | sigma clipping, the SExtractor estimator, the median filter and the interpolation are all affine-equivariant, so only rounding separates them (a clipping flip would be O(1e-3)) | 1.1e-14 / 2.6e-15 relative (bound 1e-10) |
| **`detect_sources` == scipy `ndimage.label`** on (convolved > 1.5σ) & unmasked, 8-connected, ≥ 10 px | the definition, in an independent implementation | identical pixel sets and counts in every image (3,132,904 segments) |
| deblending is a partition: the same labelled pixels, no child straddles two parents, child areas sum exactly to the parent's | that is what deblending means | exact, 3,132,904 parents → 5,894,052 sources |
| each parent's flux == the sum of its children's | a partition conserves flux | ≤ 0.011 of the 4nε·Σ\|x\| rounding bound |
| `SourceCatalog.segment_flux` and `area` == `np.bincount` over the same labels | an independent sum | flux ≤ 0.039 (x86 0.044) of the rounding bound; areas exact |
| labelled + unlabelled flux == total flux of the image (`math.fsum`) | conservation | 3.8e-6 counts (bound 4nε·Σ\|x\| = 1.1e3) |
| exact circle(18.56 px) == circle(4.16) + three annuli, at every IPP position (62,494 in skycell 000) | exact overlap weights are additive | ≤ 5.5e-16 relative |
| exact aperture weights on an image of ones == πr², all four radii | the overlap integral of a circle | ≤ 5.6e-16 |
| `aperture_sum_err`² == the aperture sum of the variance image | error propagation is a sum of variances | ≤ 6.7e-16 |
| **photutils centroid − IPP `X_PSF, Y_PSF`**, 37,961 isolated (no IPP neighbour < 25 px), unmasked stars with −15.5 < `PSF_INST_MAG` < −12, after IPP's half-pixel convention (pixel centres at 0.5) | two independent codes finding the same stars. A convention or axis error would be ≥ 0.5 px | median **+0.0019 / +0.0006 px**, MAD 0.080 / 0.072 (bound 0.05) |

The detection identity is the strongest check here. A threshold or a connectivity bug, or an
off-by-one in the pixel count, would change some of 3.1 M regions. Re-implementing the definition
compares every pixel, which no band on a source count could do.

## What is reported, not asserted

- **IPP recovery: 37,907 of the 37,961 isolated bright IPP stars (99.86%) lie in a photutils
  segment.** All 54 misses are in four skycells (037, 039, 065, 074). There the 128-px background
  mesh follows nebulosity: the local background is 255–2,323 counts, against an all-cell median of
  −84. The 1.5σ threshold rises with it, to 540–2,070 against ~170 elsewhere, so a star's convolved
  peak clears it over fewer than 10 pixels. The detection identity above shows photutils did what its
  definition says. The misses come from the configuration in a nebulous field, which a smaller mesh
  would change. This was not tested. The per-star diagnostics are in each `results.json` (`missed`).
- **Aperture flux against IPP's r = 12 px radial aperture** (the unconvolved `SkyChip.xrad` row), on
  the same stars: photutils / IPP has a median of 1.124 (MAD 0.098) on photutils' own background, and
  0.980 (MAD 0.083) with no background subtracted. The difference is the sky model. IPP measures
  after subtracting its PSF models of neighbours, against its own sky (`SKY`, −35 in skycell 000,
  where `Background2D` gives −51). So this is a comparison of two pipelines' choices, not of aperture
  arithmetic. The arithmetic is asserted above.
- **IPP's `AP_FLUX` and `AP_NPIX` are not an aperture sum.** At its `AP_MAG_RADIUS` of 11 px, IPP
  reports ~337 pixels, against the 380 a circle holds. That is an effective radius of 10.36 px. At
  11 px the flux is 6–9% off a plain sum (one-skycell probe). Those columns are not used.
- **Totals:** 3,132,904 segments → 5,894,052 deblended sources, against 8,594,614 IPP rows. IPP goes
  deeper and splits more. Sources hold a median 98.9% of each image's background-subtracted flux.

## Across chips

- **Intel and AMD are bit-identical** in every output of all 100 images: background, rms, segment and
  deblend maps, fluxes, centroids and aperture sums. They run the same linux-64 build.
- **Graviton4 vs both x86 chips.** The segmentation and deblending maps are bit-identical in all 100
  images, so every source has the same pixels on every chip and every count agrees. The floating-point
  outputs agree to the last bits: segment fluxes within 8.2e-16 relative, centroids within 9.1e-13 px,
  and aperture sums within 6.4e-10 relative. That metric divides by the sum itself, so it
  is largest where a background-subtracted sum is near zero. The background matches bit for bit in none of the 100 images, and the rms in one.
  The difference is rounding, at 1e-14 per the affine check, and it never moves a pixel across the
  detection threshold.
- **Fixed point or path.** Nothing here iterates or amplifies rounding. Every step is a direct sum,
  a median, or a threshold, so the arm64/x86 differences stay at rounding level and every verdict and
  count is identical. The steps are sums, medians, thresholds and a direct convolution, not BLAS
  calls, so `openblas_get_corename()` was not recorded. [`results/crosschip.txt`](results/crosschip.txt)
  has the full comparison.

## Pins

| | |
|---|---|
| images | `rings.v3.skycell.1749.{000..099}.stk.r.unconv{,.wt,.mask}.fits`, `s3://stpubdata/panstarrs/ps1/public/rings.v3.skycell/1749/` (RODA tier), 300 objects |
| catalogues | the same skycells' `.stk.r.cmf`, staged and pinned by [astropy-real](../astropy-real/inputs.tsv) |
| all 400 | S3 `versionId` + sha256 in [`inputs.tsv`](inputs.tsv). They are checked at copy time and again in-task against [`pins.sha256`](pins.sha256) (`staged.sha256` in each run's prefix) |
| image | arm64 `quay.io/aarchsci/astro@sha256:430fb49e…` (the recipe's pin); x86 its `astro-903b8d412bd4` twin |
| versions (all chips) | photutils 3.0.0, astropy 8.0.1, numpy 2.5.3, scipy 1.18.1 |

**Data tier: RODA.** STScI's `stpubdata` bucket is versioned, so each object is pinned by `versionId`
as well as by content. `stage-inputs.sh` copies server-side (`CopyObject`), so nothing passes through
a laptop, and S3 computes the sha256 of what it wrote. The bucket is `Payer: BucketOwner`, so the
only charge is the 12 GB inter-region copy, about $0.24. Each chip's `check.txt`, `results.json`,
`host.txt` and `footprint.txt` are in [`results/`](results/). The per-image catalogues (`cells.tar`,
403 MB) are in each run's bucket prefix.

## Caveats

One projection cell, one band, one size (`4xlarge`), and one worker per vCPU. Neither the core count
nor the generation was swept. The PS1 stacks are asinh-compressed, and `real.py` undoes that with the
documented `BOFFSET + 2 BSOFTEN sinh(x / 2.5 log10 e)`. The spend for this write-up was **about
$2.00**. That covers two probes and three two-image sizing runs, two earlier Graviton4 runs (one
asserted IPP recovery and failed on the 54 nebular misses; one added the diagnostics that explained
them), the three runs above, the comparison, and the copy.
