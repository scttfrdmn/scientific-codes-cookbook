# Running the common research-computing codes on spore.host

A recipe catalog for the codes that dominate academic clusters. Everything
routes through two tools — `truffle` finds the instance, `spawn` launches it
with a TTL and tears it down — so the catalog is organized by *execution
shape*, not by 50 unrelated snippets. Find your code in the table, note its
shape, copy that shape's template.

All examples run in **your own AWS account**. Every instance carries a hard TTL,
so a forgotten job self-terminates.

---

## The one rule that bites everyone first

`--on-complete terminate` acts on a **completion sentinel**, not on your command
exiting. If the command finishes but the sentinel was never written, the box
sits idle until its TTL. So every headless recipe ends with:

```
--command "…your work… && touch /tmp/SPAWN_COMPLETE" --on-complete terminate
```

`touch /tmp/SPAWN_COMPLETE` and `spored complete` are equivalent. On an MPI
cluster, write it **only from rank 0** (see Shape C).

---

## The seven shapes

| Shape | What it is | Codes that fit | Core flags |
|-------|-----------|----------------|-----------|
| **A** | Interactive single node (CPU or 1 GPU) | Jupyter, R, MATLAB, Julia, VMD, ParaView, cryoSPARC UI | `launch` → `connect` |
| **B** | Headless single node, terminate on done | VASP (small), QE, ORCA, Gaussian, Psi4, Q-Chem, AMBER, OpenMM, STAR, Salmon, IQ-TREE, RAxML | `--command … --on-complete terminate` |
| **C** | Multi-node tightly-coupled MPI | GROMACS (large), LAMMPS, NAMD, CP2K, NWChem, WRF, CESM, OpenFOAM, Nek5000, MITgcm, ROMS, NEMO, GADGET, AREPO, GIZMO, MOOSE, GENE, XGC, GTC | `--count N --mpi --efa` |
| **D** | Fan-out one dataset across N identical nodes | BLAST, HMMER, per-sample BWA/samtools/GATK | `--count N` + `JOB_ARRAY_INDEX` |
| **E** | Parameter sweep, one instance per combination | AutoDock Vina, Rosetta, virtual screening, phylogenetic bootstraps, price-perf benchmarks | `--param-file sweep.yaml` |
| **F** | Workflow engine drives the jobs | Nextflow, Snakemake, CWL, WDL | `nf-spawn` adapter |
| **G** | Wait for scarce GPU capacity, then run | Large PyTorch/TensorFlow training, RELION/cryoSPARC on 8×GPU, AlphaFold at scale | `lagotto` + Capacity Blocks |

---

## Shape A — Interactive single node

For exploratory work where you SSH in and drive it by hand. Idle timeout stops
the box when you walk away; TTL is the backstop.

```sh
# Find a box
truffle find "epyc genoa 64gb"

# Launch with an idle timeout so it stops when you stop working
spawn launch analysis \
  --instance-type m8a.4xlarge \
  --ttl 12h \
  --idle-timeout 1h

spawn connect analysis            # SSH in, run R / MATLAB / Julia / a notebook
spawn status analysis             # cost + TTL countdown
spawn stop analysis               # pause (keeps the disk); or terminate to destroy
```

For a **GPU workstation** (cryoSPARC web UI, interactive PyTorch, VMD with CUDA):

```sh
spawn launch gpu-station \
  --instance-type g6e.2xlarge \
  --ttl 8h --idle-timeout 45m
spawn connect gpu-station
```

---

## Shape B — Headless single node, terminate on done

The workhorse for a single simulation or a single sample. The command runs, the
sentinel fires, the box dies. Push results to S3 *before* the sentinel.

```sh
spawn launch qchem-run \
  --instance-type r8i.8xlarge \
  --ttl 6h \
  --on-complete terminate \
  --command "cd /scratch && orca input.inp > out.out && \
             aws s3 cp out.out s3://my-bucket/orca/ && \
             touch /tmp/SPAWN_COMPLETE"
```

Wait for it from your laptop (exit 0=complete, 1=failed, 2=running, 3=error):

```sh
while spawn status qchem-run --check-complete; [ $? -eq 2 ]; do sleep 30; done
```

**Scratch-heavy codes** (Gaussian, ORCA, cryoSPARC steps) want fast local NVMe.
Pick an instance with instance storage (the `d` suffix — `r8id`, `c8id`) and
point the code's scratch dir at the ephemeral mount rather than EBS.

---

## Shape C — Multi-node tightly-coupled MPI

