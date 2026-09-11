---
tool: xesmf
env: climate
image: quay.io/aarchsci/climate@sha256:5b3d840e79eabaa222b8766f08e998dea87313696da6cafd8a9740cde69b9ac7
spawn_version: 0.104.0
---
# xESMF (climate env) — conservative regridding between global grids

`xESMF` conservatively regrids a field from one global grid to another — the interpolation every climate workflow runs before models and observations can be compared.

> **What this covers.** One conservative regrid on global periodic grids, one real-netCDF reader path, one MetPy calc — proof the climate stack (xarray / netCDF4 / xESMF / esmpy / MetPy) is correct on Graviton4. Not a benchmark; no large dataset, GRIB decode, or full reanalysis.

## Run it

```python
import xesmf as xe
src = xe.util.grid_global(5, 4)          # 5°×4° global grid
dst = xe.util.grid_global(8, 6)          # → 8°×6°
regridder = xe.Regridder(src, dst, "conservative")
out = regridder(src["lat"] * 0 + 1.0)    # a constant 1.0 field must come back 1.0 everywhere
```

One task: the regrid, an `xarray.open_dataset` on a pinned NCEP file, and a MetPy wind-speed calc, in one `python3` invocation. Only the netCDF is staged.

## Make it yours

| In the recipe | Swap for | What to know |
|---|---|---|
| the constant-field regrid on 5°×4° → 8°×6° global grids | your grids + real field | the constant field is the point — a conservative scheme preserves it exactly; a real field regrids the same but gives no closed-form target to assert. |
| the pinned NCEP `air_temperature.nc` | your own netCDF | opened with the `netcdf4` engine to exercise the *reader* on a real file (the env's own D3 only round-trips a self-made one). |
| `"conservative"` | `"bilinear"`, `"nearest_s2d"`, … | scaffolding that **must match your science** — only conservative preserves the area-weighted integral, and the conservation check is specific to it. |

Deterministic — **nothing is determinism scaffolding**. **Leave the fixture:** the conservation law is exact at any grid size and the real netCDF exercises the reader; a bigger dataset is a longer run, not a more legible one. Leave-it.

## Shape, size, cost

One task, `c8g.large` (2 vCPU / 4 GiB), TTL 5m, cap $0.02. Reader + regrid + calc take ~2 s. Recorded command window **74s** — boot, Docker install, the ~0.57 GB `climate` image pull, and staging the ~7 MB netCDF are the whole task ([why](../../practices/what-this-does-not-cover.md)). **These timings are not compute cost.**

<details>
<summary>As shipped: three kinds of check, pins, smoke check, run + verify</summary>

### Three checks, three kinds

- **Conservative-regrid conservation (the headline).** Regrid a field of 1.0 from a 5°×4° global grid to 8°×6°; every target cell must still be 1.0 (area-weighted integral preserved, no flux lost). Exact-or-wrong, exercising the real ESMF machinery — the same class as [ambertools](../ambertools/README.md)'s NVE energy conservation.
- **netCDF reader on a real file.** Open the pinned NCEP file and assert its known structure (variable `air`, shape (2920, 25, 53)) and a deterministic global mean (281.255037 K) — a value that's wrong if the reader mis-decodes.
- **MetPy identity.** Wind speed of (3, 4) m/s is exactly 5.

### Pins (data tier: stable public source with a durable id)

| | |
|---|---|
| image | `quay.io/aarchsci/climate@sha256:5b3d840e79eabaa222b8766f08e998dea87313696da6cafd8a9740cde69b9ac7` (tag `2026.09.02`, xarray/dask/netCDF4/xESMF/esmpy/cfgrib/cartopy/metpy, cosign-signed, `linux/arm64`) |
| netCDF | `air_temperature.nc` from `pydata/xarray-data` commit `2baf0c22` — `sha256:c606b89c…` (7,751,328 B, NCEP `air`, (2920,25,53)) |

`stage-inputs.sh` fetches/verifies/uploads it once (~7 MB); the regrid and MetPy checks are in-code.

### Smoke check (inside the task; measured before launch)

| observable | assertion | observed |
|---|---|---|
| pinned sha256 | matches | OK |
| netCDF variable / shape | `air` / (2920, 25, 53) | air / (2920,25,53) |
| **netCDF mean** | 281.255037 K (deterministic reader statistic) | 281.255037 |
| **regrid conservation** | constant field → [1.0, 1.0] on target grid | [1.0, 1.0] |
| MetPy wind speed | 5.0 (3-4-5) | 5.0 |

No thresholds: the mean and wind speed are reference values, the regrid is exact conservation, the shape is exact.

### Run + verify

```sh
make stage RECIPE=climate       # once; fetch + verify + upload air_temperature.nc (~7 MB)
make run RECIPE=climate
make ls RECIPE=climate
```

a completed run does **not** prove the outputs exist — an exit code says the command ran, never that its output is real; the smoke check runs *inside* the task, and the bucket listing is the second half of it. Expect one object (`smoke-check.txt`). Re-run: `make run` launches a fresh task each time and overwrites this prefix — no spec edit needed.

</details>
