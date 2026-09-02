# BUILD BRIEF — Cost-to-Result Benchmark Harness for spore.host

**Audience:** Claude Code. This is a build-and-test handoff, not documentation.
**Input artifact:** `spore-host-scientific-codes-cookbook.md` (the catalog of ~50
codes, their execution shapes, and instance families). This brief turns that
catalog from *asserted* into *measured*.

---

## North star

Every deliverable here serves one reframe: **the unit of cloud cost is the
result, not the node-hour.** A node-hour comparison makes on-prem look free
because its capital, power, staffing, queue wait, and balked/stranded demand are
sunk and invisible. A *cost-per-result* comparison makes the real number legible
— and once it's legible, it's optimizable.

The job is to produce, for representative workloads, a defensible number of the
form **"$X per result on instance Y, purchase mode Z"** — reproducibly, so the
number survives being re-run next month on a different machine.

Four levers, one argument:

| Lever | What it pins / buys | Why the result-cost improves |
|-------|--------------------|------------------------------|
| Containers | the environment | the result is trustworthy — same libs, same math |
| Version pinning | the code + inputs | the number is comparable across time and machines |
| Graviton | better $/perf | lower $/result even when it's not the fastest |
| Spot | cheap interruption | lower $/result on anything checkpointable or re-runnable |

**Do not fabricate benchmark numbers.** Every cost figure in any generated table
must come from an actual run recorded in a manifest (below). Placeholder digests
and TODOs are fine; invented ns/day or $/result values are not.

---

## What you are building

A repo that, for each reference workload:

1. runs it **inside a pinned container** via spore.host,
2. across a **matrix of {Graviton, Intel, AMD, GPU} × {Spot, On-Demand}**,
3. emits a **run manifest** capturing everything needed to reproduce the number,
4. computes **cost-per-result** using the workload's defined result unit,
5. verifies the result is **reproducible** (same inputs + same pinned image →
   same result hash on two independent runs),
6. and generates a **results table** that supersedes the hand-asserted instance
   families in the cookbook.

The harness IS the product. The cookbook's assertions become the harness's
test oracle: where a measured number contradicts a cookbook claim, the measured
number wins and the cookbook row is updated with a citation to the manifest.

---

## Conventions you must follow

### Container pinning — digest, never tag

A tag is mutable; a digest is not. Resolve every image to its digest at
first use and record it. The tag stays in the manifest as the human-readable
label; the digest is the lock.

```sh
# Resolve and record — do this once per image, commit the result
docker buildx imagetools inspect nvcr.io/hpc/gromacs:2024.3 \
  --format '{{.Manifest.Digest}}'
# → sha256:...   ← this is what runs, not the tag
```

For the HPC-native path (rootless, MPI-friendly, plays with FSx), build an
**Apptainer `.sif`** once and store it in S3 — the `.sif` is itself
content-addressed, so the S3 object is the pinned artifact:

```sh
apptainer build gromacs-2024.3.sif docker://nvcr.io/hpc/gromacs@sha256:...
aws s3 cp gromacs-2024.3.sif s3://spore-bench/images/
```

**Arch resolution is a first-class test, and it has a defined order.** The naive
version ("resolve arm64 digest, mark unavailable if missing") throws away the
two cases that matter most: images that *lie* about arm64, and images we've
already rebuilt. Graviton rows resolve their image in this order:

1. **Upstream native arm64**, if it exists AND passes the arm64 correctness
   check below. Record `source: upstream`.
2. **aarch.bio / aarch.science**, our own native, signed, verified rebuilds —
   the primary arm64 source for the bioinformatics and conda-forge-science
   classes, not a fallback. Record `source: aarchbio` / `source: aarchsci`.
3. **`arm64: image-unavailable`** only if neither exists — a real, reportable
   finding that belongs upstream (aarch.bio surfaces these in `GAPS.md` /
   issues). "What Graviton can't do yet" is part of the story.