For codes that communicate across nodes every timestep. `--mpi` wires up
passwordless SSH, the hostfile, and OpenMPI; `--efa` adds the low-latency
fabric. Launch is all-or-nothing — you never pay for a half-formed cluster.

```sh
spawn launch wrf-run \
  --instance-type hpc7g.16xlarge \
  --count 8 --mpi --efa \
  --ttl 12h \
  --on-complete terminate \
  --command "mpirun -n 512 ./wrf.exe && \
             if [ \$OMPI_COMM_WORLD_RANK -eq 0 ]; then \
               aws s3 cp wrfout_* s3://my-bucket/wrf/ && touch /tmp/SPAWN_COMPLETE; fi"
```

The rank-0 guard on the sentinel is mandatory — the command runs on every node,
but only rank 0 should signal completion.

**Shared input across all nodes** → attach FSx Lustre backed by S3:

```sh
spawn launch cesm-run \
  --instance-type hpc7a.96xlarge \
  --count 16 --mpi --efa \
  --ttl 24h \
  --fsx-create --fsx-lifecycle ephemeral \
  --fsx-s3-bucket my-data --fsx-import-path s3://my-data/inputs/ \
  --fsx-export-path s3://my-data/outputs/ --fsx-mount-point /fsx \
  --on-complete terminate \
  --command "mpirun -n 1536 ./cesm.exe && \
             if [ \$OMPI_COMM_WORLD_RANK -eq 0 ]; then touch /tmp/SPAWN_COMPLETE; fi"
```

Ephemeral FSx is reaped with the cluster. AZ fallback moves the whole cluster as
a unit if the primary AZ is out of capacity.

---

## Shape D — Fan-out one dataset across N nodes

A fixed count of identical nodes, each taking a shard by index. Best when one
big input splits cleanly (a BLAST query file, a BAM to region-call, a batch of
samples).

```sh
spawn launch blast-fanout \
  --count 8 \
  --instance-type c8i.8xlarge \
  --ttl 3h \
  --on-complete terminate \
  --command "aws s3 cp s3://my-bucket/queries.fa /tmp/ && \
    split -n l/\$((JOB_ARRAY_INDEX+1))/\$JOB_ARRAY_SIZE /tmp/queries.fa > /tmp/chunk.fa && \
    blastp -query /tmp/chunk.fa -db nr -out /tmp/hits.\$JOB_ARRAY_INDEX && \
    aws s3 cp /tmp/hits.\$JOB_ARRAY_INDEX s3://my-bucket/blast/ && \
    touch /tmp/SPAWN_COMPLETE"
```

Each node sees `JOB_ARRAY_INDEX` (0…N-1) and `JOB_ARRAY_SIZE`. Terminate the
whole set with `spawn terminate --job-array-name blast-fanout`.

---

## Shape E — Parameter sweep

One instance per parameter combination, each with its own TTL. This is docking
libraries, screening campaigns, bootstrap replicates — and price-performance
benchmarks. Preview cost with `--estimate-only` first.

```yaml
# vina-sweep.yaml — one ligand batch per instance
defaults:
  instance_type: c8a.4xlarge
  ttl: 2h
  on_complete: terminate
  spot: true
  command: "vina --receptor rec.pdbqt --ligand {ligand} --out {ligand}.out && \
            aws s3 cp {ligand}.out s3://my-bucket/vina/ && touch /tmp/SPAWN_COMPLETE"
params:
  - ligand: batch01
  - ligand: batch02
  - ligand: batch03
```

```sh
spawn launch vina-screen --param-file vina-sweep.yaml --estimate-only   # preview
spawn launch vina-screen --param-file vina-sweep.yaml --max-concurrent 20 --budget 150
spawn sweep status <sweep-id>
spawn sweep collect <sweep-id> --output results.json
```

`spawn` doesn't expand a grid for you — generate the `params` list with a few
lines of Python (`itertools.product`) and write the YAML. An entry can override
`instance_type`, so the **same workload across c8i / c8a / c8g / g6e** in one
sweep is the natural price-perf benchmark; spawn picks the right AMI per family.

---

## Shape F — Workflow engine

If the pipeline is already written in Nextflow/Snakemake, don't rewrite it — use
the adapter so each process/rule lands on its own spawn-managed instance.

```sh
# Nextflow with the nf-spawn executor
nextflow run main.nf -profile spore
```

Individually the tasks look like Shape D, but they aggregate into serious
consumption, and the bottleneck is usually the shared filesystem and task
granularity, not the instance type. See the workflow-engines guide for maturity
status per engine.

---

## Shape G — Wait for scarce GPU capacity

