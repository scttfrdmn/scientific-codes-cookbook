# Assert the MPI rank count from inside the run

**The symptom:** you launch a tool under `mpirun -n 2`, it prints the right energy, the run passes — and it was never parallel. It ran two independent serial calculations that each printed the same number.

**Why this happens, and why it's silent:** conda-forge ships `nompi` builds of many parallel codes at *higher build numbers* than the openmpi ones, so an unpinned solve quietly prefers the serial variant. Launched under `mpirun -n 2`, a serial binary doesn't error — it runs twice, as two independent rank-0 processes, and each prints the same energy. A naive "the parallel run matches the serial run" check then passes **vacuously**: of course they match, they're the same serial calculation run twice.

This is the trap worth naming plainly: **a silent serial build produces correct physics and a false claim about the build.** The numbers are right; the recipe's claim that it demonstrated parallelism is wrong. It's the same shape as a conservation check passing on reads that mapped nothing — the assertion proved something true but not the thing it was supposed to prove. Assert what you're claiming, not what's convenient to observe.

**Do this — two moves:**

1. **Pin the openmpi build in the env**, so the resolver can't hand you the serial variant (`lammps=*=cpu_*mpi_openmpi*`, `siesta=*=mpi_openmpi*`, and so on). This is the fix; the assertion below is the proof it took.
2. **Read the rank count the tool itself reports, and assert it equals what you launched.** Every parallel code announces its process count in its own banner — use that, not your launch command (which is what you're trying to verify):

   | tool | what it prints | assert |
   |---|---|---|
   | LAMMPS | `with 2 MPI task(s)` | == 2 |
   | SIESTA | `Running on 2 nodes` | == 2 |
   | NWChem | `nproc = 2` | == 2 |
   | GROMACS | `Using N MPI process(es)` | == launched |
   | GPAW | `gpaw.mpi.world.size` | == 2 |

The serial-vs-parallel *energy* agreement is a genuine cross-validation — it catches an MPI stack that links but reduces forces wrongly. But it only means something once you *also* know the parallel leg was parallel. The rank-count assertion is what proves the parallelism that the energy agreement is cross-validating actually happened.

GPAW's is the sharpest case: its env was found to be **one resolver tie away** from shipping a serial build that would have passed every other check in the recipe. `world.size == 2` is the single assertion that would have caught it.
