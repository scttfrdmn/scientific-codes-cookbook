---
tool: nwchem
tool_version: 7.3.0
env: dft
image: quay.io/aarchsci/dft@sha256:0740fab9721da533ce153cae3590b1c6822dd0decfa1838b0753e76ba4434a4e
spawn_version: 0.115.0
last_verified: 2026-10-03
---
# NWChem — caffeine at B3LYP/6-31G*, serial and on 4 MPI ranks

Runs a 217-basis-function DFT energy twice, serial and parallel, and asserts NWChem's own rank count. For anyone doing quantum chemistry with MPI on ARM.

## Run it

```bash
spawn task run --spec "$(make -s spec RECIPE=nwchem)" --wait   # 128 s serial + 68 s on 4 ranks
make ls RECIPE=nwchem   # nwchem-serial.out + nwchem-4rank.out + smoke-check.txt

mpiexec -n 4 nwchem caffeine.nw        # geometry and basis inline; nothing staged
```

## Make it yours

| In the recipe | Swap for | What to know |
|---|---|---|
| caffeine, 24 atoms | your geometry | written inline in the spec — no staging, so the molecule is the spec. |
| `6-31G*` / `b3lyp` | your basis and functional | both change the energy; the basis-function count is asserted, so a silent basis change fails. |
| `-n 4` | more ranks | **and pass `--bind-to none` if you run concurrent jobs** — see below. |

**Leave the molecule** — 217 basis functions is where parallelism starts doing something, which is
what this recipe is for. **Scale it** by basis or system size; both move the
[knee](../../patterns/layout-and-effective-cost.md).

## Which box — and the layout matters more

`c8g.2xlarge`: **128 s serial, 68 s on 4 ranks.** Measured on one 16-core box, for 10,000 of these
calculations:

| | cost |
|---|---|
| 16 concurrent 1-rank jobs | **$16** |
| one job on 16 ranks, repeated | $44 |
| one instance per job, 16 ranks each | $300 |

Ranks-per-job is the *smallest* of those levers — adding ranks to one calculation returns 33%
efficiency at 16, while adding *jobs* returns nearly 100%, so packing wins **2.72×** on throughput
for 0–6.5% contention ([the run](../../measurements/nwchem-real/README.md)). Why $/core-hour cannot
see any of this: [layout and effective cost](../../patterns/layout-and-effective-cost.md).

<details>
<summary>As shipped: the rank-count assertion that stops a vacuous pass, and a reference energy</summary>

### The checks

| observable | assertion | observed |
|---|---|---|
| **MPI ranks** | **exactly 4** (NWChem's own `nproc`) | **4** |
| serial ranks | exactly 1 | **1** |
| both converged | an energy was reached, both runs | **yes / yes** |
| basis functions | exactly 217 | **217** |
| **serial == 4-rank** | **< 1e-6 Ha** | **4.55e-08** |
| DFT energy | −625.538048 ± 1e-5 Ha | **−625.538048227205** |

**The rank-count assertion is what stops the equality passing vacuously.** conda-forge ships nompi
builds at *higher* build numbers than the openmpi ones, so an unpinned solve hands back a serial
binary that under `mpiexec -n 4` runs four independent rank-0 calculations — each printing the same
energy. "Parallel equals serial" would then pass while nothing was parallel. Reading NWChem's own
`nproc` proves the parallelism that the equality cross-validates
([the practice](../../practices/mpi-rank-count.md)).

**The energy identity is exact, not a tolerance.** DFT converges to a fixed point, so dividing the
work across 4 ranks must not move the answer; 4.55e-08 Ha is SCF-convergence noise. The serial
energy reproduced to all twelve digits across runs, which is what licensed pinning it.

The basis-function count is asserted because a silently different basis is the failure that would
otherwise look like a wrong energy. Both the regex and the value came from NWChem's output
(`AO basis - number of functions:`) — an earlier version of this recipe invented both, and the regex
failing is the only reason a fabricated count never became an assertion.

### Packing concurrent jobs: `--bind-to none` is load-bearing

Each `mpiexec` believes it owns the machine and binds rank *k* to core *k*. Eight concurrent 2-rank
jobs therefore put 16 processes on **2 cores**: measured **2342 s at exactly 2.00 cores busy**,
against **110 s at 15.81** for the same work with `--bind-to none`. A 21× slowdown that reads as
memory-bandwidth contention, and the only tell is the integral core count.

### Pins

| | data tier |
|---|---|
| NWChem 7.3.0 | `quay.io/aarchsci/dft@sha256:0740fab9721d…` (`linux/arm64`, openmpi build) |
| geometry + basis | inline in the spec; basis sets ship in the env |

Nothing is staged, so there is no `stage-inputs.sh`. The task first checks `NWCHEM_BASIS_LIBRARY` is
set and holds `sto-3g` — without activation NWChem falls back to the feedstock build path baked into
the binary and exits 255, which is a confusing failure to debug from an exit code alone.

### Run + verify

```sh
spawn task run --spec "$(make -s spec RECIPE=nwchem)" --wait
make ls RECIPE=nwchem
```

Expect `smoke-check.txt` with `mpi_ranks 4`, `basis_functions 217` and `serial_eq_4rank` under 1e-6.

</details>
