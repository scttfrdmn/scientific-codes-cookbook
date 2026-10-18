# CDO + NCO on a real workload — a year of hourly ERA5, three chips

> **CDO and NCO agree on every one of 392 million mean values from a full year of hourly ERA5, and
> CDO's annual-mean field is bit-identical to xarray's on the same bytes. Two default spellings are
> about 10× and 22× slower on these files than the fix.** AMD finishes first and is cheapest billed;
> Graviton4 is cheapest on compute alone.

Workload: **all 8,760 hours of 2023** of ERA5 2 m temperature on the native 721 × 1,440 grid, the
12 monthly netCDF4 files (13.35 GB) that NSF NCAR publishes on AWS Open Data. These are the same
bytes [climate-real](../climate-real/README.md) reduced with xarray. The run does what the
operators are used for: area-weighted global means, daily, monthly, annual and hour-of-day means,
monthly anomalies, and a conservative regrid of every hour to 1°. CDO computes each product from
the year and NCO computes the same means independently. A year of one hourly variable is the unit
ERA5 work is cut in. A 30-year climatology is 30 of these runs fanned out, not a different workload.

## Run it

```sh
export AWS_PROFILE=aws B=<your bucket>
bash measurements/climate-real/stage-inputs.sh "$B"     # the year, sha256-pinned (once)
bash measurements/cdo-nco-real/stage-inputs.sh "$B"     # xarray's results + these scripts
for a in arm64 x86_64 amd; do
  spawn task run --spec "$(python3 scripts/spec_arch.py cdo-nco $a "$B" "$(date +%s)" measurements/cdo-nco-real/cdo-nco-real.task.json)" --wait
done
bash measurements/cdo-nco-real/collect.sh "$B" <scratch dir>   # results/, results/compare.txt
```

`real.sh` runs the operators in waves of parallel jobs, and `check.py` asserts everything below.
Each job is the plain invocation:

```sh
cdo -b F64 fldmean -setgridarea,area.nc -mergetime e5.*.nc g.nc        # hourly global mean
cdo -b F64 fldmean -setgridarea,area1.nc -remap,r360x180,w.nc -mergetime e5.*.nc gr.nc
cdo -b F64 daymean -mergetime e5.*.nc day.nc                           # also timmean, monmean, dhourmean
ncra --dbl --cnk_csh=1000000000 --mro -d time,,,24,24 e5.*.nc day_nco.nc   # also the year, each month
cdo -b F64 monmean -ymonsub day.nc mon.nc anom.nc                      # anomalies vs climatology
```

## Results

8 vCPU, one `r8.2xlarge` (64 GiB) per chip, same versions (CDO 2.6.1, NCO 5.3.9), same input bytes:

| chip | instance | cores × threads | year wave (9 jobs) | workload wall | $/hr | compute $/result | billed | billed $/result | peak RSS |
|---|---|---|---|---|---|---|---|---|---|
| Graviton4 | `r8g.2xlarge` | 8 × 1 | 286 s | 290 s | 0.4713 | **0.0380** | 827 s | 0.1083 | 2.07 GiB |
| Xeon 6975P-C | `r8i.2xlarge` | 4 × 2 | 347 s | 352 s | 0.5557 | 0.0543 | 828 s | 0.1278 | 2.10 GiB |
| EPYC 9R45 | `r8a.2xlarge` | 8 × 1 | **228 s** | **232 s** | 0.6390 | 0.0412 | **582 s** | **0.1033** | 2.10 GiB |

A *result* is every product of the year above. **Workload wall** covers the cell areas, the remap
weights, the nine full reads of the year and the products of products. **Billed** runs from launch
to terminate. It also pays for check.py (13–23 s) and a third wave that times the two slow default
spellings on January (199–285 s, below). Without that wave, billed would be 550 / 543 / 383 s:
$0.072 / $0.084 / $0.068.

