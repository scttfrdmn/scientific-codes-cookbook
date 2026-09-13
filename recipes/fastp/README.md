---
tool: fastp
tool_version: 1.3.6
image: quay.io/aarchbio/fastp@sha256:061ee7c6b8e5af265dfed6f25c51e482e3bb403c51f167561405010e5c5f632a
spawn_version: 0.104.0
---
# fastp — read QC and trimming

Quality-filter and adapter/quality-trim paired reads, with an all-in-one JSON report.

## Run it

```bash
fastp -i reads_1.fq.gz -I reads_2.fq.gz -o out_1.fq.gz -O out_2.fq.gz -j fastp.json
```

The recipe QCs the **same 400k read pairs [bwa](../bwa-samtools/README.md) aligned** and verifies a **conservation identity** — every input read is accounted for as passed or filtered, mates stay paired, output count matches passed count.

## Make it yours

| In the recipe | Swap for | What to know |
|---|---|---|
| the 400k-pair HG00096 slice | your own reads | reused byte-for-byte from [bwa](../bwa-samtools/README.md) — nothing re-staged. |
| default QC + trimming | add `--dedup`, UMI, or overrepresentation flags | this recipe runs fastp's defaults; the conservation identity holds whatever filters you enable, but the exact component counts will change. |

fastp is deterministic on fixed input — **nothing here is determinism scaffolding**. **Leave the fixture:** 400k pairs run in ~2 s and the read bookkeeping is exact-or-wrong; a bigger sample is a longer run, not a more legible one. Leave-it.

## Shape, size, cost

One task, **~2 s** QC. `c8g.large`, ~$0.02, **~47s** wall — boot and image pull ([why](../../practices/what-this-does-not-cover.md)).

**Sizing:** no family question — fastp streams reads with a bounded footprint; a bigger sample is a longer run, not a heavier box. Any 8g box fits.

<details>
<summary>As shipped: why a conservation identity not a cross-check, pins, smoke check</summary>

fastp is a single-tool QC step with no natural sibling to cross-validate on the same bytes, so the honest strongest claim is a **conservation identity** — the same class as salmon's TPM sum: fastp's report must balance its books. `reads_before = passed + low_quality + too_many_N + too_short + too_long`, exactly, and the two output FASTQs carry equal read counts (paired-end integrity) summing to the passed count. Deterministic on fixed input, so every number is asserted exactly — a truncated or mis-split output fails the arithmetic even though the run exits 0.

| observable | assertion | observed |
|---|---|---|
| reads_before | exactly 800000 (400k × 2) | 800000 |
| **accounted_for** | passed + low_quality + too_many_N + too_short + too_long == reads_before | 800000 |
| out1 == out2 | paired mates stay paired | 374058 = 374058 |
| out1 + out2 | == passed_filter | 748116 |

Components (folded into the sum): passed 748116, low_quality 51656, too_many_N 228, too_short 0, too_long 0.

**Pins.** Image `quay.io/aarchbio/fastp@sha256:061ee7c6b8e5…` (1.3.6, cosign-verified, `linux/arm64`). Reads: ENA `SRR062634` (HG00096, 1000G) first 400k pairs (`sha256:4bd24cd…` / `sha256:ebd1ad5…`) — reused from [bwa](../bwa-samtools/README.md); run its `stage-inputs.sh` first.

**Run + verify.**
```sh
make stage RECIPE=bwa-samtools   # fastp reuses bwa's reads
make run RECIPE=fastp
make ls RECIPE=fastp   # expect out_1.fq.gz, out_2.fq.gz, fastp.json, smoke-check.txt
```
Smoke check runs inside the task; bucket listing is the second half ([exit 0 isn't proof](../../practices/container-path.md)). Re-run: `make run` launches a fresh task each time and overwrites this prefix — no spec edit needed.

**Fan out across samples.** One QC run is one task; a cohort is the same task as a [job array](../../patterns/job-arrays.md) — validate on one sample with `make run` above, *then* fan out one instance per sample, each keyed by `$JOB_ARRAY_INDEX`. `spawn array status` / `collect` / `retry --failed` manage the set; add `--max-concurrent-auto` when a shared reference or spot capacity pushes back.

</details>
