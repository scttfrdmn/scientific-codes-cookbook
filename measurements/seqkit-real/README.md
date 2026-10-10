# seqkit across four Graviton generations — apparatus

> **The results live on the [seqkit recipe page](../../recipes/seqkit/README.md).** This directory
> is the apparatus that produced them, not a second account of them.

The recipe's own `01-stats.task.json` is the Graviton4 rung; `gen-c6g`, `gen-c7g` and `gen-c9g`
add the others — same FASTQ pair, same image digest, same `2xlarge` shape, so only the chip
changes. Raw per-rung output in `results/`.

**Why this is a pointer and not a write-up:** the page already carries the table and the
conclusion. What did not exist anywhere was a way to re-run the sweep.

Worth knowing about these specs: the counts they assert are not local to seqkit. `total_num_seqs`
must equal bwa's primary record count and fastp's `before_filtering.total_reads` on the same run,
so a rung that silently read a truncated input fails against two other recipes rather than
against a band of its own choosing.
