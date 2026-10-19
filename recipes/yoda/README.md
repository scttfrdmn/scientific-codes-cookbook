---
tool: yoda
tool_version: "2.1.4"
env: hep
image: quay.io/aarchsci/hep@sha256:6754001a09ae036ba3dd862af0b8cef9d377fc3fba2b7da1cad94740214ee0f7
env_x86_64: hep-327e2aedf1d0
spawn_version: 0.127.0
last_verified: 2026-10-11
last_verified_x86_64: 2026-10-17
last_verified_amd: 2026-10-18
---
# YODA — histogram accounting, cross-checked against numpy

Fills, writes, rebins and rescales a histogram on Graviton4, checking every transformation conserves what it should and that the binning matches `numpy.histogram` exactly. For anyone handling Rivet analysis objects on ARM.

## Run it

```bash
make stage RECIPE=yoda      # once: the checks only — 5000 fills from a pinned seed
spawn task run --spec "$(make -s spec RECIPE=yoda)" --wait
make ls RECIPE=yoda

h = yoda.Histo1D(10, 0.0, 10.0, "/h")
for x in data: h.fill(x)
sum(b.numEntries() for b in h.bins())   # == IN-RANGE fills, not h.numEntries()
h.clone()                               # copy.deepcopy raises on these objects
```

## Make it yours

| In the recipe | Swap for | What to know |
|---|---|---|
| `Σ bins == in-range fills` | `== h.numEntries()` | **those differ by design.** `numEntries()` counts all 5000 fills; the bins hold the 3556 inside `[0,10)`. yoda 2.x has no `underflow`/`overflow`/`totalDbn`, and `bins(includeFlows=True)` raises. |
| `xStdDev` vs numpy `ddof=1` | `ddof=0` | **yoda reports the SAMPLE sd.** Against `ddof=1` it agrees to **2.2e-16**; against `ddof=0` it looks **1.0e-04** wrong. Same trap as [root](../root/README.md)'s `GetStdDev`. |
| `clone()` | `copy.deepcopy` | deepcopy raises `self._ptr cannot be converted to a Python object for pickling` — a message that points at pickling, not at the fix. |
| a fixture that straddles the axis | data that fits inside | 29% of fills land outside on purpose. A fixture that fitted would make the in-range identity vacuous. |
| `Histo1D` | `Profile1D`, `Counter`, 2-D/3-D | yoda 2.x renamed the classes (`BinnedHisto1D` under the hood); only 1-D histograms are exercised here. |

**Leave the fixture.** Nothing is staged: 5000 values from `default_rng(20261011)`, so every reference is an exact count or numpy's binning of the same array. **Scale it** by raising the fill count — the identities are exact at any size.

## Shape, size, cost

One task on `m8g.large` (2 vCPU / 8 GiB), TTL 30m as a **backstop** with `cost_limit` $0.12 as the real guard. All the checks are **9 s** of a 2m11s window — **1m22s of that is pulling the image**, because `hep` carries ROOT, Geant4 and Pythia8 for the other recipes in it ([layout](../../patterns/layout-and-effective-cost.md)).

<details>
<summary>As shipped: exact conservation through four transformations, a numpy cross-check with a control, and three quantities that are not what they look like</summary>

### The checks

| observable | assertion | observed |
|---|---|---|
| staged bytes | match pin | 1 of 1 |
| yoda / numpy | read from the running install | **2.1.4** / 2.5.4 |
| fills in range / out | fixture must straddle | 3556 / **1444** |
| **`numEntries()`** | **== all fills** | **5000** |
| **Σ over bins** | **== in-range fills** | **3556** |
| **vs `numpy.histogram`** | **every bin equal** | **max diff 0** |
| control: shifted data | must differ | **54** counts |
| **write → read** | identical totals and bins | identical |
| **`scaleW(3)`: sumW** | **ratio == 3 exactly** | **3.000000000000000** |
| **`scaleW(3)`: sumW2** | **ratio == 9 exactly** (k²) | **9.000000000000000** |
| `scaleW`: numEntries | unchanged | unchanged |
| **`h + h`** | **sumW and numEntries × 2 exactly** | **2.000000000000000** |
| **`rebinXBy(2)`** | **in-range sum unchanged** | 10→5, **0.000e+00** |
| **`rebinXTo(edges)`** | **in-range sum unchanged** | 10→3, **0.000e+00** |
| `normalize(1.0)` | within ~4.5 ULP of 1 | **0.5 ULP** (1.110e-16) |
| **`xMean` vs numpy, all fills** | **bit-identical** | **0.000e+00** |
| **`xStdDev` vs numpy `ddof=1`** | < 1e-12 | **2.204e-16** |
| `xStdDev` vs `ddof=0` | *recorded* — must be further | 1.000e-04 |
| determinism | refill gives the same histogram | identical |

### Conservation through four transformations

What a histogram library must get right is accounting, and each of these is exact:

```text
scaleW(3)        sumW  x 3.000000000000000     sumW2 x 9.000000000000000   (= 3^2)
h + h            sumW  x 2.000000000000000     numEntries x 2.000000000000000
rebinXBy(2)      10 -> 5 bins    in-range sum 3556.000000 -> 3556.000000
rebinXTo(edges)  10 -> 3 bins    in-range sum 3556.000000 -> 3556.000000
write -> read    totals and every bin identical, 1200 bytes
```

