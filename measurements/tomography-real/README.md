# TomoPy on a whole synchrotron scan — Graviton4, Intel and AMD

> **TomoPy reconstructs all 2,160 slices of a real 2560 × 2160 × 1501 TOMCAT scan in under five
> minutes on Graviton4 and AMD, and every slice matches the depositors' own reconstruction on all
> three chips.** Graviton4 is cheapest per result. Gridrec is not bit-reproducible across chips
> (float32 round-off at ~8e-5 of the peak), and Intel and AMD give byte-identical volumes.

Workload: **the BBii sandstone scan as its depositors published it** (Zenodo record 4912435; SLS
TOMCAT beamline X02DA, 21.5 keV, 0.65 µm pixels; Bruns, Stipp & Sørensen, *Adv. Water Res.* 2017).
It holds 30 darks, 100 + 100 flats and 1,501 projections over 0–180°, 17.1 GB as TIFFs, plus the
depositors' own GridRec reconstruction of every slice (25.3 GB). The pipeline is a beamline user's:
dark/flat normalise, `-log`, find the centre, then gridrec with the Shepp-Logan filter on every
detector row.

## Run it

```sh
export AWS_PROFILE=aws B=<your bucket>
bash measurements/tomography-real/stage-inputs.sh "$B"     # 42.4 GB, Zenodo -> bucket, fetched on AWS
for a in arm64 x86_64 amd; do
  spawn task run --spec "$(python3 scripts/spec_arch.py tomography $a "$B" "$(date +%s)" \
    measurements/tomography-real/real.task.json)" --wait
done
bash measurements/tomography-real/collect.sh "$B"           # small files here; slice diffs run on AWS
```

[`real.py`](real.py) is the workload and its checks; [`real.sh`](real.sh) records the host and runs it.
The calls a user writes:

```python
t = tomopy.minus_log(tomopy.normalize(proj, flat, dark, ncore=16), ncore=16)
c = tomopy.find_center_vo(t[:, r:r+1], smin=-60, smax=60, step=0.25)    # 1323.25; TOMCAT used 1323.11
vol = tomopy.recon(t, theta, center=1323.11, algorithm="gridrec", filter_name="shepp", ncore=16)
```

## Results

16 vCPU, `r8*.4xlarge`, 128 GiB, one per chip. The arm64 run uses the recipe's image digest. The x86
runs use the `tomography` env's linux-64 twin (tomopy 1.15.3, astra 2.5.0, numpy 2.5.4, FFTW 3.3.11).
Same staged bytes, same `ncore`:

| chip | instance | cores × threads | TomoPy wall (one volume) | + overhead | billed (whole task) | peak RSS (main / box) | $/hr | compute $/result | $/volume, checks excluded | billed $ (whole task) |
|---|---|---|---|---|---|---|---|---|---|---|
| **Graviton4** | `r8g.4xlarge` | 16 × 1 | **279 s** | 463 s | 5710 s | 22.7 / 24.2 GiB | 0.9426 | **0.073** | **0.121** | **1.50** |
| Xeon 6975P-C | `r8i.4xlarge` | 8 × 2 | 466 s | 673 s | 8321 s | 23.0 / 24.4 GiB | 1.1114 | 0.144 | 0.208 | 2.57 |
| EPYC 9R45 | `r8a.4xlarge` | 16 × 1 | 282 s | 443 s | 5113 s | 23.0 / 23.9 GiB | 1.2781 | 0.100 | 0.157 | 1.82 |

**TomoPy wall** is the pipeline: read all 1,731 TIFFs into RAM, find the centre (Vo on 9 rows plus
the 0/180° mirror), normalise and `-log`, and gridrec on all 2,160 rows. **+ overhead** adds
spawn's overhead (stage-in of 42.4 GB, Docker, image pull or env install, stage-out: 161–207 s) and
is what one volume costs without the checks; it is constructed, not a window. **Billed** is the
whole task, spawn's window from wrapper start to stage-out done
([`results/billed-windows.txt`](results/billed-windows.txt)), checks included. $/hr is spawn's estimate. **Peak RSS**: *main* is
`ru_maxrss`; *box* is the container cgroup's `memory.peak`.

| phase (s) | Graviton4 | Xeon | EPYC |
|---|---|---|---|
| read 17.1 GB of zipped TIFFs (16 threads) | 11.4 | 18.2 | 9.4 |
| centre: Vo on 9 rows + 0/180 mirror | 104.2 | 204.3 | 97.9 |
| normalise + `-log`, all rows | 12.8 | 20.1 | 15.6 |
| **gridrec, 2,160 slices of 2560²** | **150.7** (0.070 s/slice) | **223.3** (0.103) | **159.1** (0.074) |
| *check: compare 2,160 slices with the published ones* | *263* | *570* | *324* |
| *check: astra FBP + re-projection + 30 CGLS iterations, 3 rows* | *4906* | *6980* | *4272* |

