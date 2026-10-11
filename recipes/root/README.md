---
tool: root
tool_version: "6.40.04"
env: hep
image: quay.io/aarchsci/hep@sha256:45441294ef5ef11379cf577ae746ecd91cb0e20e303cf3e557abeb15b84d201d
spawn_version: 0.126.1
last_verified: 2026-10-11
---
# ROOT — its own file format read back by an independent implementation, on Graviton

Writes a TTree with ROOT and reads it with uproot, which shares no code with ROOT, then checks ROOT's statistics and math against independent references. For anyone doing HEP analysis on ARM.

## Run it

```bash
make stage RECIPE=root     # once: the checks only — the sample is seeded, the references are identities
spawn task run --spec "$(make -s spec RECIPE=root)" --wait
make ls RECIPE=root

ROOT.RDF.FromNumpy({"x": x, "i": i, "w": w}).Snapshot("t", "out.root", ["x","i","w"])
uproot.open("out.root")["t"].arrays(library="np")   # bit-identical, float64 and int64
ROOT.Math.Boost(0.3, -0.1, 0.45)(v4).M()            # == v4.M(); a boost cannot change mass
h.Fit("gaus", "QSN")                                # params within ROOT's own reported errors
```

## Make it yours

| In the recipe | Swap for | What to know |
|---|---|---|
| uproot as the reader | `TFile` again | **this is the point.** uproot is pure Python and links no ROOT, so it is a second *implementation* of the format — not ROOT's I/O path run twice. |
| `GetMean`/`GetStdDev` | estimating from bins | **`TH1` keeps raw moments as you `Fill`**, so these are the *unbinned* statistics, and `GetStdDev` is the population form (`ddof=0`). Estimating from bin centres is a different quantity — 1.2e-03 off here. |
| `TMath::BesselI0` | `scipy.special.i0` | **~2.3e-08 against SciPy** — a polynomial approximation, about single precision. `Erf` and `LnGamma` are bit-identical; `BesselI0` is not. |
| the seeded sample | your own data | the fit is bounded by ROOT's *own* reported errors, so it transfers — but keep a fixed seed or the claim goes flaky. |
| `TTree` | `RNTuple` | both writer and reader are present in 6.40 and unexercised here. |

**Leave the fixture.** Nothing is fetched: the sample is a seeded RNG draw, and every reference is an exact identity, an independent implementation already in the image, or an uncertainty the tool reports about itself. **Scale it** to your own trees — the format round-trip and the boost invariance hold at any size.

## Shape, size, cost

One task on `m8g.xlarge` (4 vCPU / 16 GiB), TTL 45m as a **backstop** with `cost_limit` $0.20 as the real guard. The analysis is **23 s** of a 2m31s window, and **83 s of that is pulling the image** — `hep` is **2.86 GB compressed**, the largest in this catalog, so the pull dominates and TTLs here must allow for it ([layout](../../patterns/layout-and-effective-cost.md)).

<details>
<summary>As shipped: a bit-identical format round-trip through an unrelated reader, two bit-identical special functions, and a corrected causal claim</summary>

### The checks

| observable | assertion | observed |
|---|---|---|
| staged bytes | match pin | 1 of 1 |
| ROOT / uproot / scipy / numpy | read from the running install | **6.40.04** / 5.7.7 / 1.18.1 / 2.5.3 |
| **ROOT → uproot round-trip** | **bit-identical, dtypes preserved** | **20,000 entries**, float64 + int64 |
| tree and branches visible to uproot | `t`, with `i`/`w`/`x` | yes |
| ROOT file written | — | 343,249 B |
| **`Integral()` vs sum of bins** | **exactly equal** | 20000.0 == 20000.0 |
| bins + under/overflow | account for every fill | 20,000, under/over 0/0 |
| **`GetMean` vs numpy, raw values** | **< 1e-12 relative** | **4.996e-16** |
| **`GetStdDev` vs numpy `ddof=0`, raw** | **< 1e-12 relative** | **7.072e-16** |
| estimating from bin centres instead | *reported* | 1.174e-03 / 6.989e-04 |
| **`TMath::Erf` vs `scipy.special`** | **bit-identical** | **0.000e+00**, 5 of 5 |
| **`TMath::LnGamma` vs `scipy.special`** | **bit-identical** | **0.000e+00**, 5 of 5 |
| `TMath::BesselI0` vs SciPy | *reported, not held to the same bar* | **2.347e-08** |
| **invariant mass under 3 boosts** | **< 1e-13 relative** | **1.870e-16** |
| fit mean / sigma | within 4 of ROOT's own errors | **0.04σ** / **0.50σ** |
| fit χ²/ndf | *reported* | 0.9155 (ndf 76) |

### The headline: a second implementation of the format, not a second pass through one

uproot is pure Python and links no ROOT at all. So writing a `TTree` with ROOT and reading it with
uproot tests the **format**, which is what you want to know before trusting years of data to it.
All 20,000 entries come back **bit-identical** with dtypes preserved — `float64` as `float64`,
`int64` as `int64`, no silent widening.

That distinction is load-bearing and this catalog keeps running into it: comparing terra with
rasterio compares one GDAL, and r-arrow with pyarrow compares one libarrow. Here the two sides
genuinely share nothing, which is the same property that makes qutip valuable for
[qiskit](../qiskit/README.md).