`sumW2` scaling by **k²** rather than k is the one that would be easy to get wrong and hard
to notice: weights are squared there, so a library that scaled it linearly would quietly
corrupt every error bar while leaving the bin contents right.

### The cross-check, and its control

yoda's binning is compared against `numpy.histogram` on the same array with the same edges —
an independent implementation, so agreement is about the binning rather than internal
consistency:

```text
yoda   339 374 330 384 349 380 369 339 348 344
numpy  339 374 330 384 349 380 369 339 348 344     max diff 0
CONTROL: numpy on data shifted by one bin width    max diff 54
```

Staging also checks the conventions can't silently diverge: numpy's `range=` makes bins
half-open except the last, which includes the upper edge, so it asserts **no value sits
exactly on `hi`** — with a continuous draw that probability is zero, but a fixture that
violated it would make the count comparison convention-dependent rather than a real test.

### Three quantities that are not what they look like

Every one of these would have failed a correct library, and each was measured before anything
was asserted:

**1. `Σ bins ≠ numEntries`, by design.** `numEntries()` is 5000 — every fill. The bins hold
3556 — those inside `[0,10)`. The 1444 difference fell off the axis, and yoda 2.x provides no
flow bins to find it in (`underflow`, `overflow` and `totalDbn` are all absent;
`bins(includeFlows=True)` raises `TypeError`). So the only correct identity is `Σ bins ==
in-range fills`.

**2. The moments cover all fills, not the binned subset.** `xMean` is bit-identical to numpy's
mean of all 5000 values (`0.000e+00`). Compared against the in-range subset instead it looks
**1.1e-02** out on the mean and **41%** out on the width. Both numbers are correct — they
answer different questions, and the recipe records the in-range value next to the asserted one
so the distinction is visible.

**3. `xStdDev` is the sample sd.** Against numpy `ddof=1` it agrees to **2.204e-16**; against
`ddof=0`, **1.000e-04**. The run asserts not just the match but that `ddof=1` is the *closer*
of the two, so a future convention change reports which estimator it now matches rather than
failing with an opaque tolerance message. This is the second `ddof` trap in this catalog —
[root](../root/README.md)'s `TH1::GetStdDev` was the first — which is enough to treat as a
standing hazard in any histogram comparison.

### One bound that is not exact, and why

```text
normalize(1.0)  ->  0.99999999999999989      0.5 ULP from 1.0
```

**This failed the first run, and the reason is worth recording.** A probe printed the value as
`1.000000000000000` under `%.15f`, so exact equality was asserted from a *formatted* number —
`%.15f` has not the digits to show a one-ULP shortfall. `normalize` divides by a 3556-term sum,
so a ULP was always expected. The bound is now ~4.5 ULP, justified by that division, and the
deviation is reported in ULP rather than as a bare decimal. The other equalities above are
genuinely exact and still asserted with `==`.

### Pins

| | |
|---|---|
| data | **none.** 5000 draws from `default_rng(20261011)`; references are exact counts and `numpy.histogram` on the same array |
| checks | `identities.py`, pinned by sha256 |
| image | `quay.io/aarchsci/hep@sha256:6754001a09ae036ba3…` — yoda 2.1.4, fastjet 3.5.2.0, root 6.40.04, pythia8, hepmc3 3.3.1, lhapdf 6.5.6, numpy 2.5.4, python 3.14.8 |

cosign-verified against `playgroundlogic/aarchsci`; the signature covers the **manifest-list**
digest, so verify the tag and pin the arm64 digest. The written `h.yoda` is staged out so a
round-trip failure is inspectable without a rerun.

### On x86_64

The same task on `m8i.large`, through the `hep` env's linux-64 twin
(`envs-x86_64/hep-327e2aedf1d0.linux-64.lock`: 331 of 352 versions identical, yoda and numpy among
them; the 13 that differ are off this path). `score.tsv` and `identities.log` are
**byte-identical** to arm64 — the same seeded draws, every bin, every exact ratio, the 0.5 ULP
`normalize` and the 2.204e-16 `xStdDev` residual. Counting and exact scaling cannot differ; that
the two round-off values also agree is an observation, not an assertion.

### Not covered

**Everything except 1-D histograms.** `Profile1D`, `Counter`, `BinnedEstimate`, the 2-D and 3-D
variants, and `Scatter` conversion are all untouched, as is the annotation/metadata layer that
Rivet actually relies on.

**Rivet itself**, which is what produces and consumes these files — it is not in this env, so
nothing here exercises a real analysis. **Reading reference data**: no published `.yoda` from an
experiment is parsed, which would turn the round-trip into a reproduce-a-published-file check.
**Weighted fills and multiweights** — every fill here has weight 1, so the `sumW`/`sumW2`
distinction is exercised only through `scaleW`. **Error propagation** through division and
`divideBy`, where the sample-vs-population question above would matter most.

**Two siblings in this env are out of reach, and both were checked rather than assumed:**
`lhapdf` imports at 6.5.6 but ships **zero PDF sets** (`availablePDFSets()` → `[]`), so its sum
rules are unreachable without staged data from an unversioned `current/` URL; and `hepmc3` has
**no python bindings** — `pyhepmc` and `HepMC3` both fail to import, leaving only the C++
library and `HepMC3-config` ([the fifth deferral
reason](../../practices/what-this-does-not-cover.md)).

</details>
