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
it. Fifty of those is the catalog — now built, which is what the measurement phase below rests on.

GPU-bound codes wait for **Round Two**, an x86 pass on real GPU instances — the only
Graviton GPU is g7g, too small to be a representative example.

## Measurement, now — the deferral honored

This charter used to defer the cost-per-result question — cheapest on Graviton, across
architectures, on Spot — to a later phase: measurement comes on top of the working
examples, *once they exist.* **They exist** — the catalog runs in a clean account,
verified and self-terminating. **You cannot price a result you cannot reproduce** — that
is why the examples came first, and why measuring them now means something. The deferral
is met, not dropped; a $/result figure on a recipe nobody can run is exactly what it
protected against.

So measurement is now the work, and it has a shape — **one metric, cost per result, and
four levers that move it:**

- **Right-size** — more cores stop paying past a knee ([sizing](patterns/sizing.md)).
- **Scale out** — a cohort is the same task fanned out, less wall time at the same cost ([job arrays](patterns/job-arrays.md)).
- **Newer generation** — a box can cost more per hour and less per result: c8g → c9g, +9%/hr and −12% per result on two workloads ([cost per result](patterns/cost-per-result.md)).
- **Pay for the bytes you touch** — the data path moves cost as much as the hardware ([copy, mount, or share?](patterns/data-movement.md)).

Two halves of the sub-text, each doing different work: **the number you think you're
comparing isn't the number** (rate card and vCPU count both mislead; only rate × wall on
like-with-like hardware is real), and **you can get more done per dollar** — the first
half gets attention, the second changes what you launch.

The discipline is the recipes' own: **claim what the curves support, name what they
don't.** Its best evidence is a headline we did *not* publish — "Graviton is 2.5× slower
for assembly" was there for the taking and was false, a build difference between container
channels, not the silicon. The measurement pages lead with that catch rather than bury it.

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
