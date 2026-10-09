---
tool: bowtie2
tool_version: 2.5.5
image: quay.io/aarchbio/bowtie2@sha256:a6807f0611a1c276235f47d175471ebbaec863aa751a0c3771c324be57d8fc59
spawn_version: 0.111.4
last_verified: 2026-10-01
---
# Bowtie 2 — short-read alignment, cross-checked against bwa

Build an index, align paired reads — checked against bwa on identical bytes.

## Run it

```bash
spawn task run --spec "$(make -s spec RECIPE=bowtie2)" --wait
bowtie2-build ref.fa idx
bowtie2 --local -x idx -1 reads_1.fq.gz -2 reads_2.fq.gz -S aln.sam
```

The recipe aligns the **same 400k read pairs bwa aligned** to the same chr20, then checks a conservation identity plus mapped-set agreement with bwa.

## Make it yours

| In the recipe | Swap for | What to know |
|---|---|---|
| the 400k-pair HG00096 slice + chr20 (bwa's exact inputs) | your own reads + reference | reused byte-for-byte from [bwa](../bwa-samtools/README.md) so the cross-check is valid — nothing re-staged. |
| **`--local`** | the default end-to-end | **load-bearing for the cross-check** — bwa soft-clips (local); Bowtie 2's default is end-to-end, so comparing them compares *modes*: default maps 11.25% / agrees with bwa 82%, `--local` (the apples-to-apples match) 25.77% / 94.6%. |

Bowtie 2 is deterministic — **nothing here is determinism scaffolding**. **Leave the chr20 fixture:** mapping rate is low (~26%) *by design* (most reads have no chr20 home), but the claim is cross-aligner *concordance*, which is honest at this scale; it's not a mapping-rate recipe (that's [STAR's](../star/README.md) caveat). Leave-it.

## Shape, size, cost

One task; `bowtie2-build` 35 s + `--local` align 35 s. `c8g.xlarge`, ~$0.02, **~114s** wall — boot and image pull ([why](../../practices/what-this-does-not-cover.md)). Reuses [bwa](../bwa-samtools/README.md)'s staged inputs — run that first.

**Sizing:** compute-bound `c8g`, but Bowtie 2 loads the FM-index into RAM — ~3.5 GiB for a human genome, so size RAM to your reference (chr20 here is tiny).

**Don't go looking for a core knee — it was measured and there isn't one.** On a full 1000 Genomes run against the whole GRCh38 index, bowtie2 went **4.00× faster on 4× the cores at 100.1% parallel efficiency**, and because `c8g`'s per-core price is flat the compute-only cost of the result is *invariant*: **$0.1730–$0.1737 at 16, 32, 48 and 64 cores**. Pick cores for wall-clock, not for cost. Across generations, **Gv2 → Gv5 is 1536 s → 746 s** and every rung is cheaper per result ([measurement](../../measurements/bowtie2-real/README.md)).

<details>
<summary>As shipped: the like-with-like cross-check, pins, smoke check</summary>

The `--local` choice is a finding worth keeping ([compare like with like](../../practices/cross-checks.md)): comparing bwa's local alignment to Bowtie 2's default end-to-end would fail for a reason unrelated to correctness — it rejects exactly the reads bwa soft-clips. `--local` is the match; the residual ~5% disagreement is reads at the local-score threshold where two scoring schemes legitimately differ — a *method-limited* tolerance, not one picked to pass. **Position** agreement is deliberately not asserted: on a repeat-heavy chr20 slice both aligners find different equally-valid placements (~32% leftmost concordance, says nothing about correctness). The mapped-**set** concordance is the honest identity.

| observable | assertion | observed |
|---|---|---|
| primary records | exactly 800000 (400k×2) — conservation | 800000 |
| primary mapped | 150000–260000 | 206155 |
| overall alignment rate (`--local`) | 22–29% | 25.77% |
| concordance vs bwa (mapped-set) | ≥ 0.90 | **0.9461** |

Bowtie 2 is deterministic, so counts reproduce exactly; bands exist only to survive a version change, and the concordance floor is method-justified — none can go flaky.

**Pins.** Image `quay.io/aarchbio/bowtie2@sha256:a6807f0611a1…` (2.5.5, cosign-signed, `linux/arm64`). Reads + reference are [bwa](../bwa-samtools/README.md)'s pinned `inputs/bwa-samtools/` bytes (chr20 slice + 400k-pair HG00096 from the 1000G RODA mirror); cross-check against `runs/bwa-samtools/r1/aln_chr20smoke.sam`, which bwa-samtools' `03-baseline-chr20.task.json` exists to produce for exactly this comparison — borrowing that recipe's *main* output broke the moment it moved to the whole genome. Reused — no `stage-inputs.sh`.

**Run + verify.**
```sh
make stage RECIPE=bwa-samtools   # bowtie2 reuses bwa's chr20 fixture
make run   RECIPE=bwa-samtools   # includes the chr20 baseline this compares against
make run RECIPE=bowtie2
make ls RECIPE=bowtie2   # expect smoke-check.txt, align.log, build.log
```
Re-run: `make run` launches a fresh task each time and overwrites this prefix — no spec edit needed.

</details>
