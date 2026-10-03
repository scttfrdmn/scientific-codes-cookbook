# CLAUDE.md — scientific-codes-cookbook

## What this is

A cookbook: working, runnable recipes for the ~50 common research-computing codes
on spore.host. **Round One builds working examples** — does each code run cleanly
on a Graviton4 box, producing real output. Not benchmarks. Cost measurement is a
later phase and is out of scope now.

**Every recipe runs the application on AWS, through the spore.host tools.** That is
the subject and the whole point: `truffle` sizes the box, `spawn` runs the pinned
container on it under a TTL and a cost cap, `lagotto` handles capacity, and the
instance self-terminates. Graviton4 (`c8g`/`m8g`/`r8g`) is the target now; other
instance types — x86, GPU — come later. A recipe that has not run on AWS is not a
recipe yet, whatever it does anywhere else.

The recipe is the product. Read CHARTER.md for the why. State and progress live on
the GitHub project board — this file holds only standing rules.

## The one test for everything

**Does this move a code toward a working example?** Apply it to rules, scaffolding,
and your own process alike. A pinned digest and a smoke check pass (they stop real
harm). A gate framework with its own test suite, a provenance object, a findings
log file — for a one-container run, those don't; they're drift. If what you're
building is bigger than the recipe it serves, stop.

## What a working example is

One code, one Graviton4 8g box (**c8g** compute-bound / **m8g** balanced / **r8g**
memory-bound, EFA-capable 8g for multi-node — pick by fit), pinned arm64 container,
runs to completion, self-terminates, and a **smoke check** confirms the output is
real: it exists, is non-empty, is the right shape. That's done. Don't gold-plate it.

GPU-bound codes are **Round Two** (an x86 pass) — the only Graviton GPU is g7g,
too small to be representative. Don't force them onto Graviton now.

## The shape of a recipe: one tool per task, S3 between them

**aarch.* ships one tool per image, deliberately, and that is not changing.** The
registries' value is that every image traces to a single signed conda recipe;
mulled multi-tool images mean resolving a joint environment, which breaks exactly
that provenance. Same trust reason aarch.* doesn't compile from source. So:

- **Don't expect pipes.** `bwa mem | samtools sort` is not expressible —
  `spec.container` takes one image. A multi-tool recipe is a **sequence of
  single-tool tasks**, and intermediate data round-trips through S3. Pins verified
  at each hop. This is the model, not a workaround; don't describe it as one.
- **Recipes stay resumable at task boundaries.** Every task reads all its inputs
  from S3 and writes its outputs to S3, so a failed task 2 is rerun alone. That
  falls out of the shape for free — just don't design it out by having a task
  depend on a previous task's local disk. **Build no machinery for it.**
- **Every staged path is flat in `/tmp`, and directory intermediates travel as a
  tar.** Host `/tmp` is `1777` and the one mount writable whatever user the image
  runs as. `spawn` advertises directory staging (a manifest source ending in `/`
  gets `--recursive`) but a **directory output cannot work on the container path**:
  output parents are never `mkdir`-ed, so dockerd creates them as root and the
  container can't write there (spawn#564). An index is a directory, so `tar -cf` it
  to one flat file, `rm -rf` the dir to stay inside the ~6.1 GiB budget, and untar
  in the consuming task. (Unlaunchable recipe, or worse, a green check over nothing.)
- **Never `rm` a staged input; only files the container itself created.** Host `/tmp`
  is sticky (`1777`) and stage-in runs as the **instance** user while the container
  runs as the **image's** user, so the container gets `EPERM` unlinking a staged file
  it does not own — and `rm -f` does **not** suppress
  `EPERM`, only `ENOENT`, so it returns 1 and kills the task under `set -e`. Measured:
  this killed both dependent tasks in the five-recipe batch, at `rm` of the index tar.
  Budget for holding the staged copy instead. `rm -rf` on a directory the task built
  is fine. (A task that dies after its inputs verify, for a reason unrelated to the
  science.)
