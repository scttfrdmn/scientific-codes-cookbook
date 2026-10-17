---
tool: msprime
tool_version: "1.4.4"
env: popgen
image: quay.io/aarchsci/popgen@sha256:aac6868e30955dc02250502f7c282625be12093081af943f48f2cbe90ae8760c
env_x86_64: popgen-39c447dd4e42
spawn_version: 0.127.0
last_verified: 2026-10-11
last_verified_x86_64: 2026-10-17
---
# msprime + tskit — coalescent simulation against closed forms

Simulates 40 genomes over 1 Mb on Graviton4 and checks the result against coalescent theory, integer tree invariants, and a second implementation of the same statistics. For anyone doing population genetics on ARM.

## Run it

```bash
make stage RECIPE=popgen     # once: the checks only — every reference is theory
spawn task run --spec "$(make -s spec RECIPE=popgen)" --wait
make ls RECIPE=popgen

ts = msprime.sim_ancestry(samples=20, population_size=10_000, recombination_rate=1e-8,
                          sequence_length=1e6, random_seed=20261011)   # 20 diploids -> n=40
mts = msprime.sim_mutations(ts, rate=1e-8, random_seed=20261011)
mts.diversity(mode="site")                      # -> 4*Ne*mu, in expectation
mts.tables.equals(other.tables, ignore_provenance=True)   # NOT `tables ==`
```

## Make it yours