### A corrected causal claim, which is why the raw-vs-binned split is on this page

A first version of this recipe asserted that `TH1::GetStdDev()` is computed from **bin centres**.
It is not. `TH1` accumulates raw moments as you `Fill`, so `GetMean` and `GetStdDev` are the
*unbinned* statistics — and they match numpy on the raw values to **4.996e-16** and **7.072e-16**.

The mistake came from mis-reading a probe: ROOT gave `0.999424` where numpy gave `0.999924`, and
the gap is exactly `√((n−1)/n)` — a **`ddof`** difference, not a binning one. Asserting the wrong
mechanism made the run fail loudly, which is what a reference derived independently in staging is
for.

Estimating the same two numbers **from the bins** is a genuinely different quantity, and that is
reported rather than asserted, because a histogram read back from a file has only bin contents:

```text
                         mean                 std
raw moments (ROOT)   0.35037335908764938   1.25588042611065265
from bin centres     0.34996199999999977   1.25675813049130491
cost of binning           1.174e-03            6.989e-04     at 0.12-wide bins
```

### Two bit-identical special functions, and one that is not

`TMath::Erf` and `TMath::LnGamma` are **bit-identical** to `scipy.special` at every point tested —
asserted as equality. `TMath::BesselI0` is **2.347e-08** relative, because ROOT implements it as a
polynomial approximation at roughly single precision.

Applying one loose bound to all three would have passed while hiding both facts: that two are
exact, and that the third should not be relied on for double-precision work. Splitting them keeps
both, and the BesselI0 figure is reported with its number rather than buried.

### Identities that need no reference

**A boost cannot change an invariant mass.** `M = 151.98684153570664535` is unchanged to
**1.870e-16** relative across three different boost velocities. A sign error in the boost matrix
cannot satisfy this, however plausible the output looks — and staging confirms the test 4-vector is
timelike (`m² = 23100`) first, so the invariance is a statement about a real mass and not about
noise.

**A histogram partitions its fills.** `Integral()` equals the sum of bin contents exactly, and bins
plus under/overflow account for all 20,000. Staging confirms **zero** fills would land outside the
range — otherwise under/overflow would absorb them and the assertion would pass trivially.

### The fit is bounded by the uncertainty ROOT reports about itself

The data is drawn from a known Gaussian, so the fitted parameters are expected within their own
errors — not at the true values. The bound comes from the fit:

```text
mean   0.349662 ± 0.008906   truth 0.35    0.04 sigma
sigma  1.253179 ± 0.006388   truth 1.25    0.50 sigma     chi2/ndf = 0.9155, ndf 76
```

Staging first checks the seeded sample *can* recover the truth — its mean is 0.04 SEM from `MU` —
so the test is fair rather than a coin flip. Same discipline as bounding against a sampler's
reported MCSE instead of inventing a tolerance.

### Pins

| | |
|---|---|
| data | **none.** The sample is a seeded `default_rng(20261011)` draw; references are identities |
| checks | `identities.py`, pinned by sha256 |
| image | `quay.io/aarchsci/hep@sha256:45441294ef5e…` — ROOT 6.40.04, uproot 5.7.7, scipy 1.18.1, pythia8 8.312, fastjet 3.5.2.0, lhapdf 6.5.6, yoda 2.1.4, python 3.14.8 |

cosign-verified against `playgroundlogic/aarchsci`; the signature covers the **manifest-list**
digest (`f7d374cfb0c3`), so verify the tag and pin the arm64 digest.

### Run + verify

```sh
make stage RECIPE=root
spawn task run --spec "$(make -s spec RECIPE=root)" --wait
aws s3 cp "s3://$(make -s print-bucket)/runs/root/r1/score.tsv" -
```

Fails on a pin mismatch, a round-trip that is not bit-identical or loses a dtype, an `Integral()`
that disagrees with its bins, ROOT statistics that depart from numpy on the same raw values,
`Erf`/`LnGamma` that are not exactly equal to SciPy, a `BesselI0` far worse than the measured
~1e-8, a boost that moves the invariant mass, or a fitted parameter more than 4 of ROOT's own
errors from the truth it was generated from — but check the bucket regardless
([exit 0 isn't proof](../../practices/container-path.md)).

### Not covered

**Geant4, which shares this env and cannot be driven from it.** conda-forge ships the C++ library
and physics datasets for arm64 but **no python bindings at any version** — the `py314` in the build
string comes from linking `libboost_python314.so`, not from a module
([the full reason and its lifting condition](../../practices/what-this-does-not-cover.md)).

**`RNTuple`**, ROOT's new columnar format — writer and reader are both present in 6.40 and would
give a second format round-trip. **`RDataFrame` as a subject** — used here only to write a tree,
not for lazy multi-threaded analysis, which is what it is for. **The rest of the env**: pythia8,
fastjet, lhapdf, yoda, `pyHepMC3`, awkward are installed and unexercised; a generator-to-jets
chain would exercise them together. **TTree with non-trivial branch types** — `std::vector`
branches, jagged arrays and custom classes are where format compatibility actually gets hard, and
this writes three flat columns. No performance claim: 20,000 entries says nothing about ROOT's I/O
at analysis scale.

</details>
