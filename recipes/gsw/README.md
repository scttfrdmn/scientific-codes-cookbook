---
tool: gsw
tool_version: "3.6.23"
env: climate
image: quay.io/aarchsci/climate@sha256:53ce76bc2a96d4dded744df2f3eccc040e4fa53ece7faa225eaa36eee64e8428
spawn_version: 0.123.0
last_verified: 2026-10-08
---
# GSW (TEOS-10) — seawater thermodynamics, against the official check values

Reproduces the TEOS-10 check-value table on Graviton4 — density, sound speed, enthalpy and more, each against the published expected value at its published tolerance. For anyone doing physical oceanography on ARM.

## Run it

```bash
spawn task run --spec "$(make -s spec RECIPE=gsw)" --wait   # nothing to stage
make ls RECIPE=gsw

python3 -c "import gsw; print(gsw.rho(34.4682364305, 27.9964364121, 0.0))"
# 1021.8863044505 — the TEOS-10 published value for that check cast
```

**Nothing is staged.** The official check-value table ships inside the `gsw` package.

## Make it yours

| In the recipe | Swap for | What to know |
|---|---|---|
| the 45×3 check cast | your CTD profile | `gsw` is vectorised over numpy arrays — pass `SA`, `CT`, `p` of any shape. |
| `SA`, `CT` (absolute salinity, conservative temperature) | `SP`, `t` from an instrument | **raw CTD gives practical salinity and in-situ temperature.** Convert with `gsw.SA_from_SP` and `gsw.CT_from_t` first, or every downstream number is wrong. |
| the 9 checked functions | whatever you call | **this is the part worth copying** — any `gsw` function with an entry in the check-value file can be verified against the published table at its published tolerance, for free. |
| `rho`, `alpha`, `beta` | `sigma0`, `spiciness0`, … | the same pattern holds; look for `<name>` and `<name>_ca` in the shipped `.npz`. |

**Leave the fixture.** The 45×3 check cast *is* the reference — it is what TEOS-10 publishes expected values for, so substituting your own data removes the only external truth here. **Scale it** by pointing the same calls at your profiles once the check has passed; correctness is established by the cast, not by volume.

## Shape, size, cost

One task on `c8g.large` (2 vCPU / 4 GiB), TTL 20m, cap $0.05. The arithmetic is milliseconds on a 45×3 cast; the recorded window is dominated by boot and the `climate` image pull, so **these timings are not compute cost** ([layout](../../patterns/layout-and-effective-cost.md)).

<details>
<summary>As shipped: 822 published arrays, 9 functions at their own tolerances, two involutions, and a pinning mistake worth reading</summary>

### The checks

| observable | assertion | observed |
|---|---|---|
| check-value file present | it *is* the reference — assert, don't assume | `gsw/tests/gsw_cv_v3_0.npz`, 641,918 B |
| arrays in the table | — | **822** |
| **`rho`** | **≤ published tol 2.946763e-10** | **2.273737e-13** |
| **`specvol`** | ≤ 2.821094e-16 | 2.168404e-19 |
| **`alpha`** | ≤ 8.251075e-15 | 4.336809e-19 |
| **`beta`** | ≤ 1.839674e-15 | 4.662069e-18 |
| **`sound_speed`** | ≤ 2.591833e-09 | 6.821210e-13 |
| **`internal_energy`** | ≤ 2.499342e-06 | 1.455192e-11 |
| **`enthalpy`** | ≤ 2.499357e-06 | 2.182787e-11 |
| **`dynamic_enthalpy`** | ≤ 2.288826e-07 | 2.182787e-11 |
| **`pt_from_CT`** | ≤ 6.054037e-10 | 1.065814e-14 |
| functions failed | **0** | 0 of 9 |
| **involution** SA→SP→SA | exact to float | **7.105e-15** |
| **involution** CT→pt→CT | exact to float | **3.553e-15** |

**Every tolerance here is the depositors', not ours.** The `.npz` carries `<name>` (expected) and
`<name>_ca` (acceptable deviation) side by side, so the recipe asserts a published number at a
published precision — no band chosen to pass. Observed deviations run **3 to 5 orders of magnitude
inside** those tolerances, which is the signature of a reference reproduced rather than a threshold
tuned.

`sample_rho` is printed so one value is checkable by eye:
`rho(SA=34.4682364305, CT=27.9964364121, p=0.0) = 1021.8863044505`, matching the table exactly.

**The involutions need no table at all.** Practical↔absolute salinity and conservative↔potential
temperature are round trips, so they must return the input to float precision whatever the
reference says — an implementation that normalised wrongly could not satisfy them. They are the
check that survives if the shipped table ever disappears.

**Three functions are skipped, by design not accident:** `entropy`, `cp_t_exact` and `CT_from_pt`
have no `<name>`/`<name>_ca` pair usable with this cast's `(SA, CT, p)` signature. The task requires
the expected array, the tolerance **and** the attribute on `gsw` to all exist before it will check a
function, and counts a skip otherwise — so a missing function is a reported number, never a crash
on a guessed attribute path.

### Not driven by pytest

The obvious implementation is `pytest --pyargs gsw`. **The `climate` env has no pytest** — checked
in the env lock before writing the task, not discovered on the box. Loading the `.npz` directly is
better anyway: the recipe asserts named values a reader can verify, instead of reporting that a
suite passed.

### Pins

| | |
|---|---|
| image | `quay.io/aarchsci/climate@sha256:53ce76bc…` (gsw 3.6.23, numpy 2.5.3, python 3.14) |
| reference | the image's own `gsw/tests/gsw_cv_v3_0.npz` — no external input |

cosign-verified against `playgroundlogic/aarchsci` (signed by its `publish.yml` on `refs/heads/main`).
The signature covers the **manifest-list** digest, so verifying the per-architecture digest directly
returns `no signatures found` — verify the tag, pin the arm64 digest.

### Pinning: verify the tag, but check the image is not stale

cosign proves an image is **authentic**, never that it is **the one you meant**. Resolve the
newest tag by *parsing* `last_modified` — RFC-822 dates begin with a day name, so sorting them as
strings puts `"Wed, 30 Sep"` after `"Thu, 08 Oct"` — and confirm the env lock's `Built:` timestamp
is from the same build as the tag you pin. A lock file in git and an image in a registry are two
different artifacts.

This recipe therefore asserts the check-value file exists before using it: the only honest check is
on the capability actually present in the image.

### Run + verify

```sh
spawn task run --spec "$(make -s spec RECIPE=gsw)" --wait
aws s3 cp "s3://$(make -s print-bucket)/runs/gsw/r1/score.tsv" -
```

The checks run inside the task and fail it on a missing reference file, any function outside its
published tolerance, fewer than 8 functions checked, or a broken involution — but check the bucket
regardless ([exit 0 isn't proof](../../practices/container-path.md)).

### Not covered

The full 822-array table (9 functions of it), the ice and sea-ice branches of TEOS-10, geostrophic
streamfunctions, and anything requiring a real CTD cast. `gsw` is also the foundation for ocean
model diagnostics, which is a different recipe.

</details>
