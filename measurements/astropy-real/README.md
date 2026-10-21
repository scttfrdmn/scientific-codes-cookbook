# astropy on a real survey field: Gaia DR3 × Pan-STARRS1, Graviton4 vs Intel vs AMD

> **One whole HEALPix order-4 pixel (13.4 deg², Sagitta/Vulpecula, b = −5°): 4.1 M Gaia DR3 sources
> cross-matched to 7.5 M Pan-STARRS1 objects, and 3.9 billion pixels round-tripped through 100 real
> PS1 stack WCS headers.** Every coordinate claim is checked against the surveys' own catalogued
> values, and every bound comes from the mechanism, not from the observed value. Same result
> on all three chips; AMD is the fastest and the cheapest per result, Graviton4 4% cheaper than Intel.

Field: order-4 pixel 808 (centre RA 298.1, Dec +17.0), which is the four order-5 HATS partitions
3232–3235 of both catalogues, plus all 100 r-band stack catalogues (`.cmf`) of PS1 projection
cell 1749, which overlaps it. The Galactic plane makes this a dense field: 91% of Gaia sources get
a mutual-best PS1 counterpart within 1″.

## Run it

```sh
export AWS_PROFILE=aws
bash measurements/astropy-real/stage-inputs.sh "$COOKBOOK_BUCKET"
spawn task run --spec "$(python3 scripts/spec_arch.py astropy arm64 "$COOKBOOK_BUCKET" "$(date +%s)" measurements/astropy-real/field808.task.json)" --wait
spawn task run --spec "$(python3 scripts/spec_arch.py astropy x86_64 "$COOKBOOK_BUCKET" "$(date +%s)" measurements/astropy-real/field808.task.json)" --wait
spawn task run --spec "$(python3 scripts/spec_arch.py astropy amd "$COOKBOOK_BUCKET" "$(date +%s)" measurements/astropy-real/field808.task.json)" --wait
```

`stage-inputs.sh` copies 108 objects (7.4 GiB) server-side from STScI's RODA bucket
`s3://stpubdata`. Each copy is pinned by its S3 `versionId` **and** its sha256 (`inputs.tsv`), and a
mismatch aborts. The task re-checks every staged byte against `pins.sha256` before `real.py` runs. The x86 legs (Intel and AMD) are the recipe's `astro-903b8d412bd4` env twin: the same versions, as linux-64 builds.

