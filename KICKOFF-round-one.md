# KICKOFF — scientific-codes-cookbook (fresh start)

A one-time setup task. Do these steps, then hold. No recipes, no launches — this is
structure only, and it's all reads / file-creation / GitHub scaffolding (zero spend).

This is a clean start. There is **no prior work to inherit** — build everything
below fresh from the rules in CLAUDE.md. Do not look for or import a previous
harness, engine, or benchmark repo; the project starts here.

## Setup

1. **Repo.** This directory (`~/src/scientific-codes-cookbook/`) is the working
   directory. Create `scttfrdmn/scientific-codes-cookbook` on GitHub and initialize
   this as its clean root. (CHARTER.md, CLAUDE.md, KICKOFF-round-one.md, and
   catalog/cookbook.md are already in place — see step 3.)

2. **CHARTER.md and CLAUDE.md** are already at the repo root. Read both; they are the
   why and the rules. Don't add status to either.

3. **Catalog is in place.** `catalog/cookbook.md` is the authoritative ~50-code list
   the survey enumerates from. The superseded `spore-host-cost-to-result-BUILD.md`
   has been removed; do not restore it.

4. **GitHub tracking (the only place state lives).**
   - Milestones: `Survey`, `Round One — working examples`, `Round Two — x86/GPU`,
     `Later — measurement (deferred)`.
   - Labels: `shape:A`..`shape:G`; `blocker:image-gap`, `blocker:disk`,
     `blocker:spore-bug`; `round-one`, `round-two`; `image-request`,
     `upstream:aarchbio`, `upstream:aarchsci`, `upstream:spore-host`.
   - A project board, a column per milestone, one issue per code as recipes get built.
     This board is the only progress tracker.

## First work item (after setup, hold for go-ahead)

**The wide survey** — all ~50 codes from `catalog/cookbook.md`, reads-only, zero
spend, every cell unverified. Question per code: *what does it need to run once on a
Graviton4 8g box.* Columns that matter: shape, arm64 image (exists / GAP), fixed
input + tier + size, disk peak / fits-20GiB-root, smoke check, 8g fit (c/m/r + EFA),
GPU→Round-Two?, license. Output a single grid plus the two blocker lists (image gaps
→ batched requests; disk-blocked → a spore.host issue) as GitHub issues.

Keep the survey itself light — a grid and two lists, not a framework.

## Boundaries

- Setup only. No recipes, no launches, no staging.
- Fresh build from the rules; inherit nothing.
- Report what was created, then hold for the survey go-ahead.
