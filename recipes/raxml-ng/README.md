---
tool: raxml-ng
tool_version: 2.0.2
image: quay.io/aarchbio/raxml-ng@sha256:3a6bbc162ff43249ada42bd92828ac0024855c33b614c0cdbaaa1e3e3eed89e5
last_verified_x86_64: 2026-10-13
last_verified_amd: 2026-10-18
images_x86_64:
  - quay.io/biocontainers/raxml-ng@sha256:e27292d42400cb03d3f8e070836e7048a5f9bf3ff68bd979c19e883b426f2bb0
spawn_version: 0.111.4
last_verified: 2026-10-01
---
# RAxML-NG — an ML phylogeny, and the same optimum IQ-TREE finds

Infers a maximum-likelihood tree for a 114-taxon Pfam alignment under LG+G4, landing on −52706.731409 — a value [IQ-TREE reaches independently](../iqtree/README.md) to 1 part in 1e8. For anyone building trees on ARM.

> **The optimum is identical on four Graviton generations, and on Graviton4, Intel and AMD at 919 taxa.** Fixed seed plus pinned threads make the search deterministic; the chip changes only how long it takes.

## Run it

```bash
make stage RECIPE=iqtree     # shares the Pfam seed alignment
spawn task run --spec "$(make -s spec RECIPE=raxml-ng)" --wait   # adaptive ML search, ~7.6 min
make ls    RECIPE=raxml-ng   # rx.raxml.bestTree + smoke-check.txt

raxml-ng --search --msa pfam38.2_seed_alignment.fa --model LG+G4 \
  --threads 8 --workers 1 --seed 12345 --prefix rx
```

## Which box — measured (same seed, 8 threads, `2xlarge`)

On a [whole 919-taxon gene family](../../measurements/raxml-ng-real/README.md), ML search + 20 bootstraps:

| chip | instance | search + 20 bootstraps | **$/result** |
|---|---|---|---|
| Graviton4 | `c8g.2xlarge` | 30,812 s | 2.74 |
| Xeon 6975P-C | `c8i.2xlarge` | 24,394 s | 2.55 |
| **EPYC 9R45** | `c8a.2xlarge` | **9,361 s** | **1.13** |

**Take AMD:** 3.3× Graviton4's speed for 59% less, and the best tree is byte-identical on all three chips. The x86 build runs the `AVX2` kernel and the arm64 build runs `SSE3`. A bootstrap replicate costs $0.031 on AMD and $0.073 on Graviton4. Graviton generations on the fixture are below the fold.

## Make it yours

| In the recipe | Swap for | What to know |
|---|---|---|
| Pfam PF26127.1 (114 × 477) | your alignment | cost grows with taxa × sites × rate categories; 114 taxa is a real tree, not a fixture. |
| `--threads 8 --workers 1` | your core count | **not** `AUTO`: thread count feeds the adaptive heuristic's starting trees, so it changes which optimum you land on. Pin it, with `--seed`. |
| `--model LG+G4` | your model | changes the likelihood, so the cross-check with IQ-TREE only holds at matched models. |
| no bootstrap | `--bootstrap` + `--support` | in 2.0.2 the TBE file holds FBP ÷ 100 on every branch ([measured](../../measurements/raxml-ng-real/README.md)). Recompute TBE elsewhere. |

**Leave the workload** — a curated 114-taxon alignment, so the timings and the agreement transfer.
**Scale it** by taxa, and re-pin threads and seed before quoting any exact number.

<details>
<summary>As shipped: internal identities, the cross-code agreement, pins</summary>

### Across Graviton generations, on the fixture

| generation | instance | search | **$/search** | log-likelihood |
|---|---|---|---|---|
| Graviton2 | `c6g.2xlarge` | 638.1 s | 0.0482 | −52706.731409 |
| Graviton3 | `c7g.2xlarge` | 465.0 s | **0.0375** | −52706.731409 |
| Graviton4 | `c8g.2xlarge` | 456.0 s | 0.0404 | −52706.731409 |
| **Graviton5** | `c9g.2xlarge` | **313.0 s** | **0.0303** | −52706.731409 |

