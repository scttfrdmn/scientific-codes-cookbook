---
tool: cdo
tool_version: 2.6.1
env: climate
image: quay.io/aarchsci/climate@sha256:ce6735b6dffe825fd23810bce4935b8eff6f6fbf7963c1418b87a7f30767b5fe
spawn_version: 0.104.0
last_verified: 2026-09-13
---
# CDO + NCO — netCDF operators, one toolchain checking the other

CDO builds and transforms a climate field; NCO — an independent toolchain — reads CDO's output back and confirms it. These are the netCDF command-line operators climate work actually runs on, cross-checked on Graviton. For anyone who reaches for `cdo` and `ncks`, not only xarray.

## Run it

```bash
spawn task run --spec "$(make -s spec RECIPE=cdo-nco)" --wait
cdo -f nc -topo,global_2 topo.nc      # ETOPO elevation on a 2° global grid
cdo output -fldmax topo.nc            # 5761 — max elevation on this grid
ncwa -y max -v topo topo.nc max.nc    # NCO's independent max of the same field → 5761
```

CDO produces the field and its statistics; NCO reads the same file and recomputes them. Two independent implementations agreeing on identical bytes is the check — the [openbabel/pdbfixer](../openbabel-pdbfixer/README.md) shape, one domain over.

## Make it yours

| In the recipe | Swap for | What to know |
|---|---|---|
| the ETOPO field + a constant field | your netCDF datasets | both built in-code so the checks are exact; `cdo`/`nco` operate on any CF-netCDF. |
| `remapbil` regridding | your grid + method | `remapcon` (conservative) for fluxes, `remapbil` for smooth fields — a physical choice, not a default. |
| the `fldmax`/`fldmin` cross-check | `fldmean`, `fldsum`, … | **cross-check cdo↔nco with weighting-independent stats:** cdo's `fldmean` is area-weighted and nco's default isn't, so comparing means compares *methods*, not tools ([compare like with like](../../practices/cross-checks.md)). |

**Leave the fixtures:** they give exact answers to check against, which real data can't. **Scale it** to your files — the operators and the cross-tool discipline carry over unchanged.

## Shape, size, cost

One task, `c8g.large` (2 vCPU / 4 GiB), TTL 4m, cap $0.03. The operators run in **seconds**; recorded command window **97 s** — boot and the 0.65 GB `climate` image pull are the whole task. **These timings are not compute cost.**

**Sizing:** no family question — these are serial CLI operators on a small grid. Real work (high-resolution reanalysis, long time series) sizes by file volume and shifts to RAM; size it on the dataset, not this.

<details>
<summary>As shipped: the cross-tool check, exact conservation, why extrema not the mean, pins, smoke check, run + verify</summary>

### The cross-tool check (cdo produces, nco verifies) — and why extrema, not the mean

CDO builds the ETOPO field and reports its extrema (**max 5761 m, min −8979 m** on this grid); NCO reads the same file and computes the same extrema independently — **exact agreement**, and equal to the deterministic ETOPO-on-2° values (so the check is a fixed known value, not merely "the two agree", which could pass vacuously if both shared a decode bug).

Extrema are used deliberately, not the field mean: `cdo fldmean` is **area-weighted** and `nco`'s default average is not, so comparing means would compare two *methods* and fail by design — the [compare like with like](../../practices/cross-checks.md) trap. Max and min are weighting-independent, so they cross-validate two tools cleanly. On the Graviton verifying run every value reproduced bit-identically to local (5761 / −8979 / 288 / 300), and `topo.nc` reads back as valid netCDF in a re-read.

### Exact conservation and arithmetic (constructed field)

A constant field (288) regridded with `remapbil` to a coarser grid keeps `fldmean` = **288 exactly** — conservation under regrid. `cdo addc,12` then gives `fldmean` = **300 exactly** — exact arithmetic on a known input, asserted as the value rather than a round-trip (a float32 add/subtract round-trip on real elevations drifts ~2e-4; the known-value assertion can't). Both machine-exact, the constructed-field shape the [climate](../climate/README.md) recipe's constant-field regrid also uses.

### Pins (data tier: synthetic / in-code)

| | |
|---|---|
| image | `quay.io/aarchsci/climate@sha256:ce6735b6…` (`climate` env — CDO 2.6.1 + NCO 5.3.9 + xarray/xESMF, cosign-signed, `linux/arm64`) |
| input | none — the ETOPO field (CDO's bundled `topo`) and the constant field are built in-code |

### Smoke check (inside the task; measured before launch)

| observable | assertion | observed |
|---|---|---|
| cross_tool_max | cdo == nco == 5761 | 5761 / 5761 |
| cross_tool_min | cdo == nco == −8979 | −8979 / −8979 |
| conservation_regrid | const `fldmean` == 288 after regrid | 288 |
| exact_arithmetic | const + 12 → `fldmean` == 300 | 300 |

### Run + verify

```sh
make run RECIPE=cdo-nco
make ls  RECIPE=cdo-nco
```

The smoke check runs inside the task; the bucket listing is the second half ([exit 0 isn't proof](../../practices/container-path.md)). Expect `smoke-check.txt` and `topo.nc` (CDO's ETOPO field). Re-run: `make run` launches a fresh task and overwrites this prefix — no spec edit needed.

</details>