- **The local machine is a scratch pad, not a target. The AWS run is the only verdict.**
  Local Docker is for fast iteration on shell syntax and awk logic, and for nothing else.
  **Neither a local pass nor a local failure is evidence about the instance**, so don't
  treat a green local run as a gate, and don't spend time diagnosing a local-only failure
  or write it up as a finding — push it to the box and read the answer there. Measured,
  three ways, all on macOS Docker: bind mounts don't enforce sticky-bit ownership, so the
  `rm`/`EPERM` bug above survived seven green dry runs; a `--memory 3g` cgroup limit
  doesn't change `/proc/meminfo`, so manta's pyflow believed it had the host's RAM and
  only failed on a real 4 GiB box; and racon `SIGILL`s on Apple Silicon, which lacks SVE,
  while running cleanly on Graviton4 — a *loud local failure pointing away from a tool
  that works*. The first is false confidence, the third is false alarm, and both waste the
  same thing. When the question is about the *platform* — permissions, memory, CPU
  features, anything the host decides — the local answer is not an answer.
  (Time spent debugging a machine nobody ships on.)
- **Boot dominates; say so in the README.** First recipe: 46.6s of `bwa index` and
  32s of `bwa mem` inside 6m40s billed. Boot, Docker install and image pull are
  most of every task, and the ratio worsens with each task added. Irrelevant to
  Round One (working examples, not benchmarks) but it means **recipe timings are
  not compute cost** — note that in the README so no one reads them as such, and
  leave the rest to the deferred measurement phase.

## Rules (each earns its place by preventing a real harm)

- **Sentinel always fires; the box self-terminates.** A run that hits TTL instead
  of completing is a failure. (Stranded spend.)
- **Pin the image by digest, not tag; cosign-verify aarch.* images.** A `:tag` at
  run time is a bug. (Unreproducible run.)
- **Smoke-check every run.** Runs-to-exit-0 with empty or garbage output is a
  failure, not a success. This is the correctness bar — the minimum, not ceremony.
  Don't report a run worked without checking its output. (Silent failure.)
- **Reach for a conservation identity or a completion sentinel before a threshold.**
  The assertions that actually earned their place across the seven genomics recipes are
  the ones the tool must satisfy to be correct at all, not bands on observed values:
  salmon's TPM sum is exactly `1000000` and its `sum(NumReads)` equals its own mapped
  count, so a `quant.sf` truncated on a zero-count tail fails even though the row count
  passes; BLAST's queries are the first 20 records **of its own database**, so each must
  find itself full-length at 100.000% identity and nothing can outscore it — algorithmic,
  so no band at all; `hmmsearch` writes `[ok]` only on clean completion, catching a search
  killed part-way whose partial `tblout` would still land inside any hit-count band; a BAM
  starts `1f8b0804`. These cost nothing, need no headroom, and cannot go flaky. A band on a
  measured value is the fallback, not the first move — and a *flaky* check is worse than no
  check, because it teaches people to ignore failures: a ceiling 3% above the observed
  value fails on noise, and "the best hit is itself" failed 19-of-20 because BLAST breaks
  score ties arbitrarily. Assert the claim you mean ("nothing beats itself"), not the
  convenient proxy for it. **Two shapes to reach for when no reference value exists:
  an *invariance* and a *convergence rate*, both of which beat any band on a single run.**
  Moran's I has no closed-form value, but it is a ratio of covariance to variance of the
  same centred variable, so `I(y) == I(3.7y+112.5)` is bit-identical — measured `0.00e+00`,
  and an implementation that normalised wrongly could not satisfy it. And a discretised
  method has a *theoretical order*: ParaView's marching-cubes area error over 65³→513³ falls
  `4.01, 4.01, 4.00` per doubling, which is O(h²) — asserting the ratio catches a
  correct-but-first-order implementation that would sit comfortably inside any
  single-resolution tolerance you picked. A ladder costs one extra rung and tests the
  *method*; a band tests the answer. (An assertion that fails for reasons unrelated to
  correctness, or waves through garbage.)