- **Gridrec is 54% of the pipeline, and Vo's centre search over a third.** `find_center_vo` on 9 rows
  costs as much as two thirds of the whole volume's gridrec. A centre found once per scan, not per
  slice, is the right habit.
- **Graviton4 is cheapest per volume: 23% under AMD and 42% under Intel, checks excluded** (18% and
  42% on the whole billed task). AMD's cores are as
  fast (gridrec within 6%), but its hour costs 36% more. Intel's box has 8 physical cores with SMT,
  and its gridrec is 1.5× slower.
- **The box is picked by staging, not by TomoPy.** The process peaks at 23 GiB, of which 15.5 GiB is
  the raw projections as uint16. But both zips sit in the half-RAM `/tmp` tmpfs (39.5 GiB), so the
  box needs 128 GiB. A 64 GiB `m8g.4xlarge` has a 32 GiB tmpfs. A run that stages only `proj.zip`
  (no comparison against the published slices) should fit a 64 GiB box; that was not run. Copying
  at tuned concurrency stages both zips in 27 s instead of 149 s. Reading them through lith on a
  64 GiB `m8g.4xlarge` works, with every assertion identical, but fetches 10× the bytes
  ([lith-vs-copy-real](../lith-vs-copy-real/README.md)).
- **The checks are the bill here, not TomoPy.** Whole-task windows were 5,710 s / 8,321 s / 5,113 s
  ($1.50 / $2.57 / $1.82). 86% of that is astra's CPU CGLS: 30 iterations on 2656² takes 78 min on
  Graviton4, and one forward projection of a 2656² slice takes 54–75 s depending on the chip.
- n = 1 per chip; run-to-run repeatability was not measured. Within a chip, re-pairing slices moves
  a voxel by < 1e-6 of the peak (below), too little to move any asserted decision. These timings
  are not compute cost: staging is in the overhead and billed columns (boot comes before the window).

## What held at scale, on all three chips

Every row is asserted inside the task by [`real.py`](real.py); a failure fails the run.

| claim | why it must hold | Graviton4 | Xeon | EPYC |
|---|---|---|---|---|
| staged zips | sha256 `11e31de3…467e` / `539178…b5c4` (Zenodo's MD5 checked at fetch) | ok | ok | ok |
| **30 + 100 + 1,501 + 100 TIFFs, 2560 × 2160 uint16; 2,160 published slices; 0.120° step** | the scan log | exact | exact | exact |
| **which published slice each row is, found by searching all 2,160 × 8 orientations from two rows** | must be one unit-slope map | slice = row + 1, `rot270`, corr 0.62 vs 0.10 anywhere else | same | same |
| **three independent centres agree with TOMCAT's** within 1 px (the resolution element) | one axis | Vo 1323.25, mirror 1323.38, TOMCAT 1323.11 | same | same |
| **gridrec reproduces the published slices best at TOMCAT's centre**, on a 0.25 px sweep | the same reconstruction | **1323.11 exactly** | same | same |
| **every slice matches its own published slice better than either neighbour** | registration | 2,160 of 2,160, margin ≥ 0.087 | same | same |
| **every slice agrees with the published one above the 1/7 FRC criterion**, rings 8 to half Nyquist | the same data, reconstructed twice | 2,160 of 2,160, worst 0.272, median 0.523 | same | same |
| every voxel finite | — | 0 non-finite | 0 | 0 |
| **gridrec at `ncore=1` vs 16, and a row alone vs inside a block** | same arithmetic, different slice pairing | 7.4e-07 / 6.6e-07 of the peak (bound 1e-5) | 6.7e-07 / 4.6e-07 | same as Xeon |
| **astra's CPU FBP vs TomoPy's gridrec**, same sinogram, same centre: FRC > 1/7 rings 8–640 | two codes, one transform | min 0.685–0.704 on 3 rows | same | same |
| **re-projecting gridrec's slice fits the measured sinogram better than a slice 10 px off-centre** | consistency | 0.158 vs 0.177 (row 1080: 0.159 vs 0.182) | same | same |
| **CGLS's residual falls at every one of 30 iterations** | a property of CG | 0.240 → 0.102 (row 1080) | same | same |

**Why the published comparison is an FRC, from ring 8.** This is interior tomography. The core is
wider than the 1.66 mm field of view, so no projection sees air (median transmission 0.31, never
above 0.65). FBP's lowest frequencies then depend on how each code pads the truncated sinogram, and
the top octave is noise shaped by each code's filter window. So per-pixel correlation with the
published slice is only 0.50–0.81, while the Fourier ring correlation is 0.93 at ring 160 and never
falls below 1/7 before Nyquist from ring 8 up. Rings 1–7 (features ≥ 1/8 of the image, i.e. the scale
of the field itself) fall below 1/7 on 1,985 of 2,160 slices. That is the cupping each code's padding
leaves, recorded per slice, not asserted. The ring-8 cut was placed after the sizing probe showed it
([results/probe](results/probe/), from an earlier `real.py`); it is stated as such, not derived.
([Compare like with like](../../practices/cross-checks.md).)

**TOMCAT counts pixels from 1.** Its log says "Center 1324.11", and its ROI is "1 - 2560". In
TomoPy's 0-based pixels that is 1323.11, and the sweep against the published slices peaks there to
the 0.25 px grid. Vo's method (1323.25) and the 0/180° mirror (1323.38) agree. Feeding TomoPy the
log's number unchanged costs a full pixel: correlation with the published slices falls from 0.645
to 0.432.

Reported, not asserted:
- The published values are 0.43× ours (median slope, 0.34–0.53 by slice), with ~zero offset. The
  scale TOMCAT's GridRec writes was not established.
- Re-projected, gridrec's slice fits the data at a best scale of 1.00 (so it is in the data's own
  units, attenuation per pixel) and astra's FBP slice at 0.59–0.65.
