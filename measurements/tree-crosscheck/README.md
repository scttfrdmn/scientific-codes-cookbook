# Two ML search heuristics, one optimum: 7.8e-9 apart

> **IQ-TREE and RAxML-NG independently reach −52706.731 and −52706.731409** on the same alignment
> under the same model — agreement to 1 part in 1e8 between two unrelated codebases with different
> search strategies. And the optimum is identical on all four Graviton generations, to six decimals.

Pfam 38.2 `PF26127.1` (`12TM_Mok13`), **114 taxa × 477 columns**, LG+G4, fixed seed 12345, threads
pinned on both sides.

| code | version | threads | log-likelihood |
|---|---|---|---|
| IQ-TREE | 3.1.3 | `-T 4` | **−52706.731** |
| RAxML-NG | 2.0.2 | `--threads 8 --workers 1` | **−52706.731409** |
| | | difference | **0.000409 absolute, 7.8e-9 relative** |

## Why this is the strongest kind of check available

A single tool can only tell you it is self-consistent. Two tools with *different search heuristics*
converging on the same maximum tells you the **likelihood surface itself** is being computed the
same way — a statement about numerics that no internal identity reaches. Each recipe becomes the
other's check, and it cost nothing but pointing the second tool at the first one's staged alignment.

**The tolerance is set by the problem, not by the agreement.** It is 0.01 absolute because IQ-TREE
prints three decimals, so ±0.0005 of rounding is inherent in the comparison. Asserting 1e-6 would be
asserting a print format; asserting 1.0 would pass two genuinely different optima. The observed
0.000409 clears the bound 24×.

## What is deliberately *not* asserted

**The likelihood value itself, on either page.** An ML search is stochastic: thread count changes
the order of likelihood updates and therefore which optimum the heuristic lands on, so an exact
assertion goes flaky across thread counts and versions even with a fixed seed. Both recipes assert a
wide band (−54000..−52000) and pin `-T` / `--threads` plus `--seed` so the run is reproducible —
then the *cross-code* agreement carries the tight claim.

**The trees.** The two tools' topologies are not asserted equal. A related measurement found
mafft↔muscle Robinson-Foulds distances of 26 versus 4 on the same alignments purely from search
stochasticity, so topology equality between independent searches is an observation at best.

## Chip-independence, and a generation step that does not pay

RAxML-NG across four generations, same seed, same 8 threads, search-only elapsed from its own log:

| generation | instance | search | **$/search** | log-likelihood |
|---|---|---|---|---|
| Graviton2 | `c6g.2xlarge` | 638.1 s | 0.0482 | −52706.731409 |
| Graviton3 | `c7g.2xlarge` | 465.0 s | **0.0375** | −52706.731409 |
| Graviton4 | `c8g.2xlarge` | 456.0 s | 0.0404 | −52706.731409 |
| **Graviton5** | `c9g.2xlarge` | **313.0 s** | **0.0303** | −52706.731409 |

**Identical to six decimals on every chip** — which is what pinning the seed and the thread count
buys, and a useful check in its own right: if the chip could move a converged optimum, something
would be wrong with the determinism the recipes claim.

2.04× faster Graviton2→5 and 37% cheaper, **but Graviton4 is 7.3% dearer than Graviton3**, buying
only 2% of wall for 10% more per hour. That is the sharpest instance in this catalog of that rung
not paying, and it is now three codes — RAxML-NG here, [GPAW and SIESTA](../dft-crosscheck/README.md)
at +1.4% and +0.7%. All three have likelihood-or-matrix inner loops rather than the streaming
throughput the newer chips added. The mechanism is not established; it would need a bandwidth
measurement.

## Run it

```sh
export AWS_PROFILE=aws COOKBOOK_BUCKET=<your bucket>
make stage RECIPE=iqtree     # the Pfam seed alignment, read by both
make run   RECIPE=iqtree
make run   RECIPE=raxml-ng
spawn task run --spec measurements/tree-crosscheck/crosscheck.task.json --wait
```

The cross-check is a task rather than prose, so the agreement is asserted and fails loudly if either
tool's optimum moves.

## Caveats

n = 1 per cell, and repetition buys nothing here: both searches are deterministic given the pinned
seed and thread count, which the four identical generation results demonstrate.

One alignment, one model, one pair of tools. The agreement establishes that these two codes compute
this likelihood surface identically; it says nothing about larger alignments, partitioned models, or
topology agreement.