- **Where two codes solve the same problem, run them on the same bytes and compare —
  cross-validation beats any identity.** RAxML-NG and IQ-TREE, same alignment, same
  LG+G4, independently reached `-52706.731409` and `-52706.731`: two unrelated codebases
  and two different search heuristics agreeing to ~1 part in 1e8. No single-tool
  assertion can reach that, because it confirms the *numerics*, not just internal
  consistency — and each recipe becomes the other's check for free. It cost nothing but
  **not** copying the input under a second prefix; a second copy of a derived input is a
  second conversion to keep true, and the agreement only means something on identical
  bytes. So: when the catalog holds a second code for the same job — aligners, DFT
  codes, MD force fields — point it at the first one's staged input and assert the
  agreement, in both READMEs. RELION is the same move against a *published* result
  rather than a sibling recipe: the RODA archive holds the depositors' own output, so
  all 221 FSC shells could be compared at `max|diff| = 0`. Prefer either to a band.
  **A cross-code check's tolerance is set by the shared problem's precision, not by how
  closely the codes happen to agree** — and stating *why* is what separates it from a
  fudge. RAxML-NG/IQ-TREE can assert 1e-8 because the ML optimum is defined to that
  precision. PySCF and Psi4 on H2 RHF/STO-3G, **both run with exact integrals**, agree to
  3e-7 Ha — and getting there required first matching the *method*: Psi4 defaults to density
  fitting (DF), and comparing that DF energy against PySCF's exact one made two correct codes
  look 2.4e-5 Ha apart, a method difference masquerading as a basis limit (the original recipe
  blamed "unstandardized STO-3G contraction coefficients" — wrong; measured DF−PK = 2.401e-5).
  Setting `SCF_TYPE PK` makes it like-with-like; then the check asserts <1e-5 (tight enough to
  be a real cross-validation, loose enough to survive SCF-convergence noise). The integral
  treatment sets the tolerance, not a basis nuance — and this is the same "match the modes"
  discipline as the aligners, one env over.
  (A check that only proves a tool is self-consistent, when a stronger one was free; or a
  cross-code tolerance picked to pass rather than justified by the problem.)
- **A cross-code check must compare like with like — before asserting agreement, verify the
  metric measures agreement and not a difference in method.** A naive metric failing is not
  evidence of a bug in either tool; it is evidence the comparison was wrong, and a green
  check on the wrong metric is worse than none. The three failure modes, all caught in local
  validation in the first aarch.bio batch: **different models make raw values incomparable —
  use rank, because rank is the stable choice** (kallisto↔salmon TPM, measured at two depths on one
  dataset: Spearman **0.912 at 200k fragments and 0.908 at 15.8M**, moving 0.004 across a 79× depth
  change, while raw log-TPM correlation on the *same* data spans **0.787–0.966** depending on depth
  and on whether transcripts only one tool detected are included — so a raw-value number states a
  property of your comparison, not of the tools. An earlier recorded 0.61 for this pair does not
  reproduce under any of those four variants and should not be requoted); **repeat-heavy references make all-mapped concordance meaningless — restrict to
  confident calls** (minimap2↔bwa: naive all-mapped 0.43 on the chr20-only subsample where
  two correct aligners break repeat ties differently, 0.9921 gated on MAPQ≥30 and ≤5 bp —
  "agree where both are sure"); **different alignment modes reject different reads — match the
  modes** (bowtie2↔bwa: default end-to-end vs bwa's soft-clipping gave 82%, `--local` on the
  mapped set gave 0.9462). This is the sibling of the tolerance rule above — that one justifies
  the *number*, this one justifies the *metric*. Both are the same discipline: assert the claim
  you mean. (A cross-code check that fails, or passes, for a reason unrelated to correctness
  because the metric compared a method difference instead of the science.)
