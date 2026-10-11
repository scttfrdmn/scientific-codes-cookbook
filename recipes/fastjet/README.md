---
tool: fastjet
tool_version: "3.5.2.0"
env: hep
image: quay.io/aarchsci/hep@sha256:6754001a09ae036ba3dd862af0b8cef9d377fc3fba2b7da1cad94740214ee0f7
spawn_version: 0.127.0
last_verified: 2026-10-11
---
# FastJet — infrared and collinear safety, which is why these algorithms exist

Clusters jets on Graviton4 and checks the property anti-k_t was designed to have: soft particles and collinear splittings must not change the hard jets. For anyone doing jet physics on ARM.

## Run it

```bash
make stage RECIPE=fastjet     # once: the checks only — the event is generated in-run
spawn task run --spec "$(make -s spec RECIPE=fastjet)" --wait
make ls RECIPE=fastjet

jd = fastjet.JetDefinition(fastjet.antikt_algorithm, 0.4)
cs = fastjet.ClusterSequence(ak.Array([{"px":…, "py":…, "pz":…, "E":…}, …]), jd)
cs.inclusive_jets(min_pt=5.0)       # sums to the input 4-momentum, exactly
```

## Make it yours

| In the recipe | Swap for | What to know |
|---|---|---|
| soft particle **collinear with a jet** | an isolated direction | **the isolated version tests nothing.** It forms its own sub-threshold jet and `min_pt` removes it, so "jets unchanged" is the *pt cut* passing, not infrared safety. Put the soft particle where it gets clustered. |
| asserting **change/ε = 1** | a bound like `< 1e-3` | a bound passes an implementation whose error happens to be small. The ratio says the change **is** the soft momentum: measured 1.0000 at ε = 1e-3, 1e-6 and 1e-9. |
| the graded Δφ control | the collinear result alone | exact-zero at perfect collinearity would also hold if splitting were ignored. At Δφ = 0.05/0.20/1.00 the change grows 1.6e-02 → 2.5e-01 → a different jet count. |
| the 4-particle fixture | your own event | separations are checked in **rapidity–φ**, not Cartesian momenta — staging asserts the closest pair is 1.5051 apart against `R = 0.4`, so one jet per particle holds. |
| `antikt_algorithm` | `cambridge`, `kt` | all three agree **exactly** on well-separated input. They differ on substructure, which this page does not test. |

**Leave the fixture.** Nothing is staged: four hand-written particles plus a 13 TeV Pythia event generated in-run from a fixed seed, so every reference is a property of the algorithm. **Scale it** by raising `pTHatMin` or clustering a full event sample — the invariances are independent of multiplicity, and hold on 613 particles here.

## Shape, size, cost

One task on `m8g.large` (2 vCPU / 8 GiB), TTL 30m as a **backstop** with `cost_limit` $0.12 as the real guard. All the physics is **10 s** of a 3m20s window — and **2m24s of that is pulling the image**, because `hep` carries ROOT, Geant4 and Pythia8. 45 s is installing Docker ([layout](../../patterns/layout-and-effective-cost.md)).

<details>
<summary>As shipped: an exact collinear invariance, infrared safety as a scaling law, graded controls for both, and a 613-particle Pythia event</summary>

### The checks

| observable | assertion | observed |
|---|---|---|
| staged bytes | match pin | 1 of 1 |
| fastjet / awkward / numpy | read from the running install | **3.5.2.0** / 2.14.0 / 2.5.4 |
| jets from 4 separated particles | **== 4** | 4, leading pt 50.000000000 |
| **momentum conservation** | **`== 0` exactly** | **0.000e+00** |
| **collinear split** | **`== 0` exactly** | **0.000e+00** |
| control: Δφ = 0.05 / 0.20 / 1.00 | must change, and **increase** | 1.562e-02 / 2.498e-01 / jet count 4→5 |
| **IR safety, ε = 1e-3** | **change/ε within 0.1%** | **1.000000** |
| **IR safety, ε = 1e-6** | **change/ε within 0.1%** | **1.000000** |
| **IR safety, ε = 1e-9** | **change/ε within 0.1%** | **0.999997** |
| control: 42.5 GeV particle added | must change the jets | **5 jets** vs 4 |
| **anti-k_t vs Cambridge vs k_t** | **`== 0` exactly** | **0.000e+00** both |
| Pythia event, fixed seed | ≥ 50 final visible particles | **613** |
| **momentum conservation, 613 particles** | **< 1e-11** | **7.741e-14** |
| jets above 20 GeV | ≥ 1 | 3, leading **113.6598** |
| **collinear split on the Pythia event** | **< 1e-11** | **0.000e+00** |
| determinism, clustering | identical | yes |
| determinism, Pythia | same seed reproduces | **613 particles, identical** |

### The invariance is the algorithm's purpose, not a side effect

Anti-k_t exists *because* earlier cone algorithms were not infrared and collinear safe, which
made fixed-order QCD predictions divergent. So this is not a property the tool happens to have —
it is the thing it was built to provide, and it can be checked with no reference value and no
tolerance:

```text
exactly collinear split of a hard particle    jets change by 0.000e+00
same split, on the 613-particle Pythia event  jets change by 0.000e+00
```

Exact in both cases: the two halves recombine into the jet their parent made.

### Infrared safety stated as a scaling law, not a threshold

A soft particle clustered into a jet adds its own momentum and nothing else. So the right claim
is not "the change is small" but "the change **is** ε":

