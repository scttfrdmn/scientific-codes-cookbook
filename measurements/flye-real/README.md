# Flye across four Graviton generations — apparatus

> **The results live on the [flye recipe page](../../recipes/flye/README.md).** This directory is
> the apparatus that produced them, not a second account of them.

The recipe's own `01-assemble.task.json` is the Graviton4 rung. The three specs here add the
others — `gen-c6g`, `gen-c7g`, `gen-c9g` — same reads, same image digest, 8 vCPU throughout, so
only the chip changes. [`chip.task.json`](chip.task.json) adds same-generation Intel and AMD: the
recipe's spec (its exact-assembly assertions) plus a topology record and its own output prefix,
resolved by `python3 scripts/spec_arch.py flye x86_64|amd <bucket> <nonce>
measurements/flye-real/chip.task.json` onto bioconda's `linux/amd64` build of 2.9.6. On spawn
0.139.1 it exceeds EC2's 16 KiB user data (spawn#1063/#1246), so it ran with the inline command
moved, byte for byte, into a staged sha256-checked file. Both passed every assertion, and
`assembly.fasta` is byte-identical to Graviton4's (sha256 `0451707a…`), as it is on every
generation rung: a fixed point. 8 vCPU is 4 cores × 2 hyperthreads on `c8i` and 8 cores on `c8a`
(`lscpu`). Spend for the two legs: $0.083 billed. Raw per-rung output is in `results/`.

**Why this is a pointer and not a write-up:** the page already carries the generation table, the
cost per assembly, and the sizing conclusion (tmpfs peaks well under Flye's own working set). A
measurement page restating them would be a second source of truth for the same numbers. What was
missing was any record of *how* to re-run the sweep, which is what these specs are.

One thing the specs encode that is easy to lose: Flye's contig count moves with thread count, so
every rung pins the same `-t`. Without that the generations are not comparable — a different
contig count is a different assembly, not a faster one.
