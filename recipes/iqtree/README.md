---
tool: iqtree
tool_version: 3.1.3
image: quay.io/aarchbio/iqtree@sha256:dc6d9f62d56fd1ca92bfb2a9fbd162d419d4f866de67e6e879ba0f895d2a6fb7
spawn_version: 0.104.0
---
# IQ-TREE — maximum-likelihood phylogeny

Infer an ML tree from a multiple-sequence alignment under a chosen substitution model.

## Run it

```bash
iqtree3 -s alignment.fasta -m LG+G4 -T 4 --seed 12345
```

The recipe infers a 114-taxon protein tree under LG+G4 and writes the Newick tree plus a likelihood report. It reaches the **same optimum as [RAxML-NG](../raxml-ng/README.md)** on the same alignment — two unrelated ML codes agreeing to ~1 part in 1e8.

## Make it yours

| In the recipe | Swap for | What to know |
|---|---|---|
| the 114-sequence Pfam seed alignment | your own alignment (FASTA) | derived from Pfam 38.2 by a deterministic rule (first family with 40–150 seqs), so it's a durable, pinnable input rather than the package's fragile test file. |
| `-m LG+G4` | your model, or let ModelFinder pick | given explicitly here to remove a source of run-to-run drift; for real work choose the model your data wants. |
| **`-T 4` and `--seed 12345`** | pin *both* for a repeatable run | **determinism scaffolding.** The search is heuristic: `--seed` repeats a run against itself, but thread count changes which local optimum it lands in, so `-T AUTO` isn't reproducible. Pin both or assert a band ([pin threads and a seed](../../practices/cross-checks.md)). |

**Leave the fixture:** a curated 114-taxon alignment is a real ML inference that runs in minutes and lets the check assert tree shape exactly; a larger alignment is a longer run, not a more legible one. Leave-it.

## Shape, size, cost

One task, **7m3s** of actual search (26m39s CPU on 4 threads) — the rare recipe where the science outweighs boot overhead. IQ-TREE needed only **34 MB** RAM, so `c8g.xlarge` is sized for cores, not footprint. TTL **35m** (heuristic search time isn't perfectly predictable, and a TTL hit is a failure), cap $0.11.

<details>
<summary>As shipped: the deliberately-loose band, the cross-validation, pins, smoke check</summary>

Shape assertions are exact (from the input + Newick); the **log-likelihood band is loose on purpose**. A bound of a few log units would fail for reasons unrelated to correctness — the result moves with IQ-TREE version and thread count. The band's job is to catch a tree built from garbage (a mis-parsed alignment lands nowhere near −52700); for scale, the starting RapidNJ tree scored −52918.8 and the search improved it to −52706.7. Pinning `-T 4` and `-m LG+G4` removes two drift sources, so if this number *does* move later, that's signal.

| observable | assertion | observed |
|---|---|---|
| taxa / tips / columns | 114 / 114 / 477 (exact) | matches |
| best log-likelihood | −54000 … −52000 (wide, deliberate) | −52706.731 |
| Newick terminated / footer written | `;` / 1 | `;` / 1 |

**Cross-validation:** [RAxML-NG](../raxml-ng/README.md), a completely different ML codebase with a different search heuristic, independently reaches −52706.731409 on the same alignment — agreement to ~1e-8, a far stronger statement than either tool's self-report, and free because the two recipes share the pinned alignment (see that recipe for the shared-bytes discipline).

**Pins.** Image `quay.io/aarchbio/iqtree@sha256:dc6d9f62d56f…` (3.1.3, cosign-signed, `linux/arm64` only). Alignment: first 40–150-seq family in Pfam `releases/Pfam38.2/Pfam-A.seed.gz`, converted to FASTA (`sha256:b4d4d745…`, 114×477) — *derived*, so the sha256 pins our conversion (which is why the selection rule is deterministic and the converter's output is re-checked in bash). `release/Pfam38.2/` is immutable; shared with [hmmer](../hmmer/README.md).

**Run + verify.**
```sh
spawn task run --spec recipes/iqtree/01-tree.task.json --wait
aws s3 ls s3://scicookbook-942542972736-us-east-1/runs/iqtree/r1/
```
Smoke check runs inside the task; bucket listing is the second half ([exit 0 isn't proof](../../practices/container-path.md)). `-redo` is in the command, so a re-run doesn't trip the checkpoint guard. Re-running: bump the `-r1` suffix.

</details>
