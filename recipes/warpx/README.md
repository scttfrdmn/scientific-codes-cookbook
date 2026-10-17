---
tool: warpx
tool_version: "26.10"
env: pic
image: quay.io/aarchsci/pic@sha256:e02db0b494ce602c6948104fe4a9085b6fc004611f501c48c27eed1874100b95
env_x86_64: pic-bf42d7d6c3f2
spawn_version: 0.130.1
last_verified: 2026-10-16
last_verified_x86_64: 2026-10-17
---
# WarpX — a Langmuir wave against its own closed form, and what 16 threads actually buy

Runs WarpX's own langmuir deck on Graviton4, checks the whole field against the analytic solution at upstream's tolerance, and sweeps threads for a cost figure. For anyone doing particle-in-cell on ARM.

## Run it

```bash
make stage RECIPE=warpx     # once: the checks only — the deck ships inside the image
spawn task run --spec "$(make -s spec RECIPE=warpx)" --wait
make ls RECIPE=warpx

D="$(python3 -c 'import sys; print(sys.prefix)')/etc/conda/test-files/warpx/1/Examples/Tests/langmuir"
printf 'FILE = %s\nalgo.current_deposition = esirkepov\n' "$D/inputs_base_2d" > inputs
OMP_NUM_THREADS=16 warpx.2d inputs        # 128², 80 steps, 0.26 s
yt.load("diags/diag1000080")              # mesh plotfile; species have their own Headers
```

## Make it yours