- **AMD is 1.25× Graviton4 and Intel is 0.82×** on the same nine jobs. Each job is one
  single-threaded process that decompresses the whole year (deflate level 1 plus shuffle) and
  sums it, so the run is mostly per-core decompression. Intel's 8 vCPUs are 4 cores with SMT, and its
  run is the slowest.
- **Graviton4 is the cheapest per result on compute**: 8% under AMD and 30% under Intel. AMD's
  shorter run does not make up its 36% higher hourly price.
- **Graviton4's bill carries more overhead.** It spent 54 s pulling the 0.65 GB `climate` image,
  where the x86 twin's base image took 2 s plus a 5–6 s offline install. It also spent 38 s on the
  sha256 of the year, against 7–9 s on x86. Fixed cost is 244 s against 138–168 s.
- **Memory picks the family.** Staging lives in tmpfs (½ RAM): 13.35 GB in and 4 GB of products
  reached 17 GiB of `/tmp`. Each operator process stays under 2.1 GiB, and NCO's 1 GB chunk cache
  is most of that.
- n = 1 per chip. Every product is deterministic: the time means are bit-identical on all three
  chips (below), so a repeat would only re-measure timing.

## Two defaults that are slow on these files

| spelling | January, Graviton4 / Intel / AMD | the fix | fixed |
|---|---|---|---|
| `cdo fldmean` (no cell areas given) | 148.0 / 135.7 / 150.7 s | `-setgridarea,area.nc` from one `cdo gridarea` | 14.2 / 15.4 / 10.6 s |
| `ncra` (default chunk cache) | 277.4 / 284.9 / 199.2 s | `--cnk_csh=1000000000` | ~12 s / file |

- **CDO 2.6.1 computes the grid's cell areas again for every timestep** when `fldmean` has to
  derive them. `cdo gridarea` takes 0.2 s on this grid, and the default fldmean costs 0.19 s per
  hour whether the file has 48 hours or 744. Computing the areas once and attaching them gives the same mean
  within 5.4e-14. Over the year the default spelling costs about 29 minutes per job.
- **These files store 27 × 139 × 277 chunks.** NCO reads one hour at a time, which touches 36
  chunks (150 MB decompressed). With the default chunk cache, each hour decompresses its 27-hour
  slab again. A 1 GB cache makes `ncra` as fast as CDO, and the result is bit-identical. The same files took xarray 132 s or 239 s per pass depending on dask
  chunking ([climate-real](../climate-real/README.md)); this is that trap in another tool.

Both costs scale linearly with the number of timesteps, so the January timings carry over to the year.

## What held at scale, on all three chips

