---
tool: cp2k
tool_version: "2026.2"
env: cp2k
image: quay.io/aarchsci/cp2k@sha256:a2b70e16570c46a30ee44f6567668cd10610db77c7f48017721845c9a81d8c6f
spawn_version: 0.126.1
last_verified: 2026-10-10
---
# CP2K — 52 of its own committed references, reproduced on Graviton

Runs 25 Quickstep regtest directories on Graviton4 and compares every energy with the reference CP2K ships beside it, at the tolerance CP2K ships with it. For anyone doing DFT or ab-initio MD on ARM.

## Run it

```bash
make stage RECIPE=cp2k      # once: the checks only — the references ship inside the image
spawn task run --spec "$(make -s spec RECIPE=cp2k)" --wait
make ls RECIPE=cp2k

T=/opt/conda/etc/conda/test-files/cp2k/1/tests
cp -r "$T/QS/regtest-hybrid-1" /tmp/w && cd /tmp/w   # a whole directory, in manifest order
for inp in $(python3 -c "import tomllib;print(*tomllib.load(open('TEST_FILES.toml','rb')))"); do
  cp2k.psmp -i "$inp" -o "$inp.out"                  # mpiexec -n 2 cp2k.psmp … for the 2-rank leg
done
```

## Make it yours

| In the recipe | Swap for | What to know |
|---|---|---|
| a whole directory at a time | one input | **don't.** The directory is the unit: later inputs restart from wavefunctions earlier ones write, so a lone input aborts on a missing `*-RESTART.wfn`. No keyword in the input declares it — it is in the ordering. |
| 25 directories | `MAX_DIRS` | 87 of the 293 eligible Quickstep directories carry an `E_total` reference; the 25 smallest give 52 references in **7m51s**. Raising it costs only wall time. |
| the two-clause bound | upstream's tolerance alone | **don't** — upstream's tightest is **1.18 machine epsilons** relative, which is "bit-identical to the author's build". A different BLAS cannot meet it. See below. |
| `cp2k.psmp` + `mpiexec -n 2` | more ranks | the 2-rank leg is a decomposition *invariance*, not a scaling claim — these cases are seconds each. |
| `E_total` | one of the other 164 matchers | CP2K's matcher registry covers forces, stress, gaps and more; `E_total` is the one carrying a reference and a tolerance on most Quickstep cases. |

**Leave the suite.** It is the point — 52 energies somebody else committed, with the author's own tolerance attached to each, so the recipe reproduces published numbers instead of asserting a band on one it invented. **Scale it** by raising `MAX_DIRS`, or by pointing the same two-clause comparison at a non-QS section.

## Shape, size, cost

One task on `c8g.xlarge` (4 vCPU / 16 GiB), TTL 120m as a **backstop** with `cost_limit` $0.50 as the real guard. 25 directories run twice (serial and 2-rank) in **7m51s** of the 9m04s billed, so boot and image pull are the smaller half here for once — but **these timings are still not compute cost** ([layout](../../patterns/layout-and-effective-cost.md)).

<details>
<summary>As shipped: 52 references at upstream's own tolerances, why the bound needs two clauses, and the restart ordering that cost six runs</summary>

### The checks

| observable | assertion | observed |
|---|---|---|
| staged bytes | match pins | 3 of 3 |
| `CP2K_DATA_DIR` | set, a directory, non-empty | 68 basis/pseudopotential files |
| **`cp2kflags`** | **`parallel elpa scalapack libxc` all present** | **all present** |
| cp2k version / revision | read from the binary | **2026.2** / `37b1558` |
| TEST_DIRS entries this build may run | — | 387 of 460; **293** Quickstep |
| directories carrying an `E_total` reference | — | 87 |
| directories run | 25 smallest, ≤12 inputs each | 25 |
| **references checked** | **≥ 30** | **52** |
| **within upstream's own per-case tolerance** | *reported* | **44 of 52** |
| **needing the 1e-9 Ha clause** | *reported* | **8** |
| **failing both clauses** | **== 0** | **0** |
| worst deviation | — | 2.387e-06 Ha (**0.48×** its own 5e-06 tolerance) |
| worst deviation among the 8 | — | 1.405e-11 Ha, **1.9e-13 relative** |
| **rank count, every case** | **== 1 and 2 as launched** | **1 and 2** |
| serial vs 2-rank | within the same two-clause bound | worst 8.749e-09 Ha |
| serial == 2-rank bit-identically | *reported* | **22 of 52** |
| inputs whose matcher found nothing | *reported* | 1 |

