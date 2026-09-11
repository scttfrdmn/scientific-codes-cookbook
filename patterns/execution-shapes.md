# Execution shapes on spore.host

> A conceptual map of the *shapes* a job takes on spawn — interactive, headless, MPI,
> fan-out, sweep, workflow, GPU-wait. It is **not** an inventory of shipped recipes (that's
> [the catalog](../catalog/recipes.md)). Most Round-One recipes are Shape B; the other shapes
> sketch where the platform goes, with well-known codes as illustrations, not as promises that
> each is packaged.

Everything routes through two tools — `truffle` finds the instance, `spawn` launches it with a
TTL and tears it down. Pick the shape your job takes, copy that shape's template.

---

## The one rule that bites everyone first

`--on-complete terminate` acts on a **completion sentinel**, not on your command exiting. If the
command finishes but the sentinel was never written, the box sits idle until its TTL. So every
headless recipe ends with:

```sh
--command "…your work… && touch /tmp/SPAWN_COMPLETE" --on-complete terminate
```

`touch /tmp/SPAWN_COMPLETE` and `spored complete` are equivalent. On an MPI cluster, write it
**only from rank 0** (see Shape C).

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

For exploratory work where you SSH in and drive it by hand. Idle timeout stops the box when you
walk away; TTL is the backstop.

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

The workhorse for a single simulation or a single sample, and the shape almost every shipped
recipe takes. The command runs, the sentinel fires, the box dies. Push results to S3 *before*
the sentinel.

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

**Scratch-heavy codes** (Gaussian, ORCA, cryoSPARC steps) want fast local NVMe. Pick an instance
with instance storage (the `d` suffix — `r8id`, `c8id`) and point the code's scratch dir at the
ephemeral mount rather than EBS.

---

## Shape C — Multi-node tightly-coupled MPI

For codes that communicate across nodes every timestep. `--mpi` wires up passwordless SSH, the
hostfile, and OpenMPI; `--efa` adds the low-latency fabric. Launch is all-or-nothing — you never
pay for a half-formed cluster.

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

The rank-0 guard on the sentinel is mandatory — the command runs on every node, but only rank 0
should signal completion.

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

Ephemeral FSx is reaped with the cluster. AZ fallback moves the whole cluster as a unit if the
primary AZ is out of capacity.

---

## Shape D — Fan-out one dataset across N nodes

A fixed count of identical nodes, each taking a shard by index. Best when one big input splits
cleanly (a BLAST query file, a BAM to region-call, a batch of samples). See [job-arrays](job-arrays.md).

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

Each node sees `JOB_ARRAY_INDEX` (0…N-1) and `JOB_ARRAY_SIZE`. Terminate the whole set with
`spawn terminate --job-array-name blast-fanout`.

---

## Shape E — Parameter sweep

One instance per parameter combination, each with its own TTL. This is docking libraries,
screening campaigns, bootstrap replicates — and price-performance benchmarks. Preview cost with
`--estimate-only` first.

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

`spawn` doesn't expand a grid for you — generate the `params` list with a few lines of Python
(`itertools.product`) and write the YAML. An entry can override `instance_type`, so the **same
workload across c8i / c8a / c8g / g6e** in one sweep is the natural price-perf benchmark; spawn
picks the right AMI per family.

---

## Shape F — Workflow engine

If the pipeline is already written in Nextflow/Snakemake, don't rewrite it — use the adapter so
each process/rule lands on its own spawn-managed instance.

```sh
# Nextflow with the nf-spawn executor
nextflow run main.nf -profile spore
```

Individually the tasks look like Shape D, but they aggregate into serious consumption, and the
bottleneck is usually the shared filesystem and task granularity, not the instance type.
[nf-spawn](../recipes/nf-spawn/README.md) is the shipped example — a Nextflow DAG where each
process lands on its own ephemeral instance and data moves between steps through an S3 work dir.

---

## Shape G — Wait for scarce GPU capacity

For 8×H100-class jobs where the constraint is *getting* the GPUs. `truffle` confirms quota,
`lagotto` waits for capacity, or you reserve a Capacity Block up front. GPU codes are Round Two
here (the only Graviton GPU is too small to be representative), so this shape is forward-looking.

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
