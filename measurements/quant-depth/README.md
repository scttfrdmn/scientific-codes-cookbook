# Rank is the stable metric; raw-value agreement is a choice you make

> **kallisto and salmon agree on abundance rank to 0.9083–0.9120 across a 79× change in sequencing
> depth.** Their raw-value correlation ranges 0.787 to 0.966 over the same data — moved entirely by
> depth and by which transcripts you include, not by the tools.

Same transcriptome (Ensembl 116 cDNA), same reads (ERR188026), same two indices, same metric code.
Depth and restriction set are the only variables.

| fragments | Spearman, both detected | log-Pearson, both detected | log-Pearson, all 465,769 | both detected |
|---|---|---|---|---|
| 200,000 | **0.9120** | 0.9118 | **0.7871** | 15,823 |
| 15,800,127 | **0.9083** | 0.9656 | 0.9214 | 87,169 |

## What this settles

**Use rank.** Spearman moves 0.004 across the depth change — it is answering a question both tools can
answer, and it keeps answering it when the experiment changes. That is the property that makes it
assertable in a recipe check.

**Raw-value correlation is not a property of the tools.** It is a property of the tools *plus* your
depth *plus* your inclusion rule, and over reasonable choices it spans 0.18. Quoting a single
raw-value number as "how well kallisto and salmon agree" states one cell of this table as if it were
the result.

**The two effects are separable and both real.** Holding the restriction fixed, going from 200k to
15.8M fragments moves raw agreement 0.9118 → 0.9656: low-depth estimates are noise-dominated, so two
different EM models diverge more. Holding depth fixed at 200k, dropping the restriction moves it
0.9118 → 0.7871: the transcripts only one tool detects disagree about *detection*, which is a
different question from *how much*.

## A number that did not reproduce

This repo previously recorded `raw log-TPM Pearson 0.61` for this pair. **None of the four variants
above reproduces it** — the closest is 0.7871 (200k fragments, unrestricted). The two factors measured
here both push in that direction without reaching it, so something else differed in the original: raw
rather than log TPM, a different read subset, or a different index are all candidates, and the
provenance is not recoverable from what was kept.

So the conclusion that rule drew — compare rank, not raw values — stands, and is better supported by
the stability above than it was by the single figure. The figure itself should not be requoted.

## Run it

```sh
export AWS_PROFILE=aws COOKBOOK_BUCKET=<your bucket>
make run RECIPE=salmon      # full-depth salmon table
make run RECIPE=kallisto    # full-depth kallisto table + its own cross-check
spawn task run --spec measurements/quant-depth/01-kallisto-sub.task.json --wait
spawn task run --spec measurements/quant-depth/02-salmon-sub-and-compare.task.json --wait
```

The subset is the first 200,000 pairs of the same FASTQs, cut with `awk 'NR<=800000'` rather than
`head` — `head` closes the pipe early, SIGPIPEs `zcat`, and under `pipefail` kills the task.

## Caveats

n = 1 per cell; both quantifiers are deterministic given fixed threads, so repetition buys nothing.

Two depths, one dataset, one organism. The *direction* of both effects should generalise — low depth
amplifies model differences, and including one-sided detections measures detection rather than
abundance — but the magnitudes are specific to this run.
