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
- **Stop at the boundary.** Writing a recipe, staging an input, requesting an image
  are in bounds. Building a harness, a gate framework, a shared engine, or anything
  with its own test suite is not — stop and report. (Scope drift, the main risk.)
- **Use the platform, don't rebuild it.** truffle/spawn/lagotto/nf-spawn. No retry
  loops, no polling babysitters, no CLI-output scraping where structured output
  exists.
- **Zero spend by default.** Any launch needs explicit authorization with a stated
  ceiling; estimate first, TTL and a cost cap on every launch, report the pre-flight
  and hold.

## Inputs and images

- **Orchestration:** the spore.host suite only.
- **arm64 images:** aarch.bio (bioconda layer) / aarch.science (conda-forge). If an
  image is missing, that's a gap — **record it, never fall back to x86 or an
  unverified image.** Image requests are **batched for Scott's review**, not
  auto-filed (his repos, his backlog).
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