- **A stochastic-search tool needs pinned threads and a fixed seed before an exact identity
  means anything.** Many correct tools don't produce the *same* output run to run: the thread
  count changes the order of updates and therefore which local optimum / assembly the search
  lands on. The tool is right and each result is valid — but an exact assertion on it is flaky
  unless the search is pinned. Bitten in three domains now, so it's a pattern not a coincidence:
  IQ-TREE's ML search (thread count changes the tree, so `-T <n>` fixed not AUTO, plus `-seed`);
  SPAdes/Flye assembly (Flye at `-t 4` gave 2 *or* 3 contigs across runs, `-t 1` is byte-identical
  — and Flye's raw modes OOM where `--nano-hq` fits, a separate measured constraint); and it's
  why the mafft↔muscle RF check is an *observation* not an assertion (RF 26-vs-4 on the same
  alignments was search stochasticity, not signal). So: for any tree-builder, assembler, or
  sampler, pin `-t 1` (or a fixed thread count) **and** a fixed seed, verify byte-identical
  across two local runs, *then* assert the exact number — otherwise assert a band or report it
  as an observation. (An exact identity that's exact one run and different the next: correct
  tool, valid result, flaky check — the subtlest way an assertion goes bad.)
- **A pin swap is a change to the recipe's input, not a mechanical edit — every assertion
  downstream of the changed bytes needs re-derivation or re-verification, a re-run, not a
  hash edit.** An equivalence argument may justify the *sourcing* but never licenses leaving
  the *assertion* unverified — and that gap is not sloppiness, it's a good argument applied
  to the wrong question. Repinning macs2's chr20 subset to a new samtools serialization was
  argued sound because the two BAMs had identical read *counts* — correct for the sourcing,
  and 1390 peaks did survive. But the same reasoning left the 30x reads' assemblers unverified
  after their repin, and count doesn't determine an assembly: megahit shipped `2 contigs /
  400811 bp` as a false green for months (the repinned reads assemble to `1 / 400429`), because
  read-count equivalence answered a question the assertion didn't ask. The proof that reasoning
  can't stand in for a run is the audit itself — an end-to-end pass over three unreconfirmed
  repins found two harmless (spades, macs2) and one not (megahit), and nothing short of running
  could tell which. So on any repin: re-derive or re-run every downstream assertion and record
  the command. A repin to reproducible bytes is a *correction* — but only once a run shows the
  science unchanged. (A verified number silently invalidated by a pin swap nobody re-ran — the
  subtlest false green, and the project paid Graviton time to learn it.)
- **Editing a page to describe a computation is a claim about that computation, and a claim
  needs verification — the pin-swap discipline one layer up.** Two ways it bit a per-recipe
  audit: (1) a *value* change leaves downstream **citations** stale, not just downstream
  assertions. The megahit `-t 1` fix (2 contigs → 1) was reconfirmed on megahit's own page but
  left mash and sourmash citing "megahit (2 contigs)" — a page that quotes another recipe's
  number is asserting it too, so after any value change grep for who *cites* it, not only who
  *asserts* it. But the sharper question is *what depended on the dimension that moved*, not
  *what consumed the value* — the naive version re-runs every consumer. mash and sourmash also
  *consume* megahit's assembly, yet their MinHash distance and Jaccard were unchanged: fragmenting
  an assembly differently moves the contig *count* but not which k-mers are present, and a sketch
  reads k-mer content. So the citation was stale and the assertion was not. Ask which dimension
  changed and trace only its dependents. (2) "Make `## Run it` runnable" is a running task, not a writing task:
  reconstructing a complete invocation from inference put divergences on five of seven edited
  pages (a wrong topology filename, an omitted `periodic=True`, a 4×4×4 supercell where the spec
  runs 2×2×2, a missing `grompp` step) — each looked right, none ran. The fix for "make it
  runnable" is to run it, or copy verbatim what the spec verifiably ran; never to reason about
  what *would* run. (A page confidently describing a computation that never happened that way —
  the false green wearing prose.)
- **When a conda package strips the data a code needs, stage it from the code's own
  version-matched test suite — it usually ships a committed reference alongside, which
  turns "produce a number" into "reproduce a published number" for free.** This is a
  *sourcing* rule that manufactures the identity above, not another kind of check.
  conda-forge SIESTA ships no pseudopotentials, so a naive recipe could only prove the
  binary parses input (aarch.science's own env check is init-only for exactly this
  reason). But `siesta-project/siesta` at the tag matching the image (`5.4.2`) ships
  `Tests/Pseudos/Si.psf` **and** `Tests/01.PseudoPotentials/Reference/psf.out` with
  `Total = -214.377236 eV` — so staging the pinned pseudopotential let the run reproduce
  SIESTA's own committed reference exactly. Vina was the same shape: the `vina` package
  ships no example data, but `AutoDock-Vina` at `v1.2.7` carries the 1iep receptor/ligand
  and a reference docked pose (`-13.234`). The version match is load-bearing (a reference
  from another version is a different number), and staging a pinned file to S3 is **not**
  runtime-fetching — it is allowed where an env's build-time constraints forbid bundling
  data. Lots of scientific packages ship tests with reference outputs that never enter
  the conda build; reach for them before settling for init-only or a bare band.
  (A recipe that asserts nothing physical, when the code's own tests were a pinnable
  reference away.)
- **When a recipe claims MPI, assert the rank count from inside the run.** A serial
  fallback produces the *right physics* and a false claim about the build: conda-forge
  ships nompi builds at higher build numbers than the openmpi ones, so an unpinned solve
  silently hands back a serial binary that, under `mpiexec -n 2`, runs two independent
  rank-0 calculations — both print the same energy, and a naive "parallel == serial"
  check passes vacuously. So read the rank count the tool itself reports and assert it is
  what you launched: LAMMPS `with 2 MPI task(s)`, SIESTA `Running on 2 nodes`, NWChem
  `nproc = 2`, GPAW `gpaw.mpi.world.size == 2` — four recipes now carry this, and GPAW's
  is the sharpest because aarch.science found `dft` was one resolver tie from shipping a
  serial gpaw that would have passed everything else. The serial-vs-2-rank energy
  agreement is the cross-validation; the rank-count assertion is what proves the
  parallelism it's cross-validating actually happened. (A green MPI recipe running
  serially — correct answer, wrong build, silent.)