The AMD leg ran under spawn 0.139.1, whose own wrapper takes ~14.7 KB of EC2's 16 KiB user data
(spawn#1246), so the resolved spec no longer fit. Its `command` text was staged unchanged as a
sha256-checked file and run with `bash`, and one guarded line recorded `lscpu` threads-per-core
and `openblas_get_corename()` to `hwprobe.txt`. That changes how the script reaches the box, not
what it computes or asserts.

## Results

One `r8g.xlarge` / `r8i.xlarge` / `r8a.xlarge` (4 vCPU, 32 GiB), same input bytes, same script:

| chip | instance | cross-match / WCS / total | $/hr | task compute | billed | **billed $/result** | peak RSS |
|---|---|---|---|---|---|---|---|
| Graviton4 | `r8g.xlarge` | 65.3 / 767.3 / 866.3 s | 0.2356 | $0.0567 | 1,079 s | **0.0706** | 8.52 GiB |
| Xeon 6975P-C | `r8i.xlarge` | 52.0 / 728.4 / 811.2 s | 0.2778 | $0.0626 | 951 s | **0.0734** | 8.46 GiB |
| EPYC 9R45 | `r8a.xlarge` | 48.7 / 575.2 / 651.0 s | 0.3195 | $0.0578 | 761 s | **0.0675** | 8.50 GiB |

- **AMD is the fastest and the cheapest per result: 25% faster than Graviton4, 4% cheaper.** Its
  WCS sweep (single-threaded wcslib) is 25% faster than Graviton4's and 21% faster than Intel's,
  which outweighs an hourly price 36% above Graviton4's. **Graviton4 is 4% cheaper than Intel and
  7% slower.** n = 1 per chip; every asserted result is identical across chips (below), so a repeat
  would only measure timing noise. `r8i` vCPUs are SMT threads (2 per core); `r8a` vCPUs are full
  cores (`lscpu`: 1 thread per core), so the AMD box has 4 cores against Intel's 2.
- **The WCS sweep is 89% of the compute.** Each of the 100 skycells is 6274 × 6258 pixels, and every
  pixel goes pix→world→pix (the reproject workload). That runs single-threaded in wcslib, which is
  why 4 vCPUs is enough. The cross-match itself (`search_around_sky` both ways,
  `match_coordinates_sky` both ways, scipy `cKDTree`, the join) takes about a minute.
- **The box is sized by memory, not cores.** The 7.4 GiB of staged inputs sit in tmpfs, which is
  half the instance RAM, and Python peaks at 8.5 GiB. That is ~16 of 32 GiB. A 16 GiB box would have
  an 8 GiB tmpfs and would not survive.
- **Boot, staging and termination are 110–213 s of each bill.** These timings are not compute cost.

## What held, on all three chips

| claim | why it must hold | Graviton4 | x86 (Intel and AMD) |
|---|---|---|---|
| Gaia's catalogued `l, b` == ICRS → Hipparcos Galactic frame (ESA 1997 §1.5.3), built on astropy's transform graph | Gaia's published convention | **4.8e-4 µas** max | same |
| Gaia's `ecl_lon, ecl_lat` == ICRS → Chapront et al. 2002 ecliptic (ε = 84381.4110″, φ = −55.42 mas) | Gaia's published convention | **7.7e-4 µas** | same |
| astropy's built-in `Galactic` / `BarycentricMeanEcliptic` + the rotation between the two frame definitions == Gaia's catalogue | the 23–36 mas offset is a convention difference, nothing else | 6.3e-4 / 8.8e-4 µas | 6.5e-4 / 9.0e-4 µas (both) |
| ICRS → Galactic, FK5, Supergalactic, ecliptic, **FK4 B1950** → ICRS, 4.1 M sources | rotations are exact inverses, and FK4's E-term iteration contracts by ~1e-6 per step | all < 9e-4 µas | same |
| `apply_space_motion` 2016 → 2000 → 2016, 2,539,463 sources with v_t < 0.01c (21,377 parallax-noise sources above it are reported) | ERFA `starpm` is its own inverse | 3.4e-4 µas | same |
| 16 yr of motion vs 16 × catalogued pm, 187,576 stars with \|pm\| > 10 mas/yr, each within its own bound | θ² + β² + θβ_t + 2\|β_r\| + 2\|v_r dt/d\| + L_B + 1 nas/(16\|pm\|) | 187,576 of 187,576 | same |
| for stars with no RV, the median shortfall is L_B = 1.550519768e-8 | see below | L_B + **3.7e-11** | same |
| HATS `_healpix_29` (hats-import) vs healpy `ang2pix` at order 29 | two implementations, and any disagreement must be an adjacent pixel | Gaia 0, PS1 2 (both adjacent) | same |
| PS1's catalogued `cx, cy, cz` == astropy's cartesian | PS1's published unit vector | 1.7e-15 | same |
| `search_around_sky(G, P)` == `search_around_sky(P, G)`, and == scipy `cKDTree` at chord 2 sin(r/2) | symmetry, and two codes agreeing except at the 1″ boundary | 4,023,363 pairs, 0 differ | same pair hash `a59ca4b0…` |
| nearest neighbour read off the pair list == `match_coordinates_sky` | the claim is "nothing closer", which is robust to ties | 1.8e-5 µas | same |
| propagating 872 high-pm (> 50 mas/yr) matches to their PS1 mean epoch | a sign or unit error would grow the gap | 251.75 → **15.59** mas median | same |
| IPP's own `RA_PSF, DEC_PSF` == header WCS at `X_PSF, Y_PSF`, 8.59 M detections, FITS 1-based | IPP's catalogue is that WCS, to one float32 ulp of the 6274-px range (122 µas) | 15.2 µas max, 0.61 µas median | same |
| every pixel of 100 skycells, pix → world → pix | wcslib's inverse | 3.93 G pixels, 8.8e-10 px | same |
| merged catalogue (3,740,227 mutual best matches) → FITS → read | decode identity | bit-identical | bit-identical |

**Across chips:** the merged catalogue's `source_id` ↔ `objID` pairing is identical on all 3,740,227
rows. The only column that differs is `sep_arcsec`, in 399,106 rows, by at most 2 ulp
(2.2e-16 arcsec), which is libm trig rounding. Every µas-scale residual above sits at the float64
floor on every chip.

**Intel and AMD are bit-identical.** Every value in `real-results.tsv` other than the timings is
the same on both, including all the µas residuals and the 0.96 µas fitted-rotation figure, and
`matches.fits` has the same full-object sha256 (`6b0e163d…`, computed by S3 server-side) on both.
Same linux-64 env and same libm. OpenBLAS selects `Cooperlake` on the EPYC 9R45 (0.3.34 has no
Zen 5 target; the Intel leg did not record its kernel), and the workload does no large blocked
GEMM, so the ARM-vs-x86 differences above are libm, and there is no Intel-vs-AMD difference.

## What the run found

- **Gaia's proper motions are per year of TCB, and ERFA propagates in TDB.** astropy converts the
  epochs (`Time(2016.0, format="jyear", scale="tcb")`) to TDB for `starpm`, so 16 TCB years become
  16(1 − L_B) years of motion. For stars with no radial velocity, the measured median shortfall is
  1.55426e-8, against L_B = 1.55052e-8. That is 25 nas over 16 years at 100 mas/yr, scientifically
  nothing, but it is a real convention mismatch, and a check whose bound left it out failed on both chips.
- **astropy 8.0.1's parquet reader raises `KeyError('_healpix_29')` on Gaia DR3's HATS files.**
  Those files carry a `table_meta_yaml` that astropy wrote before hats-import added
  `_healpix_29`. `read_table_parquet` then looks up `header_cols[col.name]` for every column it
  reads (`astropy/io/misc/parquet.py`, the description/format/unit/meta loop), and the yaml does not
  describe that column. PS1's files carry no yaml and read fine. `real.py` reads both catalogues through
  pyarrow and records the astropy failure as a diagnostic on every run. The upstream fix is
  `header_cols.get(col.name, {})`.
- **A rotation fitted over a 3.6° field cannot verify a rotation to 1 µas.** The axis along the line of sight is
  pinned only to ~resid/0.03. The fitted-vs-predicted angle came out 0.16 µas on Graviton4 and
  0.96 µas on x86 from the same data, so it is reported. The assertion applies the
  predicted matrix to every star instead. A first version that used `arccos((tr − 1)/2)` had a
  sqrt(ε) ≈ 3 mas floor and failed for that reason alone.
- **IPP's positions are float32.** `X_PSF` is `>f4`. A per-detection half-ulp bound failed by
  11 µas, because IPP's pixel arithmetic is not a single rounding. The bound that holds is the
  precision of the shared problem, which is one float32 ulp at the image's pixel range.

## Pins

| | |
|---|---|
| Gaia DR3 | `gaia_source` HATS, `s3://stpubdata/gaia/gaia_dr3/public/hats/gaia/dataset/Norder=5/Dir=0/Npix=3232–3235.parquet` (RODA tier) |
| Pan-STARRS1 DR2 | ObjectThin + MeanObject HATS (`otmo`), the same four pixels (RODA tier) |
| PS1 stacks | `rings.v3.skycell.1749.000–099.stk.r.cmf` (RODA tier) |
| all 108 objects | S3 `versionId` + sha256 in `inputs.tsv`, checked at copy time and again in-task (`pins.sha256`) |
| script | `real.py`, sha256 `1a9ecb64…` (last line of `pins.sha256`) |
| image | the recipe's `astro@sha256:430fb49e…` (arm64) and its `astro-903b8d412bd4` x86 twin |