Graviton2→5 is **2.04× faster and 37% cheaper**. But **Graviton4 is 7.7% *dearer* than Graviton3** here — it buys only 2% of wall for 10% more per hour. That is the sharpest instance of a step that does not pay; [three other codes show the same thing](../../patterns/cost-per-result.md).

### The checks

| observable | assertion | observed |
|---|---|---|
| taxa read / tips in tree | == 114, both | **114 / 114** |
| alignment sites | exactly 477 | **477** |
| distinct site patterns | exactly 465, ≤ 477 | **465** |
| free parameters | exactly 226 (= 2n−3 branches + α) | **226** |
| **AIC** | **2k − 2lnL** | **105865.462818** |
| **AICc** | **AIC + 2k(k+1)/(n−k−1)** | **106275.878818** |
| **BIC** | **k·ln(n) − 2lnL** | **106807.321545** |
| final == best of searches | exact | **−52706.731409 of 13** |
| completion sentinel | `finished:` line present | **present** |
| **vs IQ-TREE** | **< 0.01 absolute** | **0.000409** |

The three information criteria are **conservation identities**, not bands: each is a fixed function of
the likelihood, the free-parameter count and the sample size, so recomputing them and comparing
catches a corrupted likelihood or a miscounted model that any range check would wave through. They
cost nothing.

### Why the likelihood is a band and the agreement is not

`best_log_likelihood` is asserted only as −54000..−52000, deliberately. An ML search is stochastic:
thread count changes the order of likelihood updates and therefore which optimum it lands on, so an
exact assertion is flaky across thread counts and versions even with a fixed seed — the
[stochastic-search rule](../../practices/cross-checks.md).

What *can* be asserted tightly is the **cross-code agreement**, because two unrelated search
heuristics landing on the same optimum is a statement about the numerics rather than about either
search. The tolerance is 0.01 absolute, set by IQ-TREE's printed precision — it reports 3 decimals,
so ±0.0005 of rounding is inherent. Asserting 1e-6 would be asserting a print format. Observed
difference 0.000409, or 7.8e-9 relative. Full comparison:
[measurements/tree-crosscheck](../../measurements/tree-crosscheck/README.md).

And the optimum came out **identical to six decimals on all four Graviton generations**, which is
what pinning threads and the seed buys: the search is deterministic, so the chip cannot move it.

### Pins

| | data tier |
|---|---|
| RAxML-NG | `quay.io/aarchbio/raxml-ng@sha256:3a6bbc16…` (2.0.2, `linux/arm64`) |
| alignment | Pfam 38.2 `PF26127.1` seed alignment — staged by [iqtree](../iqtree/README.md), versioned release |

Both tools read the same staged object; a second copy would be a second thing to keep true, and the
agreement only means something on identical bytes.

### On x86_64

The same task on same-generation Intel (`c8i.2xlarge`), from bioconda's own x86 build of the
same version — `quay.io/biocontainers/raxml-ng@sha256:e27292d4…` (2.0.2, `linux/amd64`; not
cosign-signed, so the digest is the pin). Same staged alignment, same `--threads 8 --seed 12345`:

| | arm64 | x86_64 |
|---|---|---|
| searches / best of them | 13 / −52706.731409 | 13 / −52706.731409 |
| AIC / AICc / BIC | 105865.462818 / 106275.878818 / 106807.321545 | identical |
| `bestTree`, `mlTrees`, `startTree`, `bestModel`, `smoke-check.txt` | — | **byte-identical** to arm64 |

Not only the optimum: every intermediate likelihood the log prints — each SPR round of all 13
searches — is the same to six decimals, although the two builds run different SIMD likelihood
kernels (`SSE3` on arm64, `AVX2` on x86). The `.log` differs only in timestamps, elapsed time, the
CPU banner and that kernel line.

### Run + verify

```sh
make run RECIPE=raxml-ng                  # Graviton4
make run RECIPE=raxml-ng ARCH=x86_64      # same spec, x86 pin, writes runs-x86_64/
make ls  RECIPE=raxml-ng
```

Expect `smoke-check.txt` with `best_log_likelihood -52706.731409`, `free_parameters 226` and all
three information criteria matching their identities.

</details>
