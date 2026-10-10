---
tool: calculix
tool_version: "2.23"
env: fem-cfd
image: quay.io/aarchsci/fem-cfd@sha256:dd8638389359818beeeb4e626cda21107dee9dc87e180bb4e5ae9f95444cd14f
spawn_version: 0.126.1
last_verified: 2026-10-10
---
# CalculiX — 225 static FE cases against their committed references, exactly

Runs CalculiX's own regression suite on Graviton4 and compares every result with the author's committed reference using his comparison script, not ours. For anyone doing finite-element analysis on ARM.

## Run it

```bash
make stage RECIPE=calculix     # once: the ccx_2.23 test suite, 13.5 MB, sha256-pinned
spawn task run --spec "$(make -s spec RECIPE=calculix)" --wait
make ls RECIPE=calculix

export OMP_NUM_THREADS=1       # upstream's own `compare` sets this
ccx achtel2                    # -> achtel2.dat
./datcheck.pl achtel2          # silence means it matches achtel2.dat.ref
```

## Make it yours

| In the recipe | Swap for | What to know |
|---|---|---|
| `datcheck.pl` | a diff of your own | **don't.** It takes each data block's maximum, then flags a value only if it exceeds 1e-3 relative error against *either* the reference at that position *or* that block maximum — and skips participation-factor blocks, which depend on the cyclic sector. A plain diff measures a method difference. |
| `OMP_NUM_THREADS=1` | your thread count | upstream sets it, and so does this recipe: a result being compared against a fixed reference must not move with thread count. |
| the whole suite | your own model | 497 cases run in **137 s**, so the suite is cheap. Your own `.inp` needs its own reference — that is the hard part, not the running. |
| `*STATIC` cases | `*DYNAMIC`, `*FREQUENCY` | **the analysis type decides what you can assert.** Static solves reproduce exactly here; time-integration and eigenvalue cases do not. See below — it is the result, not a caveat. |
| no gmsh | gmsh for meshing | gmsh never landed in this env (requested as aarchsci#24, calculix arrived alone). It is not needed: the suite ships its own meshes. |

**Leave the suite.** It is the point — 507 references somebody else committed, so the recipe reproduces published numbers instead of asserting a band on one it invented. **Scale it** by adding your own case; everything here transfers except the reference, which only you can supply.

## Shape, size, cost

One task on `c8g.xlarge` (4 vCPU / 16 GiB), TTL 60m as a **backstop** with `cost_limit` $0.25 as the real guard. The suite is **137 s** for 497 cases; the slowest single case is `thermomech` at 13 s. TTL was retightened from 240m after the first run measured it ([sizing](../../patterns/sizing.md)).

<details>
<summary>As shipped: 225 static cases exact, 12 path-like deviations reported not asserted, and why 629 references are really 507</summary>

### The checks

| observable | assertion | observed |
|---|---|---|
| staged bytes | match pins | 2 of 2 |
| suite contents | 629 `.dat.ref`, path encodes `ccx_2.23` | asserted at staging |
| achtel2 canary | reference line present before paying for a box | asserted at staging |
| **ccx version** | **== 2.23, read from the binary** | **2.23** |
| perl present | `datcheck.pl` cannot run without it | 5.40.1 |
| cases excluded, no usable reference | — | 131 |
| cases excluded, declare a prerequisite | — | 10 |
| **static cases run** | — | **225** |
| **static cases deviating** | **== 0** | **0** |
| path-like cases run | — | 272 |
| path-like cases deviating | *reported, not asserted* | **12** |
| **achtel2 node 3** | **byte-equal to the committed line** | **exact** |
| suite wall | — | 137 s (slowest `thermomech`, 13 s) |

### The result: a clean split by analysis type

**Every pure `*STATIC` case reproduces its committed reference. Every deviation is a
time-dependent or eigenvalue analysis.** 225 and 272 cases respectively, 0 and 12 deviations —
the split is total, with nothing straddling it.

That is this project's own [fixed-point-versus-path line](../../practices/reference-from-tests.md)
showing up in a new place. A `*STATIC` solve is a fixed point: it solves once and converges, so
the last bits cannot drift anywhere. `*DYNAMIC` integrates in time, `*FREQUENCY` extracts
eigenvalues — both accumulate, and a build with a different compiler or BLAS kernel than the
author's will diverge.

So the recipe **asserts zero deviations on the static cases** and **reports** the path-like ones.
Asserting zero there would be asserting that a conda arm64 build matches the author's toolchain,
which is not a property of CalculiX.

### What the 12 deviations are — and the one that is not rounding

The magnitudes do **not** all look like last-bit drift, and saying they do would be the mistake
this project warns against when it says not to over-apply the kernel finding:

```text
acou1       ref 1.418326e-01  got 1.419745e-01   1.0e-3 relative   (barely over threshold)
beamt6      absolute error 1.255400e-10                            (values near zero)
beamptied5  ref 2.004731e+06  got 1.890823e+06   5.7e-2 relative   (block max 2.968844e+06)
```

`beamptied5` is a **5.7% difference** on a tied-contact `*FREQUENCY` case. That is not rounding,
and **this recipe does not explain it.** Mode ordering within a near-degenerate subspace, a
different eigensolver path, or a genuine build difference are all candidates; distinguishing them
needs work this recipe does not do. What is established is the *classification* — the deviation
is confined to path-like analyses — not a mechanism for each one.

The clean split is strong evidence that the class of computation matters. It is not evidence that
every deviation inside that class has the same cause.

### Three exclusions, each by a rule the suite itself declares

No curated list, because a list rots:

1. **No usable reference — 131 cases.** 9 `.inp` files have no `.dat.ref` at all (generated
   `.rfn` refinement cases), and **122 `.dat.ref` files are zero bytes**. Nothing to compare
   against either way.
2. **Declares a prerequisite — 10 cases.** `*RESTART,READ`, `*SUBMODEL` or `*VIEWFACTOR,READ`
   means the case reads an artifact a prior manual step must produce. `beamread.inp` states it in
   its own comment: *"please run example beamwrite and copy beamwrite.rout to beamread.rin"*.
   **Upstream's `compare` does not do that either**, so these fail upstream too — not a build
   problem.
3. **Everything else runs**, split by analysis type.

**Rule 1 supersedes upstream's own skip list**, which hardcodes five `.rfn` cases by name when
there are nine. "Has no usable reference" catches all of them and needs no maintenance.

### 629 committed references are really 507

Worth stating because this project's own roadmap recorded "629 committed `.dat.ref` files" and
this page would have repeated it: **122 of the 629 are empty**. Only **507** carry content, and
after the prerequisite exclusions **497** are actually compared. The headline count was a file
count, not a reference count.

### Pins

| | |
|---|---|
| suite | `ccx_2.23.test.tar.bz2` from the author's site — 13,468,187 B, sha256 `be2259fd…` |
| image | `quay.io/aarchsci/fem-cfd@sha256:dd863838…` — calculix 2.23, petsc 3.25.6, python 3.14.8 |

**The version match is checked twice**, because a reference from another version is a different
number: the archive path encodes `ccx_2.23`, and the task reads `ccx -v` and refuses to continue
unless it reports 2.23.

**The publisher issues no checksum file**, so the sha256 is recorded in the staging script and
asserted on every fetch — tier 2 in this project's terms
([how inputs are pinned](../../practices/what-this-does-not-cover.md)). Staging also pins one
reference *by content*: `achtel2`'s node-3 displacement triple must be present in the archive
before an instance is paid for, so a silent repack fails for free.

cosign-verified against `playgroundlogic/aarchsci`; the signature covers the **manifest-list**
digest, so verify the tag and pin the arm64 digest.

### Run + verify

```sh
make stage RECIPE=calculix
spawn task run --spec "$(make -s spec RECIPE=calculix)" --wait
aws s3 cp "s3://$(make -s print-bucket)/runs/calculix/r1/score.tsv" -
```

Fails on a pin mismatch, a `ccx` that is not 2.23, a missing perl, any static case deviating from
its committed reference, or an `achtel2` node-3 row that differs — but check the bucket regardless
([exit 0 isn't proof](../../practices/container-path.md)). `failures-static.txt`,
`failures-path.txt` and per-case `timings.tsv` are staged out so a failure is diagnosable without
a rerun.

### Not covered

**`.frd` comparison** — upstream also runs `frdcheck.pl` where a `.frd.ref` exists; this recipe
compares `.dat` only, so displacement/stress *fields* are unchecked where the printed output does
not include them. **The 10 prerequisite cases** could be made to pass by implementing the two-step
their inputs describe, which would be more complete than upstream's own harness. Also: the 122
empty-reference cases (whether they are placeholders or deliberate), multi-threaded runs
(`OMP_NUM_THREADS` is pinned to 1 so nothing here speaks to parallel scaling), CalculiX's CFD
solver as a subject in its own right, and meshing — gmsh is absent from this env.

</details>