```text
eps = 1e-03    jet change 1.000e-03    ratio 1.000000
eps = 1e-06    jet change 1.000e-06    ratio 1.000000
eps = 1e-09    jet change 1.000e-09    ratio 0.999997
```

Six orders of magnitude, ratio pinned to 1. A bound like `< 1e-3` would pass an implementation
whose error merely happened to be small; the ratio would not. Same reasoning as asserting a
convergence *order* rather than a single-resolution error
([paraview](../paraview/README.md)).

**And the first version of this test measured the wrong thing.** The soft particle initially sat
in an isolated direction, where it formed its own sub-threshold jet and `min_pt` discarded it — so
"the jets did not change" was the **pt cut** working, not infrared safety. Placing it collinear
with a hard particle is what makes it a test of the clustering.

### Both invariances carry a control, and one is graded

"Nothing changed" is the easiest result to obtain for the wrong reason, so each exact zero is
paired with a case that **must** differ:

```text
collinear   dphi = 0      change 0.000e+00     <- the invariance
control     dphi = 0.05   change 1.562e-02
control     dphi = 0.20   change 2.498e-01
control     dphi = 1.00   jet count 4 -> 5     (beyond R = 0.4)
soft        eps -> 0      change -> eps        <- the invariance
control     42.5 GeV      jet count 4 -> 5
```

The Δφ control being **graded** is what makes it convincing: the change grows monotonically with
the departure from collinearity, so the zero at Δφ = 0 is a limit rather than an accident. The run
asserts that ordering, not just that the control is non-zero.

### Three clustering sequences, one answer

anti-k_t, Cambridge/Aachen and k_t cluster in different orders — hardest-first, angular-ordered
and softest-first — and on particles further apart than `R` they must still produce the same jets:

```text
anti-k_t      4 jets, leading pt 50.000000000
Cambridge/A   4 jets, leading pt 50.000000000   diff from anti-k_t 0.000e+00
k_t           4 jets, leading pt 50.000000000   diff from anti-k_t 0.000e+00
```

This is a consistency check within one library rather than a cross-code one, so it is weaker than
the agreements elsewhere in this catalog — but it is exact, and a bug in any one sequence would
break it.

### A realistic event, with nothing staged

`pythia8` is in this env, so the recipe generates its own 13 TeV QCD event (`pTHatMin = 100 GeV`,
seed 20261011) rather than shipping data: **613 final visible particles**, 3 jets above 20 GeV,
leading pt 113.66. Momentum conservation there is **7.741e-14** — float64's floor after hundreds
of additions of O(10³) GeV numbers, not a tolerance chosen to fit. The event is byte-reproducible
from the seed, which is what lets the numbers above be quoted at all.

### Pins

| | |
|---|---|
| data | **none.** Four hand-written particles plus a Pythia event from `Random:seed = 20261011`; references are IRC safety, momentum conservation and algorithm agreement |
| checks | `identities.py`, pinned by sha256 |
| image | `quay.io/aarchsci/hep@sha256:6754001a09ae036ba3…` — fastjet 3.5.2.0, pythia8, root 6.40.04, hepmc3 3.3.1, lhapdf 6.5.6, yoda 2.1.4, uproot 5.7.7, awkward 2.14.0, python 3.14.8 |

cosign-verified against `playgroundlogic/aarchsci`; the signature covers the **manifest-list**
digest, so verify the tag and pin the arm64 digest.

Staging computes the fixture's separations in **rapidity–φ** — the variable the algorithms cluster
in, not the Cartesian momenta the fixture is written in — and asserts the closest pair is 1.5051
apart against `R = 0.4`, that every particle clears the 5 GeV threshold, and that the widest
control split exceeds `R`. Without those, "one jet per particle" would be an assumption.

### Run + verify

```sh
make stage RECIPE=fastjet
spawn task run --spec "$(make -s spec RECIPE=fastjet)" --wait
aws s3 cp "s3://$(make -s print-bucket)/runs/fastjet/r1/score.tsv" -
```

Fails on a pin mismatch, a jet count other than 4 on the fixture, jets that do not sum to the
input momentum, a collinear split that changes anything, a non-collinear control that changes
nothing or is not graded, a soft particle whose `change/ε` departs from 1 by more than 0.1%, a jet
count that survives a 42.5 GeV addition, any disagreement between the three algorithms, a Pythia
event under 50 particles or missing momentum by more than 1e-11, or a seed that fails to reproduce
— but check the bucket regardless
([exit 0 isn't proof](../../practices/container-path.md)).

### Not covered

**Jet substructure**, which is where the three algorithms genuinely differ: no N-subjettiness,
no soft drop, no mass-drop tagging, no `exclusive_jets`. The agreement above holds only because
the fixture is well separated.

**Jet areas and pileup subtraction** — `AreaDefinition`, `GridMedianBackgroundEstimator` and
`Subtractor` are untouched, and they are much of why FastJet is used at the LHC. **Plugins**
(SISCone, CDFMidPoint), including the IRC-*unsafe* algorithms that would make the sharpest possible
control here. **The rest of this env**: `hepmc3`, `lhapdf` and `yoda` are all unexercised, and
`geant4` has no python bindings at any version ([recorded as a deferral
reason](../../practices/what-this-does-not-cover.md)). **Detector effects** — particles go
straight from the generator to the clusterer with no simulation or reconstruction.

</details>