- CGLS passes gridrec's re-projection residual at iteration 3–4, and reaches 0.101–0.127 by
  iteration 30, against gridrec's 0.158–0.199. Its FRC with gridrec is 0.96 at ring 160.

## Across chips: decisions fixed, bits a path

From [`compare.py`](compare.py) ([output](results/compare.txt)):

- **Fixed on all three chips:** every count, the slice map and orientation, all three centres, the
  sweep's peak, the registration result, which slices pass FRC, and every FRC crossing ring. Also the
  per-slice correlation summaries to the printed digit.
- **Graviton4 vs either x86 chip: no slice is byte-identical.** The saved slices differ by 7.3e-05 to
  8.9e-05 of the peak, and the per-slice published correlation by up to 4e-06. The CGLS images,
  30 iterations of a path, differ by up to 9.6e-04. That is two orders above the within-chip pairing
  difference (7e-07). Where the bits first part (the float32 `-log`, FFTW's codelets, libtomo's
  gridding) was not established. It is a float32 path difference, not a different answer.
- **Intel vs AMD: identical.** All 2,160 slices, the three CGLS images and every recorded value are
  byte-identical, on the same env twin and the same OpenBLAS kernel (`Cooperlake` on both).
- **Gridrec is not bit-reproducible even on one chip**, which is why the within-chip invariance is a
  bound: `ncore` changes how slices are paired into gridrec's complex transform.

## Pins

| | |
|---|---|
| data | Zenodo **record 4912435 v1.0**, doi:[10.5281/zenodo.4912435](https://doi.org/10.5281/zenodo.4912435) (CC BY 4.0). `proj.zip` 17,104,540,917 bytes, MD5 `026ca4569ece6cf9f0e9675a2930c0a1` (Zenodo's), sha256 `11e31de32d1d379cce26c538b31e7e2b8697bc0b960a95de02e96b860bd6467e`; `rec.zip` 25,257,818,414 bytes, MD5 `cb5ab7c979c227a8a72d091adb3061dd`, sha256 `539178626fce4d280efcbf61c78cdec3b8372a8475ab5786ef6cd7de48feb5c4`. Bruns, Stipp & Sørensen, *Adv. Water Res.* 105, 96–107 (2017), doi:10.1016/j.advwatres.2017.04.020 |
| tier | durable public id (Zenodo DOI). Fetched on AWS by [`00-fetch.task.json`](00-fetch.task.json), kept only on a size + MD5 match |
| scripts | `real.py`, `real.sh`, pinned with the data in [`pins.sha256`](pins.sha256) and checked in-task |
| image | the recipe's `tomography@sha256:ea7b4900…` (tomopy 1.15.3 / libtomo 1.15.3, astra-toolbox 2.5.0, numpy 2.5.4, FFTW 3.3.11, OpenBLAS 0.3.34 `neoversev2`); x86 via `envs-x86_64/tomography-9897b62718c6` |

**Why this dataset:** it is a complete beamline scan at the detector's full size, the unit a
synchrotron user reconstructs. It is also one of the few public scans whose depositors published
their own reconstruction of every slice alongside the raw data. That turns "TomoPy produced a
volume" into "TomoPy reproduced the beamline's volume", slice by slice.

## Caveats

- **No stripe removal.** The depositors ran TOMCAT's own ring removal, whose parameters (`1,0,0`)
  don't map onto a TomoPy call. The FRC is judged without it. In a diagnostic on row 1080, TomoPy's
  `remove_stripe_fw` lowered the per-pixel agreement with the published slice slightly (0.677 → 0.661).
- **`real.sh` runs 30 CGLS iterations**, while `real.py`'s docstring gives 10 as its default when
  run without `real.sh`. The three runs are 30.
- **astra's CPU FBP takes only a plain parallel geometry** ("FBP currently only supports parallel
  projection geometries" for `parallel_vec`). So the sinogram is edge-padded 48 px a side and
  Fourier-shifted 43.61 px to put the axis at the detector's middle. The re-projection residuals
  are read over the measured columns only.
- **Spend:** $7.19 in total. That is $0.35 for the Zenodo fetch (2 h at 3–4 MB/s from Zenodo),
  $0.96 for four sizing probes and a diagnostic, and $1.50 / $2.57 / $1.82 for the three full runs.