- **Size the instance and the TTL from a local run, never from a guess about what
  the tool "probably needs."** Run the tool in the pinned image first, read its peak
  RSS and wall time, then pick the family from the measurement and set TTL at ~2x
  measured wall. This is also how smoke-check bands get their numbers — same local
  run, so it costs nothing extra. **TTL is the cost cap, not a safety margin**: a
  loose TTL is not free caution, it is a larger blast radius. Measured beats guessed
  by real money — sizing the five recipes from observation instead of from a hunch
  about `m8g` took the batch from $1.37 worst case to $0.80. **Then retighten from the
  first real run**, which is strictly better evidence and already paid for: Graviton4
  ran RAxML-NG's search 2.17x faster than Docker Desktop (7m39s vs 16m35s), so the TTL
  sized from the local measurement came down 35m → 20m and the cap $0.19 → $0.11 on
  identical work. A local run is the right *first* move because it is free, not because
  it is accurate — **and it errs in both directions, so budget for the bad one.** AmberTools'
  `sander` went the other way: 14.95 ns/day on an Apple-Silicon laptop against **9.45 on
  Graviton4**, so the local number was 1.6x *optimistic* and a TTL at 2x it left 1.45x margin
  on the real run, not 2x. Fast laptop cores flatter a serial code and the measurement says
  nothing about which way a given tool will go, so on a first launch size the TTL from the
  local wall **times the slowdown you would still survive**, then retighten from the run. **And a measured sample is not the same as a *representative* one: an
  average over a heterogeneous workload does not license sizing from a sub-window of it.**
  GATK4 HaplotypeCaller's rate along chr20 varies ~50x with local complexity (~9,480
  regions/min on the p-arm, **168** in pericentromeric 30-31 Mb — 1.07 Mb in 35.4 min), so a
  2 Mb canary said 41 min for a job that takes 132, a mid-run linear extrapolation said 112,
  and an interval chosen from a genuine 0.54 Mb/min average turned out to be the worst one
  available. Three TTL deaths and ~$1.65 before the rule landed: **sweep only what has been
  clocked end to end, and take the whole-workload number from a whole-workload run.**
  (Overspend, and a band that was never observed.)
