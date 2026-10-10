# Flye across four Graviton generations — apparatus

> **The results live on the [flye recipe page](../../recipes/flye/README.md).** This directory is
> the apparatus that produced them, not a second account of them.

The recipe's own `01-assemble.task.json` is the Graviton4 rung. The three specs here add the
others — `gen-c6g`, `gen-c7g`, `gen-c9g` — same reads, same image digest, 8 vCPU throughout, so
only the chip changes. Raw per-rung output is in `results/`.

**Why this is a pointer and not a write-up:** the page already carries the generation table, the
cost per assembly, and the sizing conclusion (tmpfs peaks well under Flye's own working set). A
measurement page restating them would be a second source of truth for the same numbers. What was
missing was any record of *how* to re-run the sweep, which is what these specs are.

One thing the specs encode that is easy to lose: Flye's contig count moves with thread count, so
every rung pins the same `-t`. Without that the generations are not comparable — a different
contig count is a different assembly, not a faster one.
