---
tool: seqkit
tool_version: 2.13.0
image: quay.io/aarchbio/seqkit@sha256:5478aaad4dd7bf7d7f02eee168ee3ad90d17b6729ab5e889a9385b4458cde7c5
spawn_version: 0.104.0
---

# seqkit — exact statistics over a FASTQ

The everyday first look at a read set: how many sequences, how long, what spread.

## Run it

```bash
seqkit stats reads_1.fq.gz reads_2.fq.gz
```

That's the recipe. seqkit is deterministic, so on a fixed input every number is exact — which is what lets the smoke check assert them to the digit rather than in a band.

## Make it yours

| In the recipe | Swap for | What to know |
|---|---|---|
| the 51,933-pair fixture reads (the shared 30× set) | your own FASTQ/FASTA, any size | `seqkit stats` doesn't care about size or format — point it at anything FASTX. |
| `stats` | `seq`, `grep`, `rmdup`, `fx2tab`, … | it's the same binary; this recipe just exercises the read path with a checkable output. |

Nothing here is determinism scaffolding — `seqkit stats` has no seed and no thread-order effect. **Leave the fixture small.** It's legible at any size and the numbers don't mislead; a 52k-pair set teaches exactly what a 52M-pair one would, faster and cheaper. Scaling it would prove nothing new.

## Shape, size, cost

One task, sub-second. `c8g.large`, ~$0.02, **~47s** wall — nearly all of it boot and image pull, not seqkit; [a short task is mostly overhead](../../practices/what-this-does-not-cover.md).

<details>
<summary>As shipped: exact identities, pins, smoke check</summary>

The check is a **conservation identity**, not two coincidental numbers: the `cat` of the two mates has `num_seqs` = 103,866 and `sum_len` = 15,579,900, and the smoke check asserts these equal `r1 + r2` (`103866 == 51933+51933`, `15579900 == 7789950+7789950`). Each mate is 51,933 seqs / 7,789,950 bp at a uniform 150 bp (min == max). Exact-or-wrong.

| observable | assertion | observed |
|---|---|---|
| r1, r2 num_seqs / sum_len | 51933 / 7789950 each | matches |
| combined num_seqs | 103866 (== r1+r2) | 103866 |
| combined sum_len | 15579900 (== r1+r2) | 15579900 |
| read length | uniform 150 (min == max) | 150 / 150 |

**Pins.** Image `quay.io/aarchbio/seqkit@sha256:5478aaad4dd7…` (2.13.0, cosign-verified, `linux/arm64`). Input: the 30× fixture's reads at `inputs/highcov/HG00096.chr20_2.0-2.4Mb.30x_reads_{1,2}.fq.gz` — `samtools fastq` of the shared BAM fixture (`make stage RECIPE=bcftools`), reused, not re-derived.

**Run + verify.**
```sh
make run RECIPE=seqkit
make ls RECIPE=seqkit   # expect stats.tsv, smoke-check.txt
```
The smoke check runs inside the task; the bucket listing is the second half ([exit 0 isn't proof](../../practices/container-path.md)). Re-running: bump the `-r1` suffix in `task_id` and the output prefix.

</details>