**The misreport case — why "solve ≠ run".** A multi-arch manifest can advertise
an arm64 variant that fails to start (`exec format error`) or, worse, starts and
computes *subtly wrong* results — the QEMU-emulation trap on x86 CI, or a broken
wheel/ABI mismatch. A wrong arm64 result silently corrupts every `$/result`
number downstream. So a Graviton row is not accepted on the strength of a digest
resolving; the image must pass a **functional check inside the arm64 container**
before it's allowed to produce a benchmark number. This is exactly the model
aarch.science already enforces (every image imports its headline packages and
does real work before it earns a tag; the smoke test ships *inside* the image,
re-runnable on the pulled artifact) and aarch.bio audits (native rebuild vs
emulated, measured). Reuse that oracle rather than reinventing it: if the
upstream arm64 image fails the check, drop to the aarch.* rebuild.

**Verify signatures as part of the pin.** aarch.bio and aarch.science images are
cosign keyless-signed and logged to Rekor. Resolving one means verifying it, and
recording the result in the manifest:

```sh
cosign verify quay.io/aarchbio/samtools:1.21--h50ea8bc_0 \
  --certificate-identity-regexp 'github.com/playgroundlogic/aarchbio' \
  --certificate-oidc-issuer https://token.actions.githubusercontent.com
```

### Image sources per code class

For each class the **amd64** source is upstream; the **arm64** source follows the
order above. Two arm64 sources are our own registries, native-built and verified.

- **Bioinformatics** (BWA, samtools, GATK, STAR, salmon, BLAST, HMMER, kraken2, …):
  amd64 from BioContainers (`quay.io/biocontainers/<tool>:<tag>`); arm64 from
  **aarch.bio** (`quay.io/aarchbio/<tool>:<same-tag>`), 500+ native rebuilds of
  the same bioconda recipe. Essentially every container a real nf-core pipeline
  pulls is amd64-only upstream, so for this class aarch.bio *is* the arm64
  source. Point the pipeline registry straight at it (`registry =
  'quay.io/aarchbio'`).
- **conda-forge scientific stack** (OpenMM, PySCF, RDKit, ASE, MDAnalysis, MDTraj;
  GPAW and the DFT / geospatial / climate stacks): arm64 from **aarch.science**
  (`quay.io/aarchsci/{comp-chem,dft,geospatial,climate,pointcloud,…}`) — curated,
  version-pinned environments verified to import and run. The `dft` env is
  MPI-parallel and self-checks (serial vs `mpiexec -n 2` energies must agree), so
  **open DFT on Graviton has a native, verified path** (Shape C).