- **Never trust an exit code as evidence the outputs are real.** The correctness bar is
  a smoke check that runs **inside** the task (so it can fail the task) *plus* a
  confirmation that the objects are actually in the bucket afterwards. This is stronger
  than any exit code by construction, and independent of whether the runner's exit codes
  are even honest: an exit code reports that the command *ran*, never that its output is
  real — output that is empty, truncated, or garbage still exits 0. So the rule is
  smoke-check-plus-bucket-check because they prove *realness*, full stop. (History, so
  no one re-derives it: two spawn bugs once widened the gap — a lost output recorded
  `state: completed, exit_code: 0` [spawn#561], and a failed command writing no
  completion record at all so the box rode to TTL [spawn#566] — both fixed in spawn
  0.104.0, so failures now surface with a record. The rule predates and outlives them.)
  (Silent failure — output that exists but is empty, garbage, or absent, wearing a green
  check.)
- **A run that dies must leave its evidence behind, and a diagnostic must never be able to kill
  the run it describes.** `command.log` only reaches S3 at stage-out (spawn#632), so anything the
  task captured dies with the instance: **stage the tool's own log out** (`mdrun.log`,
  `flye.log`, `kraken2.log`) and run Python with **`python3 -u`**, because buffered stdout is lost
  when a process is killed — a probe that dies then prints nothing even though it had produced
  output. Measured across 118 specs: 58 wrote a log they never uploaded, 22 buffered. mdtraj cost
  two blind-diagnosed failures and flye one; a muscle TTL death left nothing at all. The mirror
  rule: a version print that guessed an API (`mdtraj.version`, which does not exist in that build)
  raised and **threw away a completed 2.5-minute simulation** — use `getattr(mod, "__version__",
  "unknown")`, never a guessed attribute path, and never let a line whose only job is to describe
  the run be able to fail it. `make check` warns on both; the fix lands when a recipe is next run,
  because editing a spec means re-verifying it. (A failure you cannot diagnose, so the retry is
  another guess — and a finished run discarded by its own logging.)
- **Stop at the boundary.** Writing a recipe, staging an input, requesting an image
  are in bounds. Building a harness, a gate framework, a shared engine, or anything
  with its own test suite is not — stop and report. (Scope drift, the main risk.)
- **Use the platform, don't rebuild it.** truffle/spawn/lagotto/nf-spawn. No retry
  loops, no polling babysitters, no CLI-output scraping where structured output
  exists.
- **Zero spend by default.** Any launch needs explicit authorization with a stated
  ceiling; estimate first, TTL and a cost cap on every launch (`lifecycle.ttl` and
  `lifecycle.cost_limit`, both in-spec since spawn 0.103.0), report the pre-flight
  and hold.
- **A spec that parses is not a spec that acts — confirm a TaskSpec field is honored
  before relying on it.** Until spawn 0.103.0, `ParseSpec` discarded unknown keys
  silently *and* `resources.disk_gib` / `lifecycle.cost_limit` did not exist, so a spec
  asking for a bigger disk or a cost cap validated cleanly and launched without either
  (spawn#556/#558 added both fields plus `DisallowUnknownFields`). Two free checks settle
  it: does the spec still parse with the field present, and does `--dry-run` echo the
  resolved value back. Both were run before `cost_limit` went on the seven recipes.
  (A guardrail you believe is armed and isn't.)

## Inputs and images

- **Orchestration:** the spore.host suite only.
- **arm64 images: if a real arm64 container already exists upstream, use it.** aarch.bio
  (bioconda layer) / aarch.science (conda-forge) exist to fill the many gaps where one
  does not — **or where a "multi-arch" tag does not actually contain an arm64 entry.**
  Never fall back to x86 or an emulated image; a missing image is still a gap to record.
- **Walk the manifest list's entries; a manifest list is not evidence of arm64.**
  `docker manifest inspect <img>` returning a list means multi-arch was *intended*, not
  that arm64 is in it. Measured: `gromacs/gromacs:latest` and `psi4/psi4:latest` are both
  manifest lists whose only entry is `linux/amd64`, so a Graviton pull fails or silently
  emulates. And a *single* manifest carries no `os`/`architecture` at the top level, so
  it reads as `?/?` — resolve it with `--verbose` and read `Descriptor.platform` (that is
  how the three BioContainers images below were confirmed `linux/amd64`). Check before
  requesting a build: it removed **R** (official `r-base` is `linux/arm64/v8`, `rocker/r-ver`
  is amd64+arm64) and **NWChem** (`ghcr.io/nwchemgit/nwchem-dev` ships `linux/arm64`) from
  an approved 11-code request batch. (Asking for a build that already exists, or pinning a
  tag that emulates.)
- **Image requests are filed directly on `playgroundlogic/aarchbio` and
  `playgroundlogic/aarchsci`, through their own templates.** aarchbio takes one tool per
  image: title `request: <tool>=<version>`, label `container-request`. aarchsci ships
  **curated envs, not per-code images**, so an ask is "new env X" or "add P to existing
  env Y" (`env-request: <name>`) — check what already ships first. Carry the evidence in
  the body: the conda-forge/bioconda `linux-aarch64` build string, that the namespace
  doesn't already have it (paginate — the quay API caps at 100 of 503 repos), and the
  upstream container's real architecture.
- **Data:** RODA first → a stable public source with a durable id (Zenodo/DOI,
  Ensembl/UCSC, versioned release) → build last. Pick the tier yourself and record
  it; flag build-tier inputs for later replacement. Pin regardless of tier — if it
  can't be pinned, it doesn't qualify; drop a tier. Subsample inputs small so a
  proof-run is fast and cheap.

## When the platform blocks you, file upstream

- **spore.host tooling bug or rough edge** (dropped flag, misleading message) →
  **auto-file** an issue on the relevant spore.host repo with file:line evidence;
  name the pattern if several share a root cause. Keep any local mitigation as well.
- **Missing arm64 image** → aarch.* (batched, above).
- **An upstream packaging or code bug we hit and can characterize** → file it wherever
  it lives, not only spore.host/aarch.*. A reproduction plus a root cause is worth a
  report on any repo: bioconda's `relion` recipe carries `ghostscript` in `host:` but
  not `run:`, so `relion_postprocess` hard-fails at the end of a correct run — filed
  `bioconda/bioconda-recipes#68838` with the `CPlot2D.cpp` line chain and the one-line
  fix. Offer the PR if the fix is small and certain; a fixed package unblocks everyone,
  not just us. Keep the local workaround regardless (here, an absolute `--o`).
- Blocked-and-filed is honest. Worked-around-quietly is the trap.

## Tracking and findings

- **State lives on GitHub only** — project board, milestones, issues, labels. This
  file and CHARTER.md never carry status. To learn what's done or next, read the
  board.
- A finding worth keeping is a **GitHub issue**, not a repo file. A finding that
  becomes a standing rule belongs **here**, stated flat. There is no findings log.

## References

CHARTER.md (why). GitHub project board (state). docs.spore.host and the
spore-host / aarchbio / aarchsci repo sources (tooling truth). catalog/recipes.md
(the generated inventory of shipped recipes — `make catalog`, never hand-edited; the
target list and remaining work live on the board). patterns/execution-shapes.md (the
conceptual A–G shape map, not an inventory).

**How a recipe is built is these rules plus a worked example, not a separate document.**
`recipes/salmon/` is the exemplar — copy its section order (caveat-first if the result
misleads, why-N-tasks, pins with data tier, smoke-check table as assertion + observed,
resources with "these timings are not compute cost", running-it with the bucket check,
re-running). `recipes/star/README.md` shows the caveat blockquote when the science is
real but the numbers are not representative. There is no pattern doc, for the same
reason there is no findings log: a second statement of these rules is a second thing
to keep true.

## Recipe pages — the cookbook layer contract (enforceable, not aspirational)

The cookbook *layer* is a reader-facing rewrite of each recipe. **The bar: a reader who
already knows the tool gets what to run and what to change for their own data in under a
minute.** A page that takes longer to get to the point is wrong regardless of what it
contains. Wordiness is the default failure mode — when in doubt, delete. `recipes/bedtools`
is the exemplar. `make check` enforces the `[auto]` rules below; the rest is human review.

**Reader: a graduate student who knows their code and their science, not AWS.** Assume
domain competence — never explain what a variant call or an SCF *is*. Do explain (or link)
a spawn/staging/instance concept when it's load-bearing.

**Recipe pages (`recipes/*/README.md`):**
- **R1 — Lede in ≤2 lines after the H1: what it does and who it's for — nothing else.**
  **Verification philosophy goes below the fold, always.** A lede that says "the check is that…"
  is the most common drift in this catalog — the check description belongs in `<details>`, where
  it already lives; a second copy in the lede is duplication in the wrong place, not a summary.
  [auto: ≤8 non-blank lines between H1 and first `##`; human: is it buried, does it verify above
  the fold]
- **R2 — `## Run it` is the first `##`,** with a fenced real invocation inside it (before
  `## Make it yours`) that a user of the tool recognizes at a glance. [auto]
- **R3 — `## Make it yours` is mandatory:** the fixture / swap / what-to-know table **and** an
  explicit leave-it-or-scale-it sentence with its argument. Scale a fixture only where it
  MISREPRESENTS the tool (chr20's 29% mapped earned it); most fixtures are honest and small is
  often the point (hand-checkable). [auto: present; human: argument sound]
- **R4 — Order:** Run it → Make it yours → brief Shape/size/cost → `<details>`. [auto]
- **R5 — All verification in exactly one collapsed `<details>`** (identities, pins,
  smoke-check table, run+verify); nothing above the fold that isn't for someone running it. [auto]
- **R6 — ≤50 lines outside `<details>`.** Over is a defect needing justification. [auto]
- **R7 — No re-teaching:** link a pattern/practice, never restate it — and **prefer a link to a
  paraphrase**, because a paraphrase is *worse* than a verbatim copy: the verbatim one is at least
  detectable. [auto is only a FLOOR — it catches an exact owned phrase appearing without that
  page's link. **Synonymic re-teaching** (the same idea in the drafter's own words — bcftools'
  "apples-to-apples", raxml-ng's "same rule as the assemblers") is invisible to the checker and is
  human review. Do not trust R7 to cover re-teaching.]
- **R8 — Cut hard.** Flabby prose under the ceiling still fails the one-minute bar. [human]
- **Frontmatter** (machine-checkable versions): `tool`, `tool_version`, `image` (full
  `@sha256:`), `spawn_version`, `last_verified`. Pipeline recipes use `images:` (one digest per
  tool). `last_verified` is a date **only a real verifying run may set** — absent is honest
  ("not verified since tracking began") and is a TODO queue, never backfilled to silence the
  warning; that's the false-green trap one layer up. Page freshness (`last_updated`) is *not*
  frontmatter — it's git-derived and generated into catalog/recipes.md, since git already knows
  it and a stored copy would be a second source that drifts. [auto]

**Ancillary pages (`patterns/`, `practices/`, README, CHARTER):** lede in the first 2 lines
(a thesis line/blockquote); one idea, tight sections, no re-teaching of a sibling page; no
`## Run it`/`## Make it yours` (they aren't recipes). [auto: lede present, re-teach grep,
warn over ~90 lines; human: the rest]