For 8×H100-class jobs where the constraint is *getting* the GPUs. `truffle`
confirms quota, `lagotto` waits for capacity, or you reserve a Capacity Block up
front.

```sh
# Is there quota and capacity right now?
truffle quotas --family P --regions us-east-1
truffle az p5.48xlarge

# Wait for capacity to appear, then launch automatically
lagotto watch --instance-type p5.48xlarge --regions us-east-1,us-west-2 \
  --spawn-config train.yaml

# Or reserve a future window (billed up front, non-refundable)
truffle capacity-blocks --instance-type p5.48xlarge --count 2 --duration-hours 48
spawn capacity-block purchase <offering-id> --instance-type p5.48xlarge \
  --count 2 --duration-hours 48 --region us-east-1
```

Multi-node training uses Shape C flags on top (`--mpi --efa`).

---

## The catalog — 50 codes mapped

Instance families are sensible starting points, not tuned optima; the last
column is the detail most likely to trip you up. "NVMe" means pick a `d`-suffix
instance and point scratch at the local disk.

### Molecular dynamics

| Code | Shape | Instance family | Watch out for |
|------|-------|----------------|---------------|
| GROMACS | B (1 GPU) / C (large) | `g6e`, or `hpc7a` ×N for very large | One good GPU beats many CPUs for most academic system sizes; go MPI only when a single GPU can't hold it |
| LAMMPS | C | `hpc7a`, `hpc7g`; `g6e` for GPU package | KOKKOS/GPU build vs CPU build changes the instance choice entirely |
| NAMD | B (multi-GPU node) / C | `g6e.12xlarge`, `p4d` | NAMD 3 is GPU-resident — a single multi-GPU node often beats a cluster |
| AMBER | B | `g6e.2xlarge` (`pmemd.cuda`) | `pmemd.cuda` is single-GPU; don't buy a cluster for it |
| OpenMM | B | `g6e.2xlarge` | Single GPU; it's the engine under many higher-level tools |
| CHARMM | B / C | `hpc7a`; `g6e` if CUDA-built | Force-field dev workflows are CPU; production runs may be GPU |

### DFT and quantum chemistry

| Code | Shape | Instance family | Watch out for |
|------|-------|----------------|---------------|
| VASP | B (small) / C (large) | `hpc6a`, `hpc7a` (memory bandwidth + FP64) | Memory-bandwidth bound; tune NPAR/KPAR to the core count |
| Quantum ESPRESSO | C | `hpc7a`, `hpc6a` | FP64-heavy; EFA matters at scale |
| CP2K | C | `hpc7a` (high memory/core) | Hybrid MPI+OpenMP — set OMP threads, don't oversubscribe |
| Gaussian | B | `r8id` (NVMe scratch) | Scratch-disk bound; point `GAUSS_SCRDIR` at local NVMe, not EBS |
| ORCA | B | `r8id`, `c8id` (NVMe scratch) | Same scratch story; multi-node Linda is rare in academia |
| NWChem | C | `hpc7a`, `hpc6a` | One of the few QC codes that genuinely scales multi-node |
| Psi4 | B | `r8i.4xlarge` | Single-node shared memory; size RAM to the basis set |
| Q-Chem | B | `r8i`, `r8id` | License server reachable from the instance; scratch on NVMe |
| SIESTA / ABINIT | B / C | `hpc7a`, `c8i` | Localized-orbital (SIESTA) scales differently than plane-wave |

### CFD and structural/FEA

| Code | Shape | Instance family | Watch out for |
|------|-------|----------------|---------------|
| OpenFOAM | C | `hpc7a`, `hpc7g` | `decomposePar` subdomains must match `-n`; EFA for large cases |
| ANSYS Fluent | C | `hpc6a`, `c8i` | License server + reachable network; RSM/MPI config |
| Abaqus | B / C | `r8id`, `hpc6a` | Explicit vs standard changes CPU/memory profile; NVMe scratch |
| COMSOL | B | `r8i` (large RAM) | Memory-hungry; often single fat node beats a cluster |
| LS-DYNA | C | `hpc6a`, `hpc7a` | Explicit FEA scales; license + MPP build |
| Star-CCM+ | C | `hpc7a`, `c8i` | Power-on-demand vs license server; EFA for big meshes |
| Nek5000 / NekRS | C (NekRS: GPU) | `hpc7a`; NekRS `p4d`/`p5` | NekRS is GPU spectral-element — very different box than Nek5000 |
| MOOSE | C | `hpc7a`, `c8i` | PETSc underneath; scales with the physics kernels loaded |

### Climate, weather, ocean

