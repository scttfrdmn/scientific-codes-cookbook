---
tool: raxml-ng
tool_version: 2.0.2
image: quay.io/aarchbio/raxml-ng@sha256:3a6bbc162ff43249ada42bd92828ac0024855c33b614c0cdbaaa1e3e3eed89e5
spawn_version: 0.104.0
---
# RAxML-NG — maximum-likelihood phylogeny

Infer an ML tree from a multiple-sequence alignment, with an adaptive search that sizes itself to the data.

## Run it

```bash
raxml-ng --search --msa alignment.fasta --model LG+G4 --threads 8 --seed 12345
```

The recipe infers a 114-taxon protein tree under LG+G4, reaching the **same optimum as [IQ-TREE](../iqtree/README.md)** on the same alignment. RAxML-NG 2.0's `--search` sizes its own starting-tree set from a difficulty prediction — no flag needed.

## Make it yours

| In the recipe | Swap for | What to know |
|---|---|---|
| the 114-sequence Pfam seed alignment | your own alignment (FASTA) | reused byte-for-byte from [IQ-TREE](../iqtree/README.md) (the spec reads `inputs/iqtree/`, no `stage-inputs.sh`) — the cross-check only means anything on identical bytes, so it isn't re-staged or re-converted. |
| `--model LG+G4` | your model | given explicitly to remove a drift source and because it's the model IQ-TREE was given. |
| the adaptive `--search` default | `--tree pars{n},rand{n}` for a fixed search | the adaptive default is what's worth demonstrating, and it earned it here — the best tree came from a *random* start a parsimony-only search would have missed. |
| **`--threads 8` and `--seed 12345`** | pin *both* for a repeatable run | **determinism scaffolding** — thread count changes how many starting trees the adaptive search generates, so `AUTO` isn't reproducible. Pin both or assert a band ([pin threads and a seed](../../practices/cross-checks.md)). |

**Leave the fixture:** 114 taxa × 477 sites is a real ML inference that runs in minutes and lets the check assert tree shape exactly. It does *not* exercise the MPI/`--workers` path or a phylogenomic dataset's memory behaviour — a larger alignment is a longer run, not a more legible one. Leave-it.

## Shape, size, cost

One task, **7m39s** of actual search — the rare recipe where the science dominates boot overhead by an order of magnitude (7m39s inside an 8m25s command window). RAxML-NG's own `--parse` estimates **65 MB** RAM, so `c8g.2xlarge` is sized for the thread count, not footprint. TTL **20m**, cap $0.11 — retightened from the first real run (below).

<details>
<summary>As shipped: the cross-validation, the one deliberately-loose band, pins, smoke check</summary>

**Cross-validation.** RAxML-NG reports `-52706.731409`; [IQ-TREE](../iqtree/README.md), a completely different codebase with a different search heuristic, reaches `-52706.731` on the same alignment under the same LG+G4 model. Two unrelated codes agreeing to ~1 part in 1e8 of the log-likelihood is far stronger evidence of the real ML optimum than either run alone — and free, because the two recipes share the pinned alignment.

Most of the smoke check is arithmetic, not observation, so it costs nothing and cannot go flaky:

| observable | assertion | observed |
|---|---|---|
| taxa / tips / sites | 114 / 114 / 477 (exact) | matches |
| distinct site patterns | exactly 465, ≤ sites | 465 |
| free parameters | == 2n−2 | 226 |
| AIC / AICc / BIC | closed forms of lnL, k, n (≤1e-4) | 105865.462818 / 106275.878818 / 106807.321545 |
| final lnL | == max(per-search lnL) | −52706.731409 |
| ML / starting trees written | == searches reported | 13 == 13 |
| analysis finished / Newick terminated | 1 / `;` | 1 / `;` |
| best log-likelihood | −54000 … −52000 (wide, deliberate) | **−52706.731409** |

- **`free parameters == 2n−2`**: an unrooted binary tree on *n* taxa has 2n−3 branches, LG+G4 adds exactly the gamma shape α, so k must be 226 — any other value means a wrong taxon count or model.
- **The three information criteria** are closed forms RAxML-NG prints on adjacent lines; they agree to floating-point exactness (largest residual 5.9e−08), catching a garbled log or mismatched lnL/k pair for free.
- **`final lnL == max(per-search)`** is what `--search` *means*; the margin is comfortable (second best −52707.13), so it's not a tie-break coin flip (contrast [blast](../blast/README.md)'s self-hit check, which failed 19-of-20 on exactly that).
- **One ML + one starting tree per search**, stated as an identity not `== 13` on purpose: 13 is a *prediction* of the adaptive heuristic, so pinning the literal would turn a legitimate difficulty re-estimate into a red check.
- **`Analysis finished`** is a completion sentinel — a search killed part-way leaves per-search lines and tree files that would still look plausible to a count.

**The log-likelihood band is the only band, and a tight one would be wrong.** Search is heuristic: `--seed` repeats a run against itself, but the result moves with RAxML-NG version and thread count (which changes both the difficulty prediction and the update order). The band catches a tree built from garbage — the best random start scored −76,542, so a mis-parsed alignment lands nowhere near it. It's deliberately the **same** band [IQ-TREE](../iqtree/README.md) uses, so the two are directly comparable. No `python3` in this image (one tool per image), so every check is `awk`/`grep`.

**Graviton4 was 2.17× faster** than the local measurement on identical work (7m39s vs 16m35s Docker Desktop, same predicted difficulty, same starting trees, same `-52706.731409`). The TTL was sized from the local run then **retightened from the box run**: 35m → 20m, cost cap $0.19 → $0.11, and `--dry-run` echoes both back — which is how the field is confirmed [honored, not merely parsed](../../practices/container-path.md). The general lesson: sizing from a local run is the right *first* move because it's free; the first real run is better evidence and should be spent. (The recorded run below used the original 35m/$0.19.)

**Pins.** Image `quay.io/aarchbio/raxml-ng@sha256:3a6bbc162ff4…` (2.0.2, cosign-signed, `linux/arm64` only). Alignment: first 40–150-seq family in Pfam `releases/Pfam38.2/Pfam-A.seed.gz`, converted to FASTA (`sha256:b4d4d745…`, 114×477) — *derived*, shared with [IQ-TREE](../iqtree/README.md); `releases/Pfam38.2/` is immutable (`current_release/` is not).

**Run + verify.**
```sh
make stage RECIPE=iqtree          # once, if the shared alignment isn't staged
make run RECIPE=raxml-ng
make ls RECIPE=raxml-ng   # expect six objects
```
Smoke check runs inside the task; bucket listing is the second half ([exit 0 isn't proof](../../practices/container-path.md)). `--redo` is in the command, so a re-run doesn't trip the checkpoint guard. Re-run: `make run` launches a fresh task each time and overwrites this prefix — no spec edit needed.

</details>
