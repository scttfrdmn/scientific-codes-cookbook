---
tool: hmmer
tool_version: "3.4"
image: quay.io/aarchbio/hmmer@sha256:ecae1325123858761f0d744aacae948a89368440fd6d91f69c178402294fc792
spawn_version: 0.104.0
---
# HMMER — profile HMM search against a proteome

Score profile HMMs against a sequence database — how you find protein families, not just pairwise hits.

## Run it

```bash
hmmsearch --cpu 8 --tblout hits.tbl models.hmm proteome.fa
```

The recipe scores the first 200 Pfam 38.2 models against all 382,428 Ensembl 116 human proteins and writes the per-hit table.

## Make it yours

| In the recipe | Swap for | What to know |
|---|---|---|
| **first 200 of ~24,000 Pfam models** | all of `Pfam-A.hmm`, or your own profiles | a subsample — proves HMMER runs and produces real hits, **not** an annotation of the proteome. Searching all of Pfam is the same command, just longer. |
| the Ensembl 116 human proteome (shared with blast) | your own targets | `hmmsearch` can't read gzip — decompress first (the recipe does). |
| `--cpu 8` | scale to your cores | scaffolding that stays; scale freely, HMMER's result doesn't depend on thread count. |

`hmmsearch` is deterministic — same version, same inputs, same numbers — so **nothing here is determinism scaffolding**. **Leave the 200-model subsample:** a legible slice teaches "does HMMER run and score real families" as well as the full set would, and the models are cut cleanly (an HMMER3 model ends in a literal `//`, so truncating after the 200th yields a valid file). Scaling to all of Pfam is a longer run, not a more legible one.

## Shape, size, cost

One task. `c8g.2xlarge` (8 vCPU), TTL 20m, cap $0.13. Measured work **3m12s** on 4 threads; memory stays ~1–2 GiB, so the box is sized for cores, not footprint. Boot + pull still dominate ([why](../../practices/what-this-does-not-cover.md)).

<details>
<summary>As shipped: the completion sentinel, pins, smoke check</summary>

The **`[ok]` trailer** is the check that earns its place: `hmmsearch` writes a literal `[ok]` as its last line *only* on clean completion, so it catches a search killed part-way — which would otherwise leave a well-formed `tblout` with a partial answer and a hit count that could still land inside a band.

| observable | assertion | observed |
|---|---|---|
| models searched | exactly 200 | 200 |
| target sequences | exactly 382428 | 382428 |
| `tblout` hits | 40000–80000 | 59840 |
| models with ≥1 hit | 140–200 | 168 |
| hits at E < 1e-10 | 5000–20000 | 10050 |
| `hmmsearch` trailer | exactly `[ok]` | `[ok]` |

Counts are banded not because the run is nondeterministic (it isn't) but because a future HMMER release may legitimately shift them; the bands are wide enough for that, tight enough that an empty/truncated/wrong-database search fails. 32 of 200 models found no human hit — expected (many Pfam families are bacterial/viral/plant-specific).

**Pins.** Image `quay.io/aarchbio/hmmer@sha256:ecae13251238…` (3.4, cosign-signed, `linux/arm64` only). Models: Pfam `releases/Pfam38.2/Pfam-A.hmm.gz` first 200 (`sha256:c686564b…`; `stage-inputs.sh` checks `NAME`/`ACC` counts both = 200 so a mis-cut can't pass). Targets: Ensembl `release-116` proteome (`sha256:9b43da92…`, shared with [blast](../blast/README.md)). Both are immutable release paths — the mutable `current_*` siblings can't be pinned, so they don't qualify.

**Run + verify.**
```sh
make stage RECIPE=hmmer          # build models + proteome from Pfam/Ensembl (public)
make run RECIPE=hmmer
make ls RECIPE=hmmer
```
Smoke check runs inside the task; bucket listing is the second half ([exit 0 isn't proof](../../practices/container-path.md)). Re-run: `make run` launches a fresh task each time and overwrites this prefix — no spec edit needed.

</details>