- **GPU MD / cryo-EM / ML** (GROMACS, NAMD, LAMMPS, RELION, PyTorch, TensorFlow):
  NGC — `nvcr.io/hpc/*`, `nvcr.io/nvidia/*`. GPU-optimized; resolve arm64 per
  image (some NGC HPC images ship arm64, many don't yet).
- **License-required** (VASP, Gaussian, ANSYS, Abaqus, COMSOL, LS-DYNA,
  Star-CCM+, Q-Chem, Schrödinger, MATLAB): image expects the user's own license
  — **out of scope for v1**, see Guardrails.

### Gap reporting — the harness feeds aarch.bio / aarch.science

The harness detects arm64 gaps as a side effect of running. Those detections are
exactly the work queue for the two container projects it depends on, so it files
them rather than just logging locally. This is the flywheel: the benchmark's
arm64 gaps become the projects' backlog, and closing them makes the next
benchmark run cheaper.

Two detection points, both already in the resolution path above:

- **No arm64 image anywhere (resolution case 3).** File a container/env request:
  `playgroundlogic/aarchbio` (a bioconda-layer tool) via the `request-container`
  issue template, or `playgroundlogic/aarchsci` (a conda-forge env/package).
- **Upstream arm64 image exists but fails the functional check** (exec-format
  error, import failure, or — the dangerous one — runs but returns a wrong
  result). This is a distinct, higher-value finding: the image *claims* arm64 and
  lies. File it so aarch.* can prioritize a verified rebuild and, where the root
  cause is upstream, so it can be pushed there.

Finding schema (one structured record per gap, emitted from the run):

```json
{
  "tool": "someTool", "version": "1.4.2", "arch": "arm64",
  "upstream_ref": "quay.io/biocontainers/sometool:1.4.2--h9f5acd7_0",
  "failure_mode": "wrong-result",     // missing | exec-format | import-fail | wrong-result | emulated-only
  "evidence_manifest": "s3://spore-bench/manifests/...",
  "dedup_key": "sometool@1.4.2:wrong-result"
}
```

Routing and hygiene:

- **Route by layer.** Bioconda / BioContainers tool → aarchbio. conda-forge
  scientific env or package → aarchsci. Anything outside both (distro base,
  framework like TensorFlow, vendor ML image) is **neither project's scope** —
  aarch.bio deliberately rebuilds only the bioconda layer — so log it locally and,
  if it blocks a row, note it as an upstream/vendor issue, not an aarch.* gap.
- **Dedup before filing.** Check open issues and `GAPS.md` against `dedup_key`;
  one issue per (tool, version, failure_mode). A nightly re-run must not reopen
  what's already tracked.
- **Attach evidence, not assertion.** Every filed gap links the manifest that
  proves it — same no-fabrication rule as the cost numbers.

### Run manifest — the reproducibility AND cost record in one file

Every run writes exactly this, keyed by a content hash of the workload spec.
This single object is both the scientific provenance record and the
cost-to-result datum.

```json
{
  "workload": "gromacs-adh-cubic",
  "result_unit": "ns_simulated",
  "result_quantity": 10.0,
  "image": {
    "ref": "nvcr.io/hpc/gromacs:2024.3",
    "digest": "sha256:...",
    "arch": "amd64",
    "source": "upstream",
    "arm64_check": "n/a",
    "signature": "n/a"
  },
  "code_version": "GROMACS 2024.3",
  "inputs_hash": "sha256:...",
  "instance_type": "c8g.16xlarge",
  "purchase": "spot",
  "region": "us-east-1",
  "az": "us-east-1b",
  "wall_seconds": 842.5,
  "instance_hourly_usd": 0.71,
  "spot_interruptions": 0,
  "cost_usd": 0.166,
  "cost_per_result_usd": 0.0166,
  "result_hash": "sha256:...",
  "spore_run_id": "...",
  "timestamp": "2026-08-18T00:00:00Z"
}
```

`cost_usd` includes any re-run overhead from Spot interruptions — that's the
honest Spot number, and the reason `cost_per_result` (not hourly rate) is the
comparison axis. `result_hash` is what makes reproducibility testable.

### Result units — define the denominator per shape

"$/result" is meaningless without a fixed result unit. Use these; each is a
fixed quantum of scientific output, held constant across the instance matrix:

| Code class | Result unit | Held fixed |
|-----------|-------------|-----------|
| MD (GROMACS/NAMD/LAMMPS/AMBER/OpenMM) | ns simulated | system + integrator + step count |
| DFT/QC (VASP/QE/CP2K/ORCA/…) | one SCF-converged calculation | system + convergence criteria |
| CFD (OpenFOAM/…) | one case converged to fixed residual | mesh + solver + residual target |
| Genomics (BWA/GATK/STAR/…) | one sample through the pipeline | reference + sample + params |
| Structure (AlphaFold/RELION) | one predicted/reconstructed structure | sequence/particle set + params |
| ML training | one epoch (or to fixed loss) | model + dataset + batch/precision |

---

## Reference benchmark, fully worked: GROMADS across the matrix

This is the pattern to replicate for every other workload. It uses spore.host's
heterogeneous sweep — one workload spec, per-entry instance type, so the whole
Graviton-vs-x86-vs-GPU comparison falls out of one launch.

```yaml
# bench/gromacs-adh-cubic/sweep.yaml
# Result unit: 10 ns of the ADH-in-water benchmark (system + steps fixed below).
defaults:
  ttl: 3h
  on_complete: terminate
  spot: true                       # Spot column; see od.yaml for the On-Demand pass
  command: >
    aws s3 cp s3://spore-bench/inputs/adh_cubic.tpr /scratch/bench.tpr &&
    apptainer exec --nv /images/gromacs-2024.3.sif
      gmx mdrun -s /scratch/bench.tpr -nsteps 5000000 -resethway
      -noconfout -g /scratch/md.log &&
    python3 /harness/emit_manifest.py
      --workload gromacs-adh-cubic --result-unit ns_simulated --result 10.0
      --log /scratch/md.log --out s3://spore-bench/manifests/ &&
    touch /tmp/SPAWN_COMPLETE

params:
  - instance_type: c8g.16xlarge    # Graviton4  (arm64 image required)
  - instance_type: c8i.16xlarge    # Intel
  - instance_type: c8a.16xlarge    # AMD
  - instance_type: g6e.2xlarge     # NVIDIA L40S — 1 GPU
  - instance_type: g6e.2xlarge     # same GPU box, MIG-partitioned (see note)
    ami: <mig-configured-ami>
```

```sh
# Cost preview before spending anything
spawn launch gromacs-bench --param-file bench/gromacs-adh-cubic/sweep.yaml \
  --estimate-only

# Spot pass, cost-capped
spawn launch gromacs-bench --param-file bench/gromacs-adh-cubic/sweep.yaml \
  --max-concurrent 5 --budget 25
spawn sweep collect <sweep-id> --output bench/gromacs-adh-cubic/spot.json

# On-Demand pass for the same matrix (flip spot:false), for the Spot-vs-OD delta
```

The two GPU rows demonstrate **MIG as a $/result lever**: a code that doesn't
saturate a full GPU (AMBER `pmemd.cuda`, OpenMM, AlphaFold inference) gets a
better $/result on a MIG slice than on a whole GPU. Measure it, don't assume it.

For **MPI reference cases** (Shape C — WRF, CESM, OpenFOAM), the same manifest
applies; the container runs under host MPI (Apptainer hybrid model), `--efa`
on, and the manifest emits from **rank 0 only**:

```sh
--command "mpirun -n 512 apptainer exec /images/wrf.sif wrf.exe && \
  if [ \$OMPI_COMM_WORLD_RANK -eq 0 ]; then python3 /harness/emit_manifest.py …; \
  fi && [ \$OMPI_COMM_WORLD_RANK -eq 0 ] && touch /tmp/SPAWN_COMPLETE"
```

---

## Repo structure

```
cost-to-result/
├── BUILD.md                       # this brief
├── harness/
│   ├── emit_manifest.py           # parse log → result_quantity + cost → manifest
│   ├── cost.py                    # wall_seconds × price (spot or OD) → cost_usd
│   ├── resolve_digest.sh          # tag → digest, per arch; records unavailability
│   ├── verify_reproducible.py     # two manifests, same inputs → assert result_hash eq
│   └── results_table.py           # manifests/ → generated cookbook tables
├── images/
│   └── pins.lock                  # tag → {amd64 digest, arm64 digest|unavailable}
├── bench/
│   ├── gromacs-adh-cubic/         # worked reference (above)
│   ├── star-rnaseq/               # Shape B, single sample, genomics
│   ├── blast-fanout/              # Shape D
│   ├── vina-screen/               # Shape E, Spot showcase
│   └── wrf-conus/                 # Shape C, MPI + EFA
├── manifests/                     # (S3-backed) every run's record
└── tables/                        # generated $/result tables, checked in
```

---

## Milestones (work through in order; each is independently testable)

**M0 — Scaffold + one pin.** Repo skeleton, `resolve_digest.sh` working,
`pins.lock` with one real image resolved for both arches (or arm64 marked
unavailable). Gate: `pins.lock` committed with at least one real digest.

**M1 — One workload, one instance, end to end.** GROMACS on a single c8g Spot
box, containerized, manifest emitted to S3 with a real `cost_per_result_usd`.
Gate: manifest exists, sentinel fired, box self-terminated (verify via
`spawn list --state all`).

**M2 — The matrix for one workload.** GROMACS across all sweep rows, Spot pass +
On-Demand pass. Gate: one manifest per (instance_type × purchase), no orphaned
instances after `spawn sweep status` shows complete.

**M3 — Reproducibility gate.** Re-run one row; `verify_reproducible.py` asserts
matching `result_hash`. Gate: two manifests, same inputs_hash + image digest,
equal result_hash. (For nondeterministic codes, define a tolerance oracle — e.g.
energy within convergence criteria — rather than bit-exact hash.)

**M4 — Breadth.** Replicate for STAR (B), BLAST (D), Vina (E), WRF (C). Gate:
every shape has at least one workload with a full matrix of manifests.

**M5 — Generated tables + gap feed.** `results_table.py` renders `tables/` from
manifests and diffs against the cookbook's asserted families. The same pass emits
the arm64 gap findings (above) as deduped issues against aarch.bio / aarch.science.
Gate: a table where every $/result cell traces to a manifest; a diff report of
asserted-vs-measured; and a gap report listing what was filed (or matched an
existing issue).

### Phasing

The milestones map to the phases you'll talk about externally:

- **Phase 0** = M0–M1: prove one honest `$/result` end to end, one code, one box.
- **Phase 1** = M2–M5: the instance × purchase matrix, reproducibility gate,
  breadth across shapes, generated tables, and the gap feed.
- **Phase 2+** = the roadmap section at the end (checkpoint/restart). Deliberately
  **not** in phase 0/1 — don't let it pull scope forward.

---

## Acceptance gates (self-verifiable — no human in the loop)

1. **Cleanup:** after any benchmark, `spawn list --state all` shows every
   instance `terminated`. No box outlives its result.
2. **Sentinel discipline:** every headless command ends in a sentinel; MPI
   emits it from rank 0 only. A run that hits TTL instead of completing is a
   failure, not a slow success.
3. **Cost present:** no manifest may have a null `cost_per_result_usd`.
4. **Digest present:** no run uses a `:tag` at execution time; the manifest
   records a `sha256:` digest.
5. **Reproducible:** M3 oracle passes for at least one workload per shape.
6. **Spot honesty:** `cost_usd` on Spot rows includes re-run time from any
   interruptions; `spot_interruptions` is recorded even when zero.
7. **Budgeted:** every sweep launches with `--budget` and `--estimate-only` was
   run first. No unbounded spend.
8. **arm64 verified, not assumed:** every Graviton row's manifest records a
   `source`, an `arm64_check` result (the in-container functional check passed),
   and a `signature` state for aarch.* images. A Graviton `$/result` produced by
   an image that resolved but never passed the functional check is a bug — it may
   be a silently-wrong emulated result, which is worse than a missing row.

---

## Guardrails

- **License-server codes are out of scope for v1** (VASP, Gaussian, ANSYS,
  Abaqus, COMSOL, LS-DYNA, Star-CCM+, Q-Chem, Schrödinger, MATLAB). They need
  FlexLM/vendor plumbing that belongs in its own brief. Use only open or
  freely-redistributable images for the harness. Note this is a *licensing* line,
  not an arm64 line: the open comp-chem and DFT codes (OpenMM, PySCF, GPAW, ASE,
  RDKit) have native, verified arm64 images via aarch.science and are fully in
  scope — including on Graviton.
- **Spot only on re-runnable work.** Shapes B/D/E and checkpointing C are safe.
  A long non-checkpointed C run on Spot can lose hours to an interruption —
  measure the interruption cost honestly rather than hiding it, or run that row
  On-Demand and label it.
- **Every sweep cost-capped.** `--budget` and `--max-concurrent` on every launch;
  `spawn alerts create <sweep-id> --cost-threshold …` as the backstop.
- **No fabricated numbers.** Restated because it's the one that matters: a table
  cell without a backing manifest is a bug, not a placeholder.
- **Region/AZ capacity is not quota.** Confirm `truffle quotas` AND expect
  `truffle az` gaps; let AZ fallback (Shape C) or lagotto (Shape G) handle
  scarce GPU rather than failing the run.

---

## What "showing what Graviton and Spot can do" produces

Not a slogan — a table. When M5 lands, each workload has a row like:

```
gromacs-adh-cubic (10 ns)
  c8i (Intel,  OD )   $ ____ /ns      1.00×  (baseline)
  c8a (AMD,    OD )   $ ____ /ns      ____×
  c8g (Graviton, OD)  $ ____ /ns      ____×   ← $/perf story
  c8g (Graviton, Spot)$ ____ /ns      ____×   ← + Spot story
  g6e (GPU,    Spot)  $ ____ /ns      ____×
  g6e (GPU+MIG,Spot)  $ ____ /ns      ____×   ← MIG $/result lever
```

The blanks are filled by runs, not by me. That table — the same result, priced
honestly across the levers — is the reframe made concrete: cloud cost stated in
the currency that matters, the result.

This isn't starting from zero. aarch.science already produced one such row for
CPU geo-prep: Graviton c7g vs Intel c7i, **1.52× faster at 1.23× lower cost/hr →
1.87× better price/performance, with bit-identical output**. That single measured
result is the whole thesis in miniature — same answer, cheaper, and *provably*
the same answer (the reproducibility oracle and the cost win in one number). The
harness's job is to generalize that row from one geo workload to the whole
catalog, and to add the Spot and GPU-partitioning columns to each. The bit-
identical result across architectures is also the existence proof for the M3
reproducibility gate: cross-arch result equality is achievable, not aspirational.

---

## Roadmap: checkpoint/restart (phase 2+, not phase 0/1)

Captured here so it isn't lost, and fenced off so it doesn't pull scope into the
phases above. Its strategic weight is real: **checkpoint/restart is the lever
that enlarges the Spot-safe set.** Today the brief runs long, non-checkpointed
tightly-coupled jobs On-Demand because a Spot reclaim wastes the whole run. Make
those jobs resumable from S3 and they move into the Spot column — which is
exactly where the largest untapped `$/result` win sits. So this is deferred, not
peripheral.

One distinction to keep straight, because they live at different layers:

- **Sweep-level resume already exists.** `spawn sweep resume` checkpoints *which
  entries completed* and continues the rest. That covers Shapes D/E — a reclaimed
  shard is just re-run, cheaply. Nothing new needed there.
- **Per-run scientific checkpoint is the new thing.** Resuming *one long job*
  mid-flight (a 12-hour MD trajectory, a multi-day climate run) after its
  instance is reclaimed. That's Shapes B-GPU and C, and it's what this roadmap is
  about.

### Tier 1 — leverage native checkpointing, to S3

Most of the heavy codes already checkpoint natively; the work is wiring that to
spore.host's lifecycle rather than inventing anything:

| Code | Native mechanism |
|------|-----------------|
| GROMACS | `-cpo state.cpt` / resume `-cpi`, interval `-cpt <min>` |
| LAMMPS | `restart` → `read_restart` |
| NAMD | `restartfreq` → resume from restart files |
| CP2K | `&EXT_RESTART` / `.restart` |
| WRF / CESM | `restart_interval`, built-in restart cadence |
| AMBER | `-r` restart → resume from `rst7` |
| Quantum ESPRESSO | `restart_mode='restart'` |
| PyTorch / TF | checkpoint callback → resume from checkpoint dir (already S3-friendly) |
| RELION | continue from `_optimiser.star` |
| AlphaFold | cache the expensive MSA stage to S3 so a restart skips it |

The spore.host pieces to connect are already present: **pre-stop hooks** (run
before any lifecycle-triggered stop/termination — "save checkpoints, sync output
to S3"), the **Spot interruption webhook**, and the ~2-minute EC2 interruption
notice. Design sketch: the app checkpoints on an interval to local NVMe; a
sidecar syncs checkpoints to S3 asynchronously; the pre-stop hook flushes the
latest checkpoint to S3 on the interruption notice; restart launches a fresh
instance (via lagotto re-acquire or sweep resume) and the app resumes from the
S3 checkpoint. The manifest gains `checkpoint_interval`, `checkpoint_s3_uri`, and
`restart_count`, so a Spot run that survived three reclaims still reports an
honest `$/result` including the redone work.

### Tier 2 — solve it for codes without native checkpoint (research)

The harder, genuinely open part: transparent, application-agnostic
checkpoint/restart for codes that have none. The candidates are system-level —
**CRIU** (process checkpoint) and **DMTCP** (handles multithreaded and MPI/
distributed state). The hard parts are the usual ones: open files, network and
MPI connection state, and above all **GPU memory state**, which classic CRIU
can't capture. Worth evaluating here is **NVIDIA `cuda-checkpoint`**, which
checkpoints/restores CUDA state and is designed to work alongside CRIU — it's
what makes transparent GPU checkpointing newly plausible rather than impossible;
its maturity is the thing to assess before committing.

This tier is a **spore.host capability question** — a generic checkpoint plugin
coordinating CRIU/DMTCP/cuda-checkpoint with the existing pre-stop hook and
interruption webhook — not a per-code harness item. It's a design consideration
to open later, not a build task now. The right phase-2 entry point is Tier 1 (the
native codes, which are most of the compute), with Tier 2 scoped as a follow-on
investigation once the S3 checkpoint plumbing exists.