| In the recipe | Swap for | What to know |
|---|---|---|
| `esirkepov` deposition | `direct` (the shipped variant's choice) | **`direct` is 2.9e8× worse at charge conservation.** Upstream's harness only checks the identity on charge-conserving schemes, and this is why. |
| the appended lines | your own physics | only **diagnostics and swept knobs** are appended; the deck's physics is upstream's, and 7 of its parameters are asserted unchanged. |
| `OMP_NUM_THREADS` | more threads | the only parallel axis — every binary is `NOMPI.OMP`. 16 threads gives **5.19×**, but see the cost table: efficiency picks your *box*, not your thread count. |
| reading openPMD | assuming the axis order | **read `axisLabels`.** It is `z,x` here, and a square grid hides the transpose — ignoring it gives 5.733e+08 against a ~1.6e9 field. |
| 128² / 80 steps | finer or longer | 80 steps at `cfl=1` is **1.0589 plasma periods**, so the oscillation completes once. Shorter and the closed form is untested. |

**Leave the fixture.** It is WarpX's own validation problem and its analytic solution is in upstream's analysis script, so the reference costs nothing and cannot drift. **Scale it** by refining the grid or switching to `warpx.3d` — the identities hold; the tolerance is upstream's.

## Shape, size, cost

One task on `c8g.4xlarge` (16 vCPU / 32 GiB, $0.6381/hr), TTL 60m as a **backstop** with `cost_limit` $0.40 as the real guard — the cap binds before the TTL. Seven WarpX runs plus analysis take **15 s** of a 1m22s window; 39 s is installing Docker and 26 s pulling the 0.49 GB image ([layout](../../patterns/layout-and-effective-cost.md)).

<details>
<summary>As shipped: upstream's deck and upstream's tolerances, a 2.9e8× deposition control, and a thread sweep where efficiency and cost disagree</summary>

### The checks

| observable | assertion | observed |
|---|---|---|
| staged bytes | match pin | 1 of 1 |
| warpx / openpmd-api / yt | read from the running install | **26.10** / 0.17.1 / 4.4.2 |
| upstream's deck present and unmodified | 7 parameters match | **7 of 7** (2,885 B) |
| `n_total` from the deck's own `n0` | — | 4.000e+24 m⁻³ |
| plasma frequency, derived not typed | — | **1.128292e+14 rad/s** |
| elapsed time | ≈1 oscillation | **1.0589** plasma periods |
| **field vs the closed form** | **< 0.0503, upstream's own** | **1.2201e-02** |
| **ε₀·div(E) == ρ, esirkepov** | **< 1e-11, upstream's own** | **3.549e-12** |
| **the same with `direct`** | **must be WORSE** | **1.025e-03** (2.9e8×) |
| **yt vs openPMD, same step** | **== 0 exactly** | **0.000e+00** |
| the same ignoring `axisLabels` | *reported* | **5.733e+08** |
| **OpenMP threads reported** | **== what was launched** | **1, 2, 4, 8, 16** |
| **field error across 5 thread counts** | **spread < 1e-6** | **2.7e-15** |
| thread sweep wall / cost | *reported* | 1.36 s → 0.26 s, $0.00024 → $0.00005 |
| WarpX's own logs staged | — | 38,605 B |

**x86_64** (`c8i.4xlarge`, the `pic-bf42d7d6c3f2` twin): every assertion holds. The field error
(1.2201e-02), yt-vs-openPMD (0.000e+00), the 7 deck parameters and the 5 reported thread counts are
identical to arm64. What differs is what also differs run to run on one chip: the esirkepov residual
(3.514e-12, OpenMP reduction order) and the thread spread (3.3e-15). The sweep runs 1.05 s → 0.28 s and
is flat from 8 to 16 threads (3.81×, 24%) where Graviton4 keeps scaling to 5.2×; the spec's
`INSTANCE_HOURLY_USD` is the c8g rate, so the x86 $/run figures are not c8i costs. linux-64 WarpX is
`nompi` too, and requires `_x86_64-microarch-level >= 3`, so the lock was solved with
`CONDA_OVERRIDE_ARCHSPEC=x86_64_v3`; it differs from arm64 only in executing, fonttools, jedi, libcurl,
libglib, libsqlite and more-itertools.

### The reference is upstream's, and so are both tolerances

The package ships WarpX's `Examples/Tests/langmuir` decks, and upstream's `analysis_2d.py` carries
the analytic solution for them:

```text
E_x = eps · (m_e c² k_x / q_e) · sin(k_x x) cos(k_z z) · sin(ω_p t)
ω_p = sqrt(n e² / (m_e ε₀)),   n = 4e24 = 2e24 electrons + 2e24 positrons
```

So the whole 2D field is compared against a closed form — not a fitted frequency — with `ω_p`
derived from the deck's own `n0`. **Both tolerances are pinned to upstream's source**, not copied
from prose: staging fetches `analysis_2d.py` and `analysis_utils.py` at tag `26.10` and verifies
`tolerance_rel = 0.0503` and the `1e-11` charge bound are still what they are. A tolerance checked
against the source at a pinned tag is a reference; one typed from memory is a number
([the practice](../../practices/reference-from-tests.md)).

Only **diagnostics and swept knobs** are appended to the deck — `rho`/`divE` for the charge
identity, a second openPMD diagnostic, `n_cell`, `max_step`, the deposition scheme. Seven of the
deck's physics parameters are asserted unchanged, so a future upstream edit fails here rather than
silently changing what was verified.

### The deposition control is the strongest thing on this page

Reading upstream's harness showed something worth more than the check it gated: they compute the
charge-conservation identity **only** for `esirkepov`, `vay` or `current_correction` deposition —
and the shipped 2D test variant uses `direct`. So the recipe runs both:

```text
esirkepov   max|div(E) − ρ/ε₀| / max|ρ/ε₀|  =  3.559e-12      inside upstream's 1e-11
direct                                      =  1.025e-03      2.9e8x worse
```

**287 million times worse.** That is asserted as a control: if `direct` ever conserved charge too,
the esirkepov result would be a property of PIC in general rather than of a charge-conserving
deposition, and the identity would be testing nothing. Upstream's gating is the reason it exists;
measuring the gap is what makes it a check.

The esirkepov residual moves about 1% run to run (3.535e-12, 3.566e-12, 3.559e-12, 3.549e-12 across four
runs) from OpenMP reduction ordering in the deposition. It sits ~3× inside the bound, so the
assertion is safe — but it is not a fixed number and is not quoted as one.

### The cross-reader equality, and the convention that hides in a square grid

WarpX writes the same step through two unrelated writers (AMReX plotfile, openPMD), and this reads
them with two unrelated readers (`yt`, `openpmd-api`). They agree **exactly, 0.000e+00** — which is
assertable as an *equality* rather than a tolerance, because any nonzero value would mean one
writer/reader path transforms the field. [aarchsci's D3 suite found this independently](https://github.com/playgroundlogic/aarchsci/issues/35);
it is cited rather than claimed.

Getting there required resolving a convention from metadata rather than by trial:

```text
openPMD axisLabels   z,x        <- the array is (z,x); yt's covering_grid is (x,z)
openPMD unitSI       1.0        <- a no-op here, but read rather than assumed
ignoring axisLabels             -> 5.733e+08 against a ~1.6e9 field
```

**A square 128×128 grid hides a transpose.** A shape check passes, and the axis swap emerges as a
36% field difference that looks like two readers disagreeing about physics. The fix is to read
`axisLabels` and reorder accordingly — asking the format what order it used, not trying transposes
until one matches. Same shape as Qiskit's little-endian trap, where ignoring qubit order gave
7.071e-01 against a bit-identical 0.0: **when two tools agree on the science but differ by an O(1)
amount, suspect the index convention before the numerics.**

### The thread sweep: efficiency and cost point different ways

Every binary is `NOMPI.OMP`, so threads are the only parallel axis. All five rungs run on **one
box** — resizing per rung would measure scaling plus whatever else changed, the error
[GPAW's measurement](../gpaw/README.md) already caught here.

**The invariance is asserted first.** The field error is identical to **2.1e-15** across all five
thread counts, which is what licenses comparing their wall times at all; a timing table means
nothing if the answer moves.

```text
threads   wall     speedup   efficiency   $/run
   1      1.36 s    1.00x       100%      0.00024
   2      0.78 s    1.73x        87%      0.00014
   4      0.47 s    2.89x        72%      0.00008
   8      0.32 s    4.24x        53%      0.00006
  16      0.26 s    5.19x        32%      0.00005
```

Efficiency collapses to **32%**, and cost per run is **lowest at exactly that rung**. There is no
contradiction: the box is rented by the second whether its cores are busy or idle, so on a machine
you have already provisioned, using all of it is always cheapest. **Parallel efficiency is an
argument about which box to rent, not how many threads to use on the one you have.** The
32% figure says a 16-vCPU box is poor value *for this problem size* — not that you should run it on
fewer threads.

### Pins

| | |
|---|---|
| deck | **inside the image** — WarpX's own `Examples/Tests/langmuir/inputs_base_2d`, 2,885 B, 7 parameters asserted |
| tolerances | `analysis_2d.py` + `analysis_utils.py` at tag **26.10**, verified and sha256-recorded at staging |
| checks | `identities.py`, pinned by sha256 |
| image | `quay.io/aarchsci/pic@sha256:e02db0b494ce…` — warpx 26.10 (`NOMPI.OMP.DP.PDP.OPMD.FFT.EB.QED`), openpmd-api 0.17.1, yt 4.4.2, python 3.14.8 |

Nothing scientific is fetched. cosign-verified against `playgroundlogic/aarchsci`; the signature
covers the **manifest-list** digest (`3299b36b7084`), so verify the tag and pin the arm64 digest
above — they are different objects.

### Run + verify

```sh
make stage RECIPE=warpx
spawn task run --spec "$(make -s spec RECIPE=warpx)" --wait
aws s3 cp "s3://$(make -s print-bucket)/runs/warpx/r1/score.tsv" -
```

Fails on a pin mismatch, an upstream deck whose parameters have changed, a field error over
upstream's 0.0503, a charge residual over their 1e-11, a `direct` run that *also* conserves charge,
any difference between the two readers, a thread count WarpX does not report back, or a field error
that moves across thread counts — but check the bucket regardless
([exit 0 isn't proof](../../practices/container-path.md)). WarpX's per-run logs are tarred and
staged out (38,631 B) because they are separate files spawn never uploads unless declared.

### Not covered

**MPI.** Every conda build is `nompi` on every platform — not an arm64 limitation. So nothing here
speaks to domain decomposition, strong scaling across nodes, or EFA. **A convergence ladder**: this
runs one resolution, and aarchsci's D3 measured order 2.12 on a 128→256 refinement, which this does
not reproduce. **3D and RZ**, both built (`warpx.3d`, `warpx.rz`, plus `rcylinder`/`rsphere`) and
unexercised. **Laser-plasma acceleration**, the other shipped example family and what most WarpX
users actually run. **PICMI** as the driving interface — this uses the native deck, though
`pywarpx.picmi` imports and D3 exercises it. **Embedded boundaries, QED and the FFT solver**, all
compiled in and untouched. And no claim about larger problems: at 128² the thread sweep is
measuring a problem too small to fill 16 cores, which is exactly what the 32% efficiency says.

</details>
