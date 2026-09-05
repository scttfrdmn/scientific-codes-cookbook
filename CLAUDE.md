# CLAUDE.md — scientific-codes-cookbook

## What this is

A cookbook: working, runnable recipes for the ~50 common research-computing codes
on spore.host. **Round One builds working examples** — does each code run cleanly
on a Graviton4 box, producing real output. Not benchmarks. Cost measurement is a
later phase and is out of scope now.

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
- **Local Docker on macOS cannot prove uid/permission behaviour.** Bind mounts there
  don't enforce sticky-bit ownership, so a dry run passes where the real Linux host
  fails — that is exactly how the `rm` above got through seven green dry runs. When
  the question is *permissions*, test it on a real Linux filesystem: create the file
  as root in a `1777` dir inside the container's own fs, then `setpriv --reuid` to the
  image's user. (False confidence from a green local run.)
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
  convenient proxy for it. (An assertion that fails for reasons unrelated to correctness,
  or waves through garbage.)
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
  precision. PySCF and Psi4 on H2 RHF/STO-3G agree only to 2.4e-5 Ha, and that is *correct*:
  STO-3G's contraction coefficients aren't standardized across packages, so two right HF
  codes must differ at the 5th decimal on a minimal basis — the check asserts agreement to
  chemical accuracy (<1 mHa), which still catches a broken integral/SCF (those diverge by
  mHa–Ha) while a 1e-8 band would fail for a reason unrelated to correctness. Same technique,
  different justified tolerance; the basis sets it, not the noise.
  (A check that only proves a tool is self-consistent, when a stronger one was free; or a
  cross-code tolerance picked to pass rather than justified by the problem.)
- **A cross-code check must compare like with like — before asserting agreement, verify the
  metric measures agreement and not a difference in method.** A naive metric failing is not
  evidence of a bug in either tool; it is evidence the comparison was wrong, and a green
  check on the wrong metric is worse than none. The three failure modes, all caught in local
  validation in the first aarch.bio batch: **different models make raw values incomparable —
  use rank** (kallisto↔salmon TPM: raw log-TPM Pearson 0.61 because the two use different
  EM/effective-length models, Spearman rank 0.912 once you ask the question they can both
  answer); **repeat-heavy references make all-mapped concordance meaningless — restrict to
  confident calls** (minimap2↔bwa: naive all-mapped 0.43 on the chr20-only subsample where
  two correct aligners break repeat ties differently, 0.9921 gated on MAPQ≥30 and ≤5 bp —
  "agree where both are sure"); **different alignment modes reject different reads — match the
  modes** (bowtie2↔bwa: default end-to-end vs bwa's soft-clipping gave 82%, `--local` on the
  mapped set gave 0.9462). This is the sibling of the tolerance rule above — that one justifies
  the *number*, this one justifies the *metric*. Both are the same discipline: assert the claim
  you mean. (A cross-code check that fails, or passes, for a reason unrelated to correctness
  because the metric compared a method difference instead of the science.)
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
  it is accurate. (Overspend, and a band that was never observed.)
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
spore-host / aarchbio / aarchsci repo sources (tooling truth). catalog/cookbook.md
(the ~50-code list this project works through).

**How a recipe is built is these rules plus a worked example, not a separate document.**
`recipes/salmon/` is the exemplar — copy its section order (caveat-first if the result
misleads, why-N-tasks, pins with data tier, smoke-check table as assertion + observed,
resources with "these timings are not compute cost", running-it with the bucket check,
re-running). `recipes/star/README.md` shows the caveat blockquote when the science is
real but the numbers are not representative. There is no pattern doc, for the same
reason there is no findings log: a second statement of these rules is a second thing
to keep true.
