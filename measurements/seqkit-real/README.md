# seqkit across four Graviton generations — apparatus

> **The results live on the [seqkit recipe page](../../recipes/seqkit/README.md).** This directory
> is the apparatus that produced them, not a second account of them.

The recipe's own `01-stats.task.json` is the Graviton4 rung; `gen-c6g`, `gen-c7g` and `gen-c9g`
add the others — same FASTQ pair, same image digest, same `2xlarge` shape, so only the chip
changes. Raw per-rung output in `results/`.

**The 8th-generation x86 legs** (2026-10-21) are the same `01-stats.task.json` through
`scripts/spec_arch.py seqkit {x86_64,amd} <bucket> real recipes/seqkit/01-stats.task.json`, with the
outputs moved to `runs-{x86_64,amd}/seqkit/gen/{c8i,c8a}/` and the inline script staged as a
sha256-checked file (spawn 0.139.1's user-data budget). bioconda's x86 seqkit 2.13.0 on
`c8i.2xlarge` (Xeon 6975P-C, 4 cores × 2 threads) and `c8a.2xlarge` (EPYC 9R45, 8 cores × 1):

| chip | instance | `stats` + `fq2fa` | billed (spawn task window) | $/hr | compute $ | billed $ |
|---|---|---|---|---|---|---|
| Graviton4 | `c8g.2xlarge` | 93 s | 137 s | 0.3190 | 0.0082 | 0.0121 |
| Intel | `c8i.2xlarge` | 86 s | 127 s | 0.3748 | 0.0090 | 0.0132 |
| AMD | `c8a.2xlarge` | **72 s** | 106 s | 0.4311 | 0.0086 | 0.0127 |

Every assertion passed on both (`SMOKE CHECK PASSED`; `results/smoke-check-c8{i,a}-2xl.txt`), and
`stats.tsv` and `fa_stats.tsv` are byte-identical to Graviton4's on both — integer counts, so a
fixed point; only the wall time and nothing else differs. The tmpfs high-water mark is the same
5,612 MB. n = 1 per chip; seqkit is deterministic, so the counts are exact, while the 14 s
Intel–AMD gap is one draw. Spend for the two legs: $0.026 billed
(spawn's windows, wrapper start to stage-out done, [`results/billed-windows.txt`](results/billed-windows.txt)).

**Why this is a pointer and not a write-up:** the page already carries the table and the
conclusion. What did not exist anywhere was a way to re-run the sweep.

Worth knowing about these specs: the counts they assert are not local to seqkit. `total_num_seqs`
must equal bwa's primary record count and fastp's `before_filtering.total_reads` on the same run,
so a rung that silently read a truncated input fails against two other recipes rather than
against a band of its own choosing.