### A directory is the unit, and that took six runs to accept

The references, the tolerances and the matchers all ship with the package, so nothing numeric is
written here — a manifest entry is
`{matcher="E_total", tol=3e-13, ref=-21.04944231395054}` and the run reads both halves. The
staging script refuses to upload `identities.py` if it contains any float literal with six or
more decimals in executable code, checked on the AST so a docstring quoting the manifest format
does not trip it.

What the design got wrong six times was *selection*. Picking individual inputs and filtering out
the ones with dependencies fails, because **the dependency is not declared anywhere greppable**:
early inputs in a directory write the wavefunctions later ones restart from, so an isolated
input dies with `An error occurred opening the file 'Ar-RESTART.wfn'`. Filtering on
`SCF_GUESS RESTART`, `WFN_RESTART_FILE_NAME` and `&EXT_RESTART` moved which inputs were chosen
(1,049 eligible entries down to 940) and the chosen ones still aborted. Running the whole
directory in manifest order — which is what CP2K's own harness does — makes the problem vanish,
because the predecessors are what satisfy it.

### Why the bound needs two clauses, and why neither works alone

A deviation passes if it is within **upstream's own tolerance for that case, or within 1e-9 Ha**.
Both halves were forced by measurement, in opposite directions:

**Upstream's tolerance is sometimes far tighter than any other toolchain can reach.** These
tolerances span **2.0e-14 to 5.0e-06** — seven orders of magnitude — and the tight end is set at
the bit level on the author's build:

```text
QS/regtest-hybrid-1  H2O-hybrid-bhandhlyp.inp   tol 2.0e-14 abs  =  1.18 eps relative
QS/regtest-hybrid-1  H2O-hybrid-pbe0.inp        tol 3.0e-14 abs  =  1.77 eps relative
```

Asking for 1.18 machine epsilons is asking for the same bits, which is a property of the
reference build's BLAS and compiler, not of CP2K. All eight cases over tolerance are of that
shape — a handful of epsilons past a tolerance itself only a handful of epsilons wide:

```text
                                            upstream tol      our deviation
sto             H2O_t1.inp                    47.6 eps          836.3 eps     17.6x tol
hybrid-1        H2O-hybrid-bhandh.inp          1.2 eps            5.1 eps      4.3x tol
linearscaling   w3-filter-2.inp                4.0 eps            7.4 eps      1.8x tol
meta            acid_water_meta.inp            4.5 eps            8.0 eps      1.8x tol
hybrid-1        H2O-hybrid-b3lyp.inp           2.4 eps            4.2 eps      1.8x tol
hybrid-1        H2O-hybrid-pbe0.inp            1.8 eps            2.5 eps      1.4x tol
as, as-qcschema be.inp                       310.8 eps          419.6 eps      1.3x tol
```

**But a flat bound of ours is worse, and that was measured too.** A single 1e-9 Ha criterion
*failed* `QS/regtest-sasccs/H2_sasccs.inp` at 1.124e-08 — a case upstream deliberately allows
1e-07 on. Overriding a looser upstream tolerance with a tighter one of our own asserts something
about the test that its author denies. So the clauses divide by who knows what: **upstream's
tolerance is authoritative where it is looser**, because it encodes that case's conditioning;
**1e-9 Ha covers where upstream's is tighter** than a different build can reach, and is justified
by the chemistry — 1 kcal/mol is 1.5936e-3 Ha, so 1e-9 is 1.6e6× inside the scale at which any
conclusion could change. Each clause's count is reported, so the split is visible rather than
buried inside a `max()`.

### Upstream's tolerances encode the fixed-point/path line themselves

Worth noticing, as an observation and not an assertion: every case upstream allows ≥1e-8 on is an
**outer-iteration** method, and every case it pins near machine precision is a single-point solve.

