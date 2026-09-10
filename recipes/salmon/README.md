---
tool: salmon
image: quay.io/aarchbio/salmon@sha256:7134f5116644d29ab5b8fbc1c1199214842d7391094438ba6166e5631ecb7a5e
spawn_version: 0.104.0
---
# salmon — RNA-seq transcript quantification

Quantify transcript abundance from RNA-seq reads against the human transcriptome — a mapping-based quantifier, cross-checked against [kallisto](../kallisto/README.md).

## Run it

```bash
salmon index -t transcriptome.fa.gz -i idx
salmon quant -i idx -l A -1 reads_1.fq.gz -2 reads_2.fq.gz -o out
```

Two tasks: `salmon index` builds a 1.6 GiB index over the Ensembl-116 human transcriptome; `salmon quant` maps 200k read pairs against it. Split because the index is the expensive, reusable artifact — [one tool per image](../../practices/container-path.md) makes each its own task, and the split lets `quant` re-run without rebuilding.

## Make it yours

| In the recipe | Swap for | What to know |
|---|---|---|
| Ensembl-116 transcriptome + 200k ERR188026 pairs | your transcriptome + reads | the index task is reusable — every sample against Ensembl-116 wants the same index, which is why it's split out. |
| `-l A` (auto-detect library type) | an explicit `-l` (e.g. `ISR`) | `A` infers strandedness; pin it if you know it. |
| the 200k-pair subsample | your full-depth reads | subsampled for a fast demo, not because salmon wants small input. |

Nothing is determinism scaffolding — salmon reports this config `deterministic` and reproduced every count across runs. **Leave the fixture:** a real transcriptome with a small sample exercises the index build and gives a real cross-code agreement with [kallisto](../kallisto/README.md); full depth is a longer run, not a more legible one. Leave-it.

## Shape, size, cost

Two tasks, `c8g.2xlarge` (index peak 2.3 GB RSS — compute-family, not memory-bound), TTL 20m, cap $0.13. Index 2m53s, quant 9.6s. **These timings are not compute cost** — boot and image pull dominate ([why](../../practices/what-this-does-not-cover.md)).

<details>
<summary>As shipped: the conservation identities, the tar workaround, pins, smoke check, run + verify</summary>

**Conservation identities** (exact, so they can't go flaky): TPM sums to exactly **1,000,000**, and `sum(NumReads)` equals the mapped count (189,012) — an internal-consistency check that catches a `quant.sf` truncated on a zero-count tail, which a row count alone would miss.

**The index travels as a tar, not an S3 prefix.** A salmon index is a directory of nine files, and spawn can't stage a directory *output*: output parents aren't `mkdir`-ed, so dockerd creates them as root and the container can't write there (spawn#564). So `index` tars it to one flat `/tmp` file and `quant` untars it.

**Pins** (data tier: stable public — Ensembl `release-116/` is an immutable path):

| | |
|---|---|
| image | `quay.io/aarchbio/salmon@sha256:7134f5…` (tag `2.7.0--hb05d258_0`, cosign-signed, `linux/arm64`) |
| transcriptome | Ensembl release-116 `Homo_sapiens.GRCh38.cdna.all.fa.gz` — `sha256:683eb193…` (453,553 transcripts) |
| reads | ENA `ERR188026` first 200,000 pairs — `sha256:1198ed07…` / `6104ee46…` |

| observable | assertion | observed |
|---|---|---|
| fragments processed | exactly 200000 | 200000 |
| `quant.sf` rows | exactly 453553 | 453553 |
| TPM sum | exactly 1000000 | 1000000 |
| `sum(NumReads)` | == num_mapped | 189012 = 189012 |
| percent mapped | 85–99 | 94.506 |
| expressed transcripts (TPM>0) | 8000–30000 | 17731 |
| *index:* references / files | 453553 / ≥8 | 453553 / 9 |

**`quant` holds the staged tar, doesn't delete it** — under sticky `/tmp` the container gets `EPERM` unlinking a staged input it doesn't own, and `rm -f` doesn't suppress `EPERM` ([the container path](../../practices/container-path.md)).

**Run + verify.**
```sh
spawn task run --spec recipes/salmon/01-index.task.json --wait
spawn task run --spec recipes/salmon/02-quant.task.json --wait
aws s3 ls s3://scicookbook-942542972736-us-east-1/runs/salmon/r1/
```
Smoke check runs inside the task; the bucket listing is the second half ([exit 0 isn't proof](../../practices/container-path.md)). Re-running: bump the `-r1` suffix.

</details>
