# CHARTER — scientific-codes-cookbook

## What this is

A cookbook: working, runnable recipes for the ~50 common research-computing codes
on spore.host. For each code, someone can find their code, copy the recipe, and run
it — truffle finds the box, spawn runs it in a pinned container, it produces real
output, the box tears itself down.

**The recipe is the product.** The test for any piece of work is: does it move a
code toward a working example? If not, it isn't this project.

## Round One: working examples on Graviton4

Each code gets a recipe that *runs* on one Graviton4 8g box (c8g / m8g / r8g by fit,
EFA-capable 8g for multi-node), in a pinned arm64 container, producing output a
smoke check confirms is real. No cost measurement, no benchmarking, no multi-arch
comparison. Just: does it run, cleanly, on Graviton — yes, with a recipe that proves
it. Fifty of those is the catalog.

GPU-bound codes wait for **Round Two**, an x86 pass on real GPU instances — the only
Graviton GPU is g7g, too small to be a representative example.

## Measurement is a later phase, deferred

Whether a code is cheapest on Graviton, or across architectures, or on Spot — the
cost-per-result question — is real and interesting, and it is **not** this phase.
Round One is about *running*, not measuring. Measurement comes later, on top of the
working examples, once they exist. Don't pull it forward.

## How this stays a project and not a machine

The scaffolding around a recipe should never outgrow the recipe. A working example
is a small thing — find, run, check, done — and the structure that proves it should
be small too. Build recipes and the minimum that makes them trustworthy; don't build
a harness, a gate framework, or an engine. When in doubt, ship the working example
and stop.

The arm64 images come from aarch.bio and aarch.science; when one is missing, the
cookbook drives it — a request, not a workaround. The spore.host suite is the
tooling; when it gets in the way, the cookbook files the fix upstream. Building the
catalog makes both better as a byproduct.

Progress lives on the GitHub board. The rules live in CLAUDE.md. This charter is the
why, and it doesn't change often.
