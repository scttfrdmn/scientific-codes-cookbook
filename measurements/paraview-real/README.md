# ParaView on a real workload — a week of hourly ERA5 3D temperature, three chips

> **168 hourly 0 °C isosurfaces of a 38-million-point atmosphere: every topological count matches an
> independent numpy recount on Graviton4, Intel and AMD, and all 168 rendered frames are
> byte-identical across the three chips.** AMD is the fastest and the cheapest. Graviton4 is 1.35×
> faster than Intel. One environment variable matters more than the chip:
> conda-forge's ParaView starts on VTK's `Sequential` SMP backend, which uses one core.

Workload: **the first week of 2023 (168 hours)** of ERA5 temperature on **all 37 pressure levels** at
the native 0.25° grid, 1440 × 721 × 37 = 38,414,880 points an hour. That is 6.45 billion values in
the seven daily netCDF4 files (8.86 GB) that NSF NCAR publishes on AWS Open Data. Each hour gets
the batch post-processing `pvbatch` is used for: the CF reader, a `Contour` of the freezing level
(T = 273.15 K, about 1.1 M points and 2.2 M triangles), `FeatureEdges`, scalar `Clip`s
below and above freezing, `IntegrateVariables` on all four, and a rendered frame. A week of hourly
3D fields is the unit a weather case study or an animation is cut in. A season is this task
fanned out by week, not a different workload.

## Run it

```sh
export AWS_PROFILE=aws B=<your bucket>
bash measurements/paraview-real/stage-inputs.sh "$B"     # 7 files, server-side copy, sha256-pinned
for a in arm64 x86_64 amd; do
  spawn task run --spec "$(python3 scripts/spec_arch.py paraview $a "$B" w1 measurements/paraview-real/week.task.json)" --wait
done
bash measurements/paraview-real/collect.sh "$B" w1      # results/<chip>/ and the cross-chip comparison
```

The task runs `real.sh`, which starts Xvfb and runs `pvbatch week.py`. Both are pinned by sha256
beside the data. The core of `week.py`, per file:

```python
vtkSMPTools.SetBackend("TBB")                     # the default is "Sequential": one core
r = NetCDFReader(FileName=[f]); r.Dimensions = "(level, latitude, longitude)"; r.SphericalCoordinates = 0
c = Contour(Input=r, ContourBy=["POINTS", "T"], Isosurfaces=[273.15])
lo = Clip(Input=r); lo.ClipType = "Scalar"; lo.Scalars = ["POINTS", "T"]; lo.Value = 273.15; lo.Invert = 1
IntegrateVariables(Input=c)                       # and on lo, hi and r; then Render + SaveScreenshot, per hour
```

## Results

8 vCPU, 32 GiB (`m8*.2xlarge`), the TBB backend on 8 threads, and the same staged bytes on every
chip. ParaView 6.1.1 is aarch.science's `viz` image on Graviton4 and its linux-64 lock twin on both
x86 chips.

| chip | instance | pvbatch wall (168 h) | per data-hour | task window | peak RSS | $/hr | compute $/result | billed $/result |
|---|---|---|---|---|---|---|---|---|
| Graviton4 | `m8g.2xlarge` | 1,581 s | 9.4 s | 1,743 s | 7.93 GiB | 0.3590 | 0.158 | 0.174 |
| Xeon 6975P-C | `m8i.2xlarge` | 2,136 s | 12.7 s | 2,251 s | 7.86 GiB | 0.4234 | 0.251 | 0.265 |
| **EPYC 9R45** | `m8a.2xlarge` | **886 s** | **5.3 s** | **972 s** | 7.92 GiB | 0.4869 | **0.120** | **0.131** |

- **AMD is 1.78× faster than Graviton4, and Graviton4 is 1.35× faster than Intel.** AMD is also the
  cheapest per result, by 24% against Graviton4 on both compute and billed cost, despite the
  dearest hour. Intel is the dearest per result, at 1.6× Graviton4's compute cost.
- **The SMP backend is the bigger dial.** On hour 0, the contour, clips and integrals took this long:

  | chip | `Sequential` (the default) | `TBB`, 8 threads | speed-up |
  |---|---|---|---|
  | Graviton4 | 29.4 s | 7.3 s | 4.0× |
  | Xeon | 22.8 s | 11.5 s | 2.0× |
  | EPYC | 17.3 s | 4.2 s | 4.2× |

  Without `vtkSMPTools.SetBackend("TBB")` at the top of the script, seven of the eight
  cores sit idle. Intel's 8 vCPUs are 4 cores × 2 threads (`lscpu`, `results/*/host.txt`), and that
  shows as half the speed-up. Its single core is faster than Graviton4's, but the box is slower.
- **Clip and integrate are 69–80% of the time** (Graviton4: 465 s and 705 s of 1,581). Reading the
  netCDF, the contour itself and the render are 0.4–0.6 s an hour each.
- **The box is sized by staging, not by compute.** Peak RSS is 7.9 GiB on every chip (`ru_maxrss`).
  But the 8.86 GB of input sits in `/tmp`, a tmpfs of half the RAM, which is 54–58% full here. A
  `c8g.2xlarge` (16 GiB, so an 8 GiB tmpfs) cannot hold it. The probe (`probe.task.json`, one day)
  measured both before the week launched.
