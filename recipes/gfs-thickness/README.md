---
tool: metpy
tool_version: "1.7.1"
env: climate
image: quay.io/aarchsci/climate@sha256:53ce76bc2a96d4dded744df2f3eccc040e4fa53ece7faa225eaa36eee64e8428
spawn_version: 0.123.0
last_verified: 2026-10-08
---
# MetPy + eccodes — GFS in GRIB2, checked against its own geopotential

Reads an operational GFS analysis in GRIB2 on Graviton4 and verifies the file's geopotential heights against its own temperature and humidity through the hypsometric equation. For anyone working with operational weather data on ARM.

## Run it

```bash
make stage RECIPE=gfs-thickness     # once: 40.3 MiB GFS analysis + NOAA's inventory
spawn task run --spec "$(make -s spec RECIPE=gfs-thickness)" --wait
make ls RECIPE=gfs-thickness

import xarray as xr, metpy.calc as mpcalc
ds = xr.open_dataset("gfs.pgrb2.1p00.f000", engine="cfgrib",
        backend_kwargs={"indexpath": "",
                        "filter_by_keys": {"typeOfLevel": "isobaricInhPa", "shortName": "t"}})
mpcalc.thickness_hydrostatic(p, T, mixing_ratio=w)   # 5725.61 m vs the file's 5711.69 m
```

## Make it yours

| In the recipe | Swap for | What to know |
|---|---|---|
| `gfs.20251001/00` | any GFS cycle | `s3://noaa-gfs-bdp-pds` is open and free; keys are `gfs.YYYYMMDD/HH/atmos/`. **Don't reach back past ~2023** — older cycles have no `pgrb2` output. |
| `pgrb2.1p00` | `pgrb2.0p25` | 1° is 40 MiB, 0.25° is ~500 MiB for the same fields. Start coarse. |
| `indexpath=""` | **keep it** | cfgrib otherwise writes an index cache **next to the input**, which fails with EPERM on a staged file the container doesn't own. |
| one `shortName` per open | all isobaric variables | **this is the part worth copying** — a GFS file holds variables on *different* vertical coordinates, and a combined open silently drops the mismatched ones. See below. |
| the `.idx` sidecar | — | NOAA publishes a wgrib2 inventory beside every file: byte offset, variable and level per message. It's both a manifest and a way to range-get single fields. |

**Leave the fixture.** One 1° analysis is 40 MiB and carries 696 messages — enough that the thickness check spans 13 pressure levels, small enough to be free. **Scale it** to 0.25° or a forecast series once it passes; the check is resolution-independent.

## Shape, size, cost

One task on `m8g.large` (2 vCPU / 8 GiB), TTL 25m, cap $0.08. Decode plus the calculations take seconds; the recorded window is boot and image pull, so **these timings are not compute cost** ([layout](../../patterns/layout-and-effective-cost.md)).

<details>
<summary>As shipped: the file checked against itself, a discretisation-justified band, and the cfgrib trap that passes silently</summary>

### The checks

| observable | assertion | observed |
|---|---|---|
| staged bytes | match pins (and the MD5s S3 serves) | both match |
| inventory | 696 messages; HGT/TMP/RH present at 1000 and 500 mb | 49 HGT, 54 TMP, 51 RH |
| **level identity** | **T, RH and HGT on an identical level vector** | **33 levels, identical** |
| levels in 1000–500 mb | ≥ 8, or the integral is too coarse | **13** |
| **thickness vs the file's own geopotential** | **within 2% (discretisation)** | **max 0.24%** |
| **θ involution** | **θ(p,T) → T(p,θ) returns T** | **0.000e+00** |

### The file verifying itself

The hypsometric equation ties geopotential thickness to the temperature and humidity field:

```text
Z(500) - Z(1000) = (Rd/g) * integral of Tv dln(p)
```

GFS carries **both sides** — its own geopotential heights *and* its own T/RH — so the file can be
checked for internal consistency. That is an independent field verifying the computation, not a
band on an observed value:

```text
lat   40.0  lon 260.0   model 5711.69 m   hypsometric 5725.61 m   rel 0.0024
lat    0.0  lon 180.0         5762.72             5763.17              0.0001
lat  -45.0  lon  30.0         5525.20             5526.00              0.0001
lat   60.0  lon 350.0         5573.02             5573.16              0.0000
lat   20.0  lon 100.0         5777.15             5782.11              0.0009
```

**The 2% band is set by the discretisation, not by what the numbers did.** GFS integrates its own
hydrostatic equation on ~127 native model levels; this reproduces it from the 13 archived isobaric
levels with a trapezoidal rule. A few tenths of a percent is the expected cost of that coarsening,
which is what four of the five points show (≤0.09%). The 0.24% outlier is the mid-latitude point,
where the strongest vertical structure sits between archived levels — the error behaves the way the
explanation predicts, which is the useful part.

### Decode statistics against the publisher's manifest, not against eccodes

NOAA ships a wgrib2 `.idx` beside every GRIB2 file: one line per message with its byte offset,
variable and level. Checking what eccodes decodes against *that* is a check against an external
statement of the contents, rather than against the same library that performed the decode. 696
messages, with 49 HGT / 54 TMP / 51 RH across all level types.

### The cfgrib trap: a combined open drops variables silently

A GFS file carries variables on **different vertical coordinates** — 33 isobaric levels for most
fields, 22 for others. Asking cfgrib for all isobaric variables at once makes it raise
`DatasetBuildError` internally, **silently drop** the incompatible ones, and continue with a merged
coordinate.

That is not merely noisy. The surviving merged `isobaricInhPa` coordinate then gets used to index
arrays that may not share it — so a level mask can select **different pressures for temperature than
for humidity** and produce a plausible, wrong thickness with no error raised. The first version of
this recipe worked only because `t`, `r` and `gh` all happened to land on the 33-level set.

So each field is opened with its own `shortName` filter and **the three level vectors are asserted
identical** before any mask is applied. If a future GFS product archives RH on fewer levels, the
recipe stops with a clear message instead of quietly comparing different altitudes.

Also `indexpath=""`: cfgrib writes an index cache next to its input by default, and a staged input
is owned by the instance user while the container runs as the image's user — so that write fails
with EPERM on a file the task never created.

### Pins

| | |
|---|---|
| analysis | `s3://noaa-gfs-bdp-pds/gfs.20251001/00/atmos/gfs.t00z.pgrb2.1p00.f000`, 42,302,512 B, md5 `7e2c7fb9…` |
| inventory | the same key `+ .idx`, 31,097 B, md5 `11355098…` |
| image | `quay.io/aarchsci/climate@sha256:53ce76bc…` — metpy 1.7.1, cfgrib 0.9.15.1, eccodes 2.48.0 |

Both objects are **single-part uploads, so their S3 ETag is the MD5** — checkable at source rather
than only against ourselves. Staging verifies size and MD5 against what S3 serves, then records a
sha256 for the in-task gate.

**ERA5 was the obvious alternative and does not work here:** `nsf-ncar-era5` on the Open Data
Registry is NetCDF throughout and 1.37 GB per file — wrong format for an eccodes recipe and too
large for a cheap proof run.

### Run + verify

```sh
make stage RECIPE=gfs-thickness
spawn task run --spec "$(make -s spec RECIPE=gfs-thickness)" --wait
aws s3 cp "s3://$(make -s print-bucket)/runs/gfs-thickness/r1/score.tsv" -
```

Fails on a pin mismatch, mismatched level vectors, fewer than 8 levels in the integration range,
thickness disagreement beyond 2%, or a broken involution — but check the bucket regardless
([exit 0 isn't proof](../../practices/container-path.md)).

### Not covered

Forecast hours beyond the analysis, 0.25° resolution, GRIB2 *writing*, range-getting single
messages via the `.idx` offsets (which is how you avoid downloading 40 MiB for one field), MetPy's
soundings and skew-T plots, and the CAPE/CIN family of derived quantities.

</details>