| claim | why it must hold | Graviton4 | Xeon | EPYC |
|---|---|---|---|---|
| 12 files match their sha256; 8,760 hours, 365 days, 12 months, 24 hours of day | provenance | ok | ok | ok |
| CDO's cell areas sum to 4πR² | telescoping sum | 0.0e+00 | 0.0e+00 | 0.0e+00 |
| CDO == NCO, annual / 12 monthly / 365 daily fields, every cell: within ½ float32 ulp | NCO writes the same double mean rounded to float32 | float32-identical in 392,454,720 of 392,454,720 cells | same | same |
| daily, day-weighted monthly and hour-of-day means → annual field, every cell | resample conserves (bound 2·8760·u) | ≤ 2.4e-15 | same | same |
| `fldmean` of the daily field == daily mean of the hourly `fldmean` | operators commute (bound 2·1,038,240·u = 2.3e-10) | 5.6e-13 | 5.6e-13 | 5.6e-13 |
| monthly anomalies average to zero, every cell | definition of an anomaly | 4.2e-14 K | same | same |
| control: climatology one month out of phase | the zero check has teeth | misses by 24.9 K | same | same |
| `remapcon` to 1° keeps the global mean, all 8,760 hours | conservative remap (bound 2(E+E1) = 1.1e-4) | 1.11e-6 | same | same |
| **CDO vs xarray**, hourly global mean, all 8,760 hours | same bytes, independent weights: ≤ L1(weights) × ½ range = 8.1e-5 K | 2.1e-5 K | same | same |
| **CDO vs xarray**, annual-mean field, every cell | same mean; xarray saved float32 | float32-identical, 1,038,240 / 1,038,240 | same | same |
| NCO `ncwa -w cell_area` == numpy with the same weights | weighted mean | float32-identical 13 / 13 | same | same |
| 2023 global mean, C3S [Global Climate Highlights 2023](https://climate.copernicus.eu/global-climate-highlights-2023): 14.98 °C | published | 14.9829 (NCO: 14.9829) | same | same |
| July 2023, C3S [July bulletin](https://climate.copernicus.eu/july-2023-sees-multiple-global-temperature-records-broken): 16.95 °C | published | 16.953 | same | same |
| record daily global mean, C3S/WMO: 17.08 °C on 6 July | published | 17.0797, day 187 | same | same |

The bounds in the table are worst-case naive float64 summation (n·u for n terms, u = 2⁻⁵³), the
float32 rounding that NCO applies to its output, and two measured properties of CDO's areas. E and
E1 are the largest relative deviations from the exact R²Δλ(sin φn − sin φs): 3.2e-6 on the native
grid and 5.1e-5 on the 1° grid. L1 is the distance between CDO's normalised
weights and the exact ones (1.2e-6). The published values carry ±0.01 °C, since they are quoted to
two decimals from ERA5T. Each lands about 0.003 °C inside.

**CDO's float64 annual-mean field has the same sha256 as xarray's** (`1c1073c575740b93`, from
climate-real's run). Two codebases summed 8,760 hours per cell, one a C++ operator chain and the
other dask over numpy, and they produced the same 8.3 MB of doubles. The hourly global means
differ by 2e-5 K because the area weights differ. CDO computes cell areas as spherical polygons,
xarray uses the exact band formula, and the difference sits within the bound the weights imply.

**Bilinear regridding of the annual field also keeps the global mean, to −0.00017 K.** On a field
this smooth the global mean cannot tell `remapbil` from `remapcon`. The conservation check catches
a broken remap; it cannot choose the method for you.

## Across chips: the time means are identical, the area weights are not

Every time mean is **bit-identical on all three chips**: the annual, monthly, daily and
hour-of-day fields, NCO's three products and the anomalies. They are sums of float32 inputs in
float64, done in the same order. Intel and AMD agree bit for bit everywhere, since they run the same
x86 binaries.

The **cell areas differ in their last bits** between the arm64 and x86 builds, so every
area-weighted value moves. The hourly global means differ by up to **2.2e-15 relative** (about ten
ulp) and the remapped ones by 1.2e-15. These are still fixed points. The difference comes from how
the two builds compute the areas, not from the order of the sums. Neither tool links a
BLAS, so the OpenBLAS kernel question does not arise. Every global mean shown to four decimals is
the same on all three chips (`results/compare.txt`).

## Pins

| | |
|---|---|
| data | the 12 ERA5 `e5.oper.an.sfc.128_167_2t.ll025sc.2023MM…nc` objects from `s3://nsf-ncar-era5` (RODA tier), staged and pinned by [climate-real](../climate-real/README.md): its `era5-2023.sha256`, re-checked in the task |
| reference | xarray's `global_hourly_2023.npy` and `annual_mean_2023.npy` from climate-real's arm64 run. Its x86 run wrote byte-identical files (same ETag, checked by `stage-inputs.sh`). sha256 in `pins.sha256` |
| scripts | `real.sh`, `check.py`, `rt.py`, sha256 in `pins.sha256`, checked in-task |
| image | the recipe's `climate@sha256:ce6735b6…` (arm64) and its `climate-8bc38c951a40` x86 twin |

Raw per-chip outputs are in `results/`. The staged year is climate-real's, so delete
`inputs/climate-real/era5-2023/` only when you are done with both measurements.