- Overhead is small: 86–162 s of each window is boot, image pull or env install, and staging.
  These timings are not compute cost ([layout](../../patterns/layout-and-effective-cost.md)).
- n = 1 per chip. The counts are deterministic, and the float values that move are explained
  below, so a repeat would measure only timing noise.

## What held at scale, on all three chips

| claim | why it must hold | all three chips |
|---|---|---|
| contour points = grid edges whose ends straddle 273.15 K | marching cubes puts exactly one merged point on each crossed edge, and there are 0 exact ties | 168/168 hours, recounted in numpy from the same array |
| boundary edges of the surface = crossed edges on the six domain faces | the surface meets the box boundary in closed loops, one segment per crossing | 168/168 |
| 0 non-manifold edges | a consistent case table gives a 2-manifold | 168/168 |
| `IntegrateVariables` volume of the grid = Σ\|dx\|·Σ\|dy\|·Σ\|dz\| | the voxels tile the box | 0.0 relative |
| ∫T over the grid = numpy's tensor trapezoid on the same coordinates | the voxel rule *is* the trapezoid rule | worst 1.6e-13, bound N·ε = 8.3e-9 |
| a planar clip (z = 512.3 hPa) partitions the volume | exact polyhedral pieces, so below + above = whole up to rounding | worst 5.3e-11, bound 8.3e-9 |
| the TBB backend is the one in use, with 8 threads | the parallel claim, read back from VTK itself | `TBB`, 8 |
| 168 frames, 800 × 450, shaded | a failed GL path renders blank | 168/168 |

The N·ε bound is the worst-case relative error of a double-precision sum over the grid's 37.4 M cells.

**Two things a ParaView user should know, both measured here:**

- **A clip of ERA5 integrates to a negative volume.** ERA5's latitude runs north to south. The
  clip's pieces are integrated as tetrahedra with a signed volume, so both halves come back negative
  (−5.12e7 and −1.34e7 deg²·hPa on hour 0) while the uncut grid, integrated as voxels with `|dx dy dz|`, is
  positive. Take `abs()` before you report a clipped volume or a ratio of one.
- **A scalar clip does not partition the volume exactly.** For the T = 273.15 K clip, |below + above|
  misses the whole by up to 4.0e-7 relative, and ∫T by 4.3e-7. That is 50× the rounding bound. The
  planar clip on the same grid closes to 5e-11, so the gap is the method: a scalar cut's faces are
  not planar inside a cell, and each side triangulates them on its own. This is reported, not
  asserted. The below-freezing fraction (0.791–0.794 of the coordinate volume) is accurate to 4e-7.

The Euler characteristic V − E + F of the surface ranges from −55 to 178 across the week. The
freezing level is not one sheet. It is reported only.

## Across chips: fixed points and paths

**Everything discrete is identical on all three chips:** V, F, boundary and non-manifold edge
counts, the clipped cell counts, the grid volume, T's range, and **all 168 frames, byte for byte**
(S3 ETags, `results/*/frames.txt`). That is 21 of 28 columns of `hours.tsv`, in every hour.

**The float integrals differ, and the chip is not the cause.** Area differs by ≤ 2.4e-14 relative,
∫T by ≤ 1.7e-13, and the clipped volumes by ≤ 2.0e-10. The evidence that this is thread scheduling:

- On hour 0, the **`Sequential` backend gives bit-identical** area and clipped volume on Graviton4,
  Intel and AMD (`vol_lo = −51248981.04289708` on all three).
- Under TBB, **the same hour computed twice in one run** differs by 3e-12 to 7.6e-11 (`meta.json`
  against row 0 of `hours.tsv`). That is the same order as the cross-chip spread.

So ParaView's threaded reductions are a path: TBB hands out the partial sums in a different order
each run. The Sequential result is a fixed point, and it holds across architectures. Compare TBB
integrals with a tolerance, never by equality, even on one machine.

numpy's own trapezoid (`einsum`) differs between arm64 and both x86 chips in 73 of 168 hours, by
≤ 2.4e-16. That is one ulp from SIMD summation order. Intel and AMD agree with each other. OpenBLAS
is not on this path (`openblas_get_corename()`: `neoversev2` on Graviton4, `Cooperlake` on both x86
chips, recorded in `meta.json`).

## Pins (data tier: RODA)

| | |
|---|---|
| data | `s3://nsf-ncar-era5/e5.oper.an.pl/202301/e5.oper.an.pl.128_130_t.ll025sc.202301{01..07}00_*23.nc` (RDA ds633.0, Payer: BucketOwner), sha256 in `pins.sha256` |
| image | arm64 `quay.io/aarchsci/viz@sha256:2539c1e4…`; x86 the `viz-c97c5af10411` lock twin (the recipe's frontmatter) |
| task | `real.sh`, `week.py`, pinned in `pins.sha256` and re-checked inside the task before anything runs |

## Caveats

- Prices are on-demand us-west-2: spawn's `cost_estimate` over the task window, from wrapper start
  to stage-out done. `$/hr` is that estimate over the window.
- `cgroup memory.peak` reads 7.7 GiB on Graviton4 and 11.2 GiB on x86. It counts page cache, and on
  x86 that includes the installed env. `ru_maxrss` is the process peak, and it is the same on all
  three.
- The volume is in coordinate units (deg² · hPa), not m³. The identities do not depend on units.