| Code | Shape | Instance family | Watch out for |
|------|-------|----------------|---------------|
| WRF | C | `hpc7g` (Graviton is strong here), `hpc7a` | EFA-sensitive; domain decomposition sizing |
| CESM / CAM | C | `hpc7a.96xlarge` ×N | Large multi-component; FSx Lustre for input datasets |
| MITgcm | C | `hpc7a`, `hpc7g` | Tile decomposition; EFA at scale |
| ROMS | C | `hpc7a` | NetCDF I/O can bottleneck — stage to FSx |
| NEMO | C | `hpc7a`, `hpc6a` | XIOS I/O servers want their own ranks |

### Genomics and bioinformatics

| Code | Shape | Instance family | Watch out for |
|------|-------|----------------|---------------|
| BWA / samtools / bcftools | D (per sample) | `c8i.4xlarge` | Tiny per-job; time-to-start dominates, not tuning |
| GATK | D | `r8i.4xlarge` | Java heap sizing; some steps are memory-bound |
| STAR | B | `r8i.8xlarge` (~32–64 GB for human) | Genome index must fit in RAM |
| Salmon / kallisto | D | `c8i.2xlarge` | Fast and light — batch many per node or fan out |
| BLAST+ | D | `r8i` (DB in RAM) | Load the DB into memory once; shard the query, not the DB |
| HMMER | D | `c8i.4xlarge` | CPU-bound profile search; fan out over the DB |
| IQ-TREE / RAxML | B (single) / E (bootstraps) | `c8a.8xlarge` | Bootstraps parallelize as a sweep, not within one run |

### Cryo-EM and structure

| Code | Shape | Instance family | Watch out for |
|------|-------|----------------|---------------|
| RELION | C (some steps) / G | `p4d`, `g6e.12xlarge` + NVMe | Multi-GPU + fast local scratch; motion-corr vs classification differ |
| cryoSPARC | A (UI) + G (workers) | `g6e`, `p4d` + NVMe | Runs a persistent web app; workers are the GPU spend |
| AlphaFold / ColabFold | G (GPU) | `g6e.2xlarge`, `p4d` | MSA step is CPU/RAM-heavy, folding is GPU — two different profiles |
| Rosetta | E | `c8a.4xlarge` | Embarrassingly parallel trajectories → sweep over seeds |
| AutoDock Vina | E | `c8a.4xlarge`, spot | Pure fan-out over the ligand library |
| Schrödinger suite | E / B (Desmond GPU) | `c8a`; `g6e` for Desmond MD | License server; screening is a sweep, MD is Shape B |

### Astrophysics and cosmology

| Code | Shape | Instance family | Watch out for |
|------|-------|----------------|---------------|
| GADGET / AREPO / GIZMO | C | `hpc7a.96xlarge` (memory/node) | Memory-per-rank sizing; EFA essential at scale |

### Fusion / plasma

| Code | Shape | Instance family | Watch out for |
|------|-------|----------------|---------------|
| GENE / XGC / GTC | C (GPU) | `p4d`, `p5` + EFA | Tiny user count, huge node-hours; the leadership-scale end |

### AI / ML frameworks

| Code | Shape | Instance family | Watch out for |
|------|-------|----------------|---------------|
| PyTorch | B (1 GPU) / C+G (multi-node) | `g6e`, `p4d`, `p5` | Single GPU is Shape B; multi-node adds `--mpi --efa` + Capacity Blocks |
| TensorFlow | B / C+G | `g6e`, `p4d` | Same as PyTorch; declining share but still deeply embedded |

### Statistics, interpreted, viz

| Code | Shape | Instance family | Watch out for |
|------|-------|----------------|---------------|
| Python / NumPy / SciPy | A / B / D | anything | The substrate for half the catalog; shape depends on the workload |
| R | A / D | `r8i` for big frames | Huge job count, small footprint — optimize start time |
| MATLAB | A / E | `m8a`, `c8i` | License; Parallel Server jobs map to a sweep |
| Julia | A / B | `c8a`, `g6e` if CUDA | `Distributed`/threads decide single vs multi-node |
| VMD | A | `g6e.2xlarge` | GPU for rendering; usually paired with an MD run |
| ParaView / VisIt | A | `g6e` (or CPU for small) | Client/server split for large data; pvserver on the instance |

---

## Two commands you'll use constantly

```sh
spawn list                        # everything running, with cost + TTL
spawn list --sweep-name <name>    # every instance in a sweep
```

And when you forget one:

```sh
spawn terminate <name>            # or ask your AI assistant via the MCP server
```
