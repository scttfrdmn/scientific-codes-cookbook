---
tool: kallisto
tool_version: 0.52.0
image: quay.io/aarchbio/kallisto@sha256:b8f0e24c8a014b202f7ef9eeafffd4a6fa1cd27ac4214850cd65b0b1f684f18d
spawn_version: 0.104.0
---
# kallisto — RNA-seq transcript quantification

Pseudoalign reads to a transcriptome and quantify abundance, cross-checked against salmon.

## Run it

```bash
kallisto index -i idx transcriptome.fa.gz
kallisto quant -i idx -o out reads_1.fq.gz reads_2.fq.gz
```

The recipe indexes the Ensembl-116 human transcriptome and quantifies the **same 200k read pairs [salmon](../salmon/README.md) used**, then checks TPM conservation plus rank-order agreement with salmon.

## Make it yours

| In the recipe | Swap for | What to know |
|---|---|---|
| the Ensembl-116 transcriptome + 200k ERR188026 pairs | your own transcriptome + reads | reused byte-for-byte from [salmon](../salmon/README.md) so the cross-check is valid — nothing re-staged. |
| the **`r8g.large` (16 GiB)** box | size *up* for a bigger transcriptome | **the load-bearing sizing fact, and the catalog's first memory-bound recipe.** kallisto 0.52's index build OOM-kills at 7.75 GiB on the human transcriptome (147M k-mers) — so it's sized memory-bound on salmon's proven 16 GiB figure for identical bytes, **not** compute-bound like every prior recipe. If you scale the reference, RAM is the constraint to watch, [not disk_gib](../../practices/container-path.md). |

**Leave the fixture:** a small sample against a *real* human transcriptome is enough to exercise the index build (the OOM-prone step) and produce a real cross-code agreement; a full-depth sample is a longer run, not a more legible one. Leave-it.

## Shape, size, cost

One task, **8m54s** wall (index build is the long pole; quant ~1 min). `r8g.large` (16 GiB, memory-bound), TTL **15m**, cap $0.04 — retightened from the first real run (below). Actual cost ~$0.017.

<details>
<summary>As shipped: three identities (exact, conservation, cross-code), sizing, pins, smoke check</summary>

Three identities:
- **`n_targets` = 465,769 (exact)** — every FASTA sequence. Note this is *not* salmon's 453,553 `quant.sf` rows: salmon collapses 12,216 duplicate transcript sequences at index time and kallisto keeps them all — a genuine cross-tool difference, so each tool's count is asserted against *itself*.
- **TPM sum = 1,000,000 (exact conservation)** — a per-million normalisation must sum to 1e6, the same identity that anchors [salmon](../salmon/README.md); a quant truncated on a zero tail fails this even if the row count passes.
- **Spearman rank vs salmon ≥ 0.85 (cross-code, observed 0.912)** — on the 15,823 transcripts both detect, kallisto and salmon agree on abundance **rank order**. Rank, not raw TPM, is the honest claim: the two use different effective-length and multimapping models, so absolute TPMs differ (raw log-TPM Pearson only ~0.61) while ordering is robust. The floor is set by that *method* agreement (two correct EM quantifiers land in the 0.85–0.95 rank range), [not by shaving the observed value](../../practices/cross-checks.md); a broken quant decorrelates far below 0.85.

| observable | assertion | observed |
|---|---|---|
| n_targets / quant rows | 465769 / == n_targets | 465769 / 465769 |
| fragments processed | exactly 200000 | 200000 |
| **tpm_sum** | exactly 1000000 (conservation) | 1000000 |
| pct pseudoaligned | 88..96 | 92.1 |
| **Spearman vs salmon** | ≥ 0.85 (both-expressed) | 0.9120 |

**Sizing.** The salmon-anchored 16 GiB held — the run completed on `r8g.large` with no OOM, confirming kallisto 0.52's index fits comfortably in 16 GiB on identical bytes. TTL retightened from that first real run: 30m → 15m, cap $0.06 → $0.04. Had it needed more it would have OOM'd and been retightened *up* to `r8g.xlarge` (the cost cap bounds that miss) — it didn't.

**Pins.** Image `quay.io/aarchbio/kallisto@sha256:b8f0e24c8a01…` (0.52.0, cosign-verified, `linux/arm64`). Transcriptome: Ensembl release-116 `Homo_sapiens.GRCh38.cdna.all.fa.gz` (`sha256:683eb193…`, 184 MB); reads: ENA `ERR188026` first 200k pairs; salmon reference `runs/salmon/r1/quant.sf`. All reused from [salmon](../salmon/README.md) — run its `stage-inputs.sh` first.

**Run + verify.**
```sh
spawn task run --spec recipes/kallisto/01-quant.task.json --wait
aws s3 ls s3://scicookbook-942542972736-us-east-1/runs/kallisto/r1/   # expect abundance.tsv, run_info.json, smoke-check.txt
```
Smoke check runs inside the task; bucket listing is the second half ([exit 0 isn't proof](../../practices/container-path.md)). Re-running: bump the `-r1` suffix.

</details>
