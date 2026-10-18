---
tool: xesmf
env: climate
image: quay.io/aarchsci/climate@sha256:5b3d840e79eabaa222b8766f08e998dea87313696da6cafd8a9740cde69b9ac7
env_x86_64: climate-60e8dca4df0d
spawn_version: 0.115.0
last_verified: 2026-10-03
last_verified_x86_64: 2026-10-17
---
# xESMF (climate env) — conservative regridding between global grids

`xESMF` conservatively regrids a field from one global grid to another — the interpolation every climate workflow runs before models and observations can be compared.

## Run it

```bash
make stage RECIPE=climate
spawn task run --spec "$(make -s spec RECIPE=climate)" --wait
```

```python
import xesmf as xe
src = xe.util.grid_global(5, 4)          # 5°×4° global grid
dst = xe.util.grid_global(8, 6)          # → 8°×6°
regridder = xe.Regridder(src, dst, "conservative", periodic=True)   # periodic: global grids wrap
out = regridder(src["lat"] * 0 + 1.0)    # a constant 1.0 field must come back 1.0 everywhere
```

## Make it yours

| In the recipe | Swap for | What to know |
|---|---|---|
| the constant-field regrid on 5°×4° → 8°×6° global grids | your grids + real field | the constant field is the point — a conservative scheme preserves it exactly; a real field regrids the same but gives no closed-form target to assert. |
| the pinned NCEP `air_temperature.nc` | your own netCDF | opened with the `netcdf4` engine to exercise the *reader* on a real file (the env's own D3 only round-trips a self-made one). |
| `"conservative"` | `"bilinear"`, `"nearest_s2d"`, … | scaffolding that **must match your science** — only conservative preserves the area-weighted integral, and the conservation check is specific to it. |

Deterministic — **nothing is determinism scaffolding**. **Leave the fixture:** the conservation law is exact at any grid size and the real netCDF exercises the reader; a bigger dataset is a longer run, not a more legible one — the year of hourly ERA5 [measured below](../../measurements/climate-real/README.md) is that run, with its own identities. Leave-it.

## Shape, size, cost

One task, `c8g.large` (2 vCPU / 4 GiB), TTL 5m, cap $0.02 — retightened from the first real run (10m → 5m, $0.03 → $0.02). Measured phase split: stage-in of the 7 MB netCDF **1 s**, Docker install **46 s**, image pull **75 s**, reader + regrid + calc **5 s**. Provisioning is 96% of the 127 s window ([why](../../practices/what-this-does-not-cover.md)), so **these timings are not compute cost** ([layout](../../patterns/layout-and-effective-cost.md)).

## Which box — [measured](../../measurements/climate-real/README.md) (all 8,760 hours of 2023 ERA5 2 m temperature, 0.25°, 8 threads)

| chip | instance | era5.py wall | peak RSS | **billed $/result** |
|---|---|---|---|---|
| Graviton4 | `r8g.2xlarge` | 884 s | 30.9 GiB | **0.1398** |
| x86 | `r8i.2xlarge` | 1001 s | 32.7 GiB | 0.1743 |

**Graviton4: 1.13× faster, 20% cheaper billed**, and every non-timing output byte-identical across chips; the 2023 global mean (14.9829 °C), July (16.953 °C) and the 6 July daily record (17.0797 °C) each land within 0.003 °C of Copernicus's published values. **Memory picks the family:** staging is tmpfs (½ RAM), so the year's 13.35 GB needs `r8g` before any compute ([data movement](../../patterns/data-movement.md)).

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

### On x86_64

The same task on `c8i.large`, through the `climate` env's linux-64 twin
(`envs-x86_64/climate-60e8dca4df0d.linux-64.lock`: 245 of 249 versions identical; `deprecated`
3.0.0 vs 1.3.1, `unicodedata2` 18.0.0 vs 17.0.1 and `wrapt` 2.5.1 vs 2.4.0 differ, none on the
regrid or reader path). Same staged `air.nc`; `smoke-check.txt` is **byte-identical** to arm64,
including the six-decimal mean 281.255037 and the [1.0, 1.0] conservation.

### Run + verify

```sh
make stage RECIPE=climate       # once; fetch + verify + upload air_temperature.nc (~7 MB)
make run RECIPE=climate
make ls RECIPE=climate
```

The smoke check runs inside the task; the bucket listing is the second half ([exit 0 isn't proof](../../practices/container-path.md)). Expect one object (`smoke-check.txt`). Re-run: `make run` launches a fresh task each time and overwrites this prefix — no spec edit needed.

</details>