```text
cdft-4-1         HeH-cdft-broyden-bt1explicit.inp   5e-06   Broyden constraint solve
tddfpt-sf-force  h2o_pbe_opt.inp                    1e-06   geometry optimisation on TDDFPT forces
sasccs           H2_sasccs.inp                      1e-07   self-consistent continuum solvation
lsroks           ch2o_rs.inp, ch2o_hf.inp           2e-08   large-scale ROKS
kp-spglib        c_k290_space_group_ops.inp         1e-08   k-point symmetry reduction
hybrid-1         H2O-hybrid-*.inp                   2e-14   single-point hybrid DFT
```

That is this project's own [fixed-point-versus-path line](../../practices/reference-from-tests.md)
appearing in the test author's tolerance choices rather than in our results. A converged SCF is a
fixed point and can be pinned at the bit; a constraint solver or a geometry optimisation is a path
and accumulates. The recipe did not impose that classification — it read it off upstream's numbers.

### The parallel leg is an invariance, not a benchmark

Every case runs serially and on 2 ranks, and **CP2K's own reported process count is asserted to be
1 and 2 as launched** — conda-forge ships `nompi` builds at higher build numbers, so `mpiexec -n 2`
on a serial binary runs two independent rank-0 calculations that print the right energy and pass a
naive comparison vacuously ([rank-count guard](../../practices/mpi-rank-count.md)). **22 of 52** are
then bit-identical between the two decompositions and the worst differs by 8.749e-09 Ha, which is
0.11× that case's own tolerance.

### Pins

| | |
|---|---|
| references, tolerances, matchers, TEST_DIRS | **inside the image**, installed with the package — the version match is structural, not asserted by hand |
| `matchers.py`, `TEST_DIRS` | staged from tag `v2026.2` as a pinned fallback, sha256-asserted; 165 matchers and an `E_total` entry verified at staging |
| checks | `identities.py`, pinned by sha256, AST-checked for hardcoded references |
| image | `quay.io/aarchsci/cp2k@sha256:a2b70e16…` — cp2k 2026.2 (`37b1558`), elpa, scalapack, libxc, python 3.14.8 |

`CP2K_DATA_DIR` is exported by the env's own Dockerfile (aarchsci#30); conda-forge installs the 68
basis-set and pseudopotential files but sets no such variable, and an unset value fails deep inside
the first SCF, so it is checked before anything runs.

cosign-verified against `playgroundlogic/aarchsci`; the signature covers the **manifest-list**
digest, so verify the tag and pin the arm64 digest.

### Run + verify

```sh
make stage RECIPE=cp2k
spawn task run --spec "$(make -s spec RECIPE=cp2k)" --wait
aws s3 cp "s3://$(make -s print-bucket)/runs/cp2k/r1/score.tsv" -
aws s3 cp "s3://$(make -s print-bucket)/runs/cp2k/r1/cp2k-diag.txt" -   # per-case deviations
```

Fails on a pin mismatch, an unset `CP2K_DATA_DIR`, a build missing any of `parallel elpa scalapack
libxc`, a reported rank count that is not what was launched, fewer than 30 references checked, or
any reference outside both clauses — but check the bucket regardless
([exit 0 isn't proof](../../practices/container-path.md)). `cp2k-diag.txt` carries every case's
reference, tolerance and both deviations and is written **before** any assertion, so a failing run
is diagnosable without a rerun.

### Not covered

**`QS/regtest-casino/h2_ecp_casino.inp` produces output the `E_total` matcher finds nothing in** —
reported, not suppressed, and not diagnosed here; it is one case of 53 attempted.

Beyond that: the 62 Quickstep directories with references that were not among the 25 smallest, and
every non-Quickstep section (`Fist`, `SE`, `TMC`, `QMMM`, `Fit`, `optimize_input` and the rest) —
the selection is Quickstep-only by rule, not by survey. Also: directories with more than 12 inputs;
matchers other than `E_total`, so forces, stresses and band gaps are unchecked even where the same
manifests carry references for them; MD and geometry-optimisation *trajectories* as opposed to their
final energies; rank counts above 2, so nothing here is a scaling claim; and the ~57 TEST_DIRS
entries this build's flags exclude, which would need a differently configured env.

</details>
