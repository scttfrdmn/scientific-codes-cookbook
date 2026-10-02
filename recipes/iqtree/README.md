---
tool: iqtree
tool_version: 3.1.3
image: quay.io/aarchbio/iqtree@sha256:dc6d9f62d56fd1ca92bfb2a9fbd162d419d4f866de67e6e879ba0f895d2a6fb7
spawn_version: 0.111.4
last_verified: 2026-10-01
---
# IQ-TREE — an ML phylogeny, cross-validated against RAxML-NG to 1 part in 1e8

Infers a maximum-likelihood tree for a 114-taxon Pfam alignment under LG+G4, reaching −52706.731 — the same optimum [RAxML-NG finds independently](../raxml-ng/README.md). For anyone building trees on ARM.

> **Two unrelated search heuristics, one optimum, 0.000409 apart.** That agreement is the check; neither tool's own likelihood can be asserted exactly, because an ML search is stochastic.

## Run it

```bash
make stage RECIPE=iqtree   # the Pfam 38.2 seed alignment, read by both tree recipes
make run   RECIPE=iqtree   # ML search, ~6 min
make ls    RECIPE=iqtree   # iqout.treefile + smoke-check.txt

iqtree3 -s pfam38.2_seed_alignment.fa -m LG+G4 -T 4 --seed 12345 --prefix iqout
```

## Shape, size, cost

One task on `c8g.xlarge`, 4 threads: **447 s billed**, about $0.02. The companion
[RAxML-NG](../raxml-ng/README.md) run carries the generation table for this pair — 2.04× from
Graviton2 to Graviton5, with Graviton4 oddly dearer per search than Graviton3.

## Make it yours

| In the recipe | Swap for | What to know |
|---|---|---|
| Pfam PF26127.1 (114 × 477) | your alignment | 114 taxa is a real tree; cost grows with taxa × sites × rate categories. |
| `-T 4` | your core count | **not** `AUTO`: thread count changes the order of likelihood updates and therefore which optimum the search lands on. Pin it, with `--seed`. |
| `-m LG+G4` | your model, or `-m MFP` to select one | changes the likelihood, so the RAxML-NG agreement only holds at matched models. |

**Leave the workload** — a curated 114-taxon alignment, so the timing and the agreement transfer.
**Scale it** by taxa, and re-pin threads and seed before quoting any exact number.

<details>
<summary>As shipped: why the likelihood is a band, what is asserted instead, pins</summary>

### The checks

| observable | assertion | observed |
|---|---|---|
| taxa in alignment / tips in tree | == 114, both | **114 / 114** |
| alignment columns | exactly 477 | **477** |
| Newick terminated | ends `;` | **yes** |
| completion footer written | exactly 1 | **1** |
| log-likelihood | −54000..−52000 | **−52706.731** |
| **vs RAxML-NG** | **< 0.01 absolute** | **0.000409** |

### Why the likelihood is only a band

An ML search is stochastic: the thread count changes the order of likelihood updates and therefore
which local optimum the heuristic reaches, so an exact assertion on the value goes flaky across
thread counts and versions even with `--seed` fixed — the
[stochastic-search rule](../../practices/cross-checks.md). So the recipe pins `-T 4` and `--seed
12345` to make the run reproducible, asserts a wide band on the value, and puts the tight claim
where it belongs: on the **agreement with a second, unrelated code**.

That agreement is 0.000409 absolute, 7.8e-9 relative, against a tolerance of 0.01 set by this tool's
own printed precision — IQ-TREE reports three decimals, so ±0.0005 is inherent. Asserting 1e-6 would
be asserting a print format. Full comparison:
[measurements/tree-crosscheck](../../measurements/tree-crosscheck/README.md).

The trees themselves are **not** asserted equal. Independent searches can reach the same likelihood
by slightly different topologies, and a sibling measurement saw Robinson-Foulds distances of 26
versus 4 on the same alignments from search stochasticity alone.

### Pins

| | data tier |
|---|---|
| IQ-TREE | `quay.io/aarchbio/iqtree@sha256:dc6d9f62…` (3.1.3, `linux/arm64`) |
| alignment | Pfam 38.2 `PF26127.1` (`12TM_Mok13`) seed alignment — versioned release, staged here |

This recipe stages the alignment; [RAxML-NG](../raxml-ng/README.md) reads the same object rather than
a copy, because the agreement only means something on identical bytes.

### Run + verify

```sh
make run RECIPE=iqtree
make ls  RECIPE=iqtree
```

Expect `smoke-check.txt` with `taxa_in_alignment 114`, `alignment_columns 477` and
`best_log_likelihood -52706.731`.

</details>