| In the recipe | Swap for | What to know |
|---|---|---|
| `ignore_provenance=True` | `tables == tables` | **always False.** The provenance table holds timestamps and the command line, so a plain `==` differs on every run whatever the seed — it says nothing about determinism. |
| `start=1, stop=L` for scikit-allel | `start=0` | allel's span is `stop - start + 1`, so `start=0` covers `L+1` sites and every per-site statistic is off by `1/L`. That is exactly the `1.000e-06` it cost here. |
| `samples=20` | — | that is **20 diploid individuals = 40 sample nodes**, and `n = 40` is what goes in `2n-1` and in Watterson's `a_n`. Using 20 makes both wrong by a factor of two. |
| one replicate of `π` | 24 | a single replicate ran **0.81–1.09** of `4·Ne·μ`. The closed form is only assertable on a mean against its standard error. |
| `mode="site"` | `mode="branch"` | site mode is span-normalised already (per site); branch mode is not. Mixing them is where a factor of `L` creeps in. |
| this synthetic sweep | a real dataset | nothing is fetched — [tskit](https://tskit.dev) tree sequences from SLiM or stdpopsim slot into the same checks, but the closed form only holds for the neutral coalescent. |

**Leave the fixture.** No data is staged: coalescent theory supplies the references, so the right answers exist before msprime runs. **Scale it** by raising `sequence_length` or the replicate count — both tighten the closed-form check, and the structural invariants hold at any size.

## Shape, size, cost

One task on `m8g.large` (2 vCPU / 8 GiB), TTL 30m as a **backstop** with `cost_limit` $0.12 as the real guard. All of it — 25 simulations, 1,481 trees of invariant checking, two cross-validations — is **5 s** of a 1m19s window; 39 s is installing Docker and 34 s pulling the image ([layout](../../patterns/layout-and-effective-cost.md)).

<details>
<summary>As shipped: integer tree invariants, an exact accounting for an apparent disagreement, and a closed form bounded by its own standard error</summary>

### The checks

| observable | assertion | observed |
|---|---|---|
| staged bytes | match pin | 1 of 1 |
| msprime / tskit / scikit-allel | read from the running install | **1.4.4** / 1.0.3 / 1.3.13 |
| individuals → sample nodes | **20 → 40** (diploid) | 20 / 40 |
| **every tree single-rooted** | **0 violations** | **0 of 1,481** |
| **every node binary** | **0 violations** | **0** |
| **nodes per tree** | **== 2n−1 = 79** | 0 trees wrong |
| **edges per tree** | **== 2n−2 = 78** | 0 trees wrong |
| **determinism** | **same seed, tables equal** | identical |
| control: different seed | **must differ** | differs |
| dump → load | tables equal | yes |
| **polarised AFS sum** | **== num_mutations** | **1736.0** |
| **π: tskit vs scikit-allel** | **< 1e-12 relative** | **4.668e-15** |
| allel span convention | recomputed by hand, < 1e-12 | **0.000e+00** |
| **θ ratio == num_mutations/num_sites** | **< 1e-9** | **0.000e+00** |
| **mean π / 4·Ne·μ** | **within 4 SE of 1.0** | **1.37 SE** (mean 0.9804) |
| single-replicate spread | *reported* | 0.8104 – 1.0897 |

**x86_64** (`m8i.large`, the `popgen-39c447dd4e42` twin installed offline): `score.tsv` is
**byte-identical** to arm64's, every row above. Nothing here is a BLAS path — msprime's RNG and the
tree algorithms are integer, and π/θ are sums over them. The twin differs from the arm64 env only in
filelock, fonttools, libcurl, libglib, libsqlite and wrapt, none on the computation.

### Integers a coalescent simulator cannot fake

A binary tree on `n` samples has exactly `2n−1` nodes and `2n−2` edges, one root if it is fully
coalesced, and no node with one child. With `n = 40` that is 79 and 78, and it holds on **all
1,481 marginal trees** along the sequence:

```text
trees checked              1481
not single-rooted             0
nodes with != 0 or 2 children 0
wrong node count              0
wrong edge count              0
```

No reference value, no tolerance. A simulator that left a tree uncoalesced, or emitted a unary
node, fails on an integer. Same family as the Euler characteristic in
[paraview](../paraview/README.md) and group closure in [gemmi](../gemmi/README.md).

### An apparent disagreement that is exactly accountable

tskit and scikit-allel compute the same statistics from **different data** — a tree sequence
versus a genotype matrix — so agreement is about the numerics rather than internal consistency.
Pairwise diversity agrees to `4.668e-15`. Watterson's θ appeared not to:

```text
theta, tskit        4.081303478321e-04
theta, scikit-allel 4.074250534522e-04      <- 1.73e-03 apart
```

**That gap is not an error in either tool.** tskit's `segregating_sites` counts **mutations**
(1736); scikit-allel counts segregating **sites** (1733), and they differ wherever a site carries
more than one mutation. The ratio is therefore predictable, and the recipe asserts it rather than
reporting a near-miss:

```text
theta_tskit / theta_allel   1.001731102135
num_mutations / num_sites   1.001731102135
difference                  0.000e+00
```

So the whole discrepancy is the counting convention, to every digit printed. That is a much
stronger statement than "they agree to 0.2%", and it fails if anything *else* ever contributes —
which a loose band would have hidden. Deciding which quantity each tool reports before comparing is
the same discipline as matching integral treatments or alignment modes
([cross-checks](../../practices/cross-checks.md)).

### Three comparisons that measured metadata, and one off-by-one

Worth recording because the same mistake wore four costumes here:

- **`tables == tables` includes the provenance table** (timestamps, command line), so it is False
  for a repeated seed. Determinism needs `ignore_provenance=True` — then a repeat is identical and
  a different seed differs, which makes the control meaningful rather than vacuous.
- **The `.trees` file `sha256` differs while the tables are equal.** Bytes are the wrong
  granularity for a round-trip check.
- **tskit's `mode="site"` statistics are already span-normalised.** Dividing by `L` again made
  tskit and allel look `1e6` apart — arithmetic, not a finding.
- **scikit-allel's span is `stop − start + 1`.** Passing `start=0, stop=L` covers `L+1` sites, so
  π came out `1.000e-06` off — precisely `1/L`, the signature of an off-by-one. **The 1e-12 bound
  caught it and failed the run**; a 1e-3 tolerance would have shipped a comparison that was wrong
  by one part in a million while claiming two implementations agreed. The convention is now pinned
  by recomputing allel's own estimator as `S_sites / (a_n · L)` and asserting a match, so it is a
  checked fact rather than a lucky argument.

### The closed form, and why it needs 24 replicates

`E[π] = 4·Ne·μ` per site under the neutral coalescent — a theoretical reference, not a band on an
observation. But one replicate is nowhere near it:

```text
single replicate        0.8104 .. 1.0897  of 4*Ne*mu
mean of 24 replicates   0.9804
sd                      0.0702
standard error          0.0143
deviation from 1.0      1.37 SE          (bound: 4 SE)
```

That spread is real coalescent variance, not error, so the assertion is on the **mean against its
own standard error** — a bound from the sampling distribution rather than a band drawn around
whatever the run produced. A biased estimator would sit many SE out; sampling noise will not. The
single-replicate range is reported so nobody reads one simulation as a measurement of θ.

### Pins

| | |
|---|---|
| data | **none.** References are coalescent closed forms (`4·Ne·μ`, Watterson's `a_n`) and integer tree invariants; sims seeded from `random_seed=20261011` |
| checks | `identities.py`, pinned by sha256 |
| image | `quay.io/aarchsci/popgen@sha256:aac6868e30955dc022…` — msprime 1.4.4, tskit 1.0.3, scikit-allel 1.3.13, demes 0.2.3, pyslim 1.1.1, tszip 0.3.1, numpy 2.5.4 |

cosign-verified against `playgroundlogic/aarchsci`; the signature covers the **manifest-list**
digest, so verify the tag and pin the arm64 digest.

This env was built in response to [aarchsci#40](https://github.com/playgroundlogic/aarchsci/issues/40) —
nothing in the previous 23 envs covered population genetics. Staging derives every reference
without msprime in the loop, including `a_n` and the `2n−1`/`2n−2` counts, and checks θ is small
enough that the multi-hit correction above stays a small one.

### Run + verify

```sh
make stage RECIPE=popgen
spawn task run --spec "$(make -s spec RECIPE=popgen)" --wait
aws s3 cp "s3://$(make -s print-bucket)/runs/popgen/r1/score.tsv" -
```

Fails on a pin mismatch, any tree that is not single-rooted and strictly binary with 79 nodes and
78 edges, a seed that does not reproduce, a *different* seed that does, a dump/load that loses the
tables, an AFS that does not sum to the mutation count, a tskit/allel diversity gap above 1e-12, a
span convention that does not span `L`, a θ ratio that is not exactly `num_mutations/num_sites`, or
a mean π more than 4 SE from `4·Ne·μ` — but check the bucket regardless
([exit 0 isn't proof](../../practices/container-path.md)).

### Not covered

**Demography.** One constant-size panmictic population. Population splits, migration, bottlenecks
and admixture are the reason people use msprime, and `demes` ships in this env unexercised — a
demographic model's expected `π` per deme is the natural next closed form.

**Selection.** Everything here is neutral, so nothing touches sweeps, background selection or
`msprime`'s SLiM interop (`pyslim` is present and unused). **The DTWF and other ancestry models** —
only the default Hudson coalescent runs. **Site frequency spectrum beyond the sum**: the shape of
the AFS has a closed form (`E[AFS_i] ∝ 1/i` under neutrality) that is not asserted here.
**tszip** compression, present and unexercised. **Large sample sizes**: n = 40 over 1 Mb is small,
so nothing here speaks to memory or runtime at cohort scale.

</details>
