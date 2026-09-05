# kallisto — transcript quantification, cross-checked against salmon on identical bytes

One task. kallisto builds its index from the Ensembl-116 human transcriptome and
pseudoaligns the same 200,000 read pairs salmon quantified, then the smoke check
confirms an exact conservation identity (TPM sums to 1,000,000) **and** that kallisto
and salmon — two independent quantifiers — agree on transcript-abundance rank order.

> **What this recipe does and does not cover.** It quantifies one small RNA-seq sample
> against a real human transcriptome and cross-checks it against `recipes/salmon` — enough
> to prove kallisto's index build and pseudoalignment work on Graviton4 and agree with an
> unrelated code. Not a benchmark; no differential expression or full-depth sample.

## Why kallisto is the catalog's first memory-bound recipe (first `r8g`)

Every recipe so far has been compute-bound and landed on **c8g** — "pick c8g / m8g / r8g by
fit" has been theory. kallisto exercises it for real: **kallisto 0.52's index build (the
bifrost / CompactedDBG path) needs more than 8 GiB of RAM** on the human transcriptome —
it OOM-kills at 7.75 GiB at the MPHF / equivalence-class stage (147M unique k-mers, 1.8M
unitigs). salmon indexed these *exact* bytes comfortably in 16 GiB, so this recipe is sized
memory-bound at **`r8g.large` (16 GiB)** — salmon's proven figure on identical input — not
compute-bound. That's a real, useful sizing fact for anyone running a kallisto job, and it's
the kind of thing this cookbook exists to surface. **Sizing is retightened from the first
real Graviton run** (the standing rule); the recorded figures below are the first run's.

## One task, and reusing salmon's staged bytes

salmon split index and quant into two tasks because its index is a 1.68 GB directory tar;
kallisto's index is a single **0.93 GB** `.idx` file, so index + quant + the cross-check fit
one task and one boot inside the ~6.1 GiB `/tmp` budget — cheaper, no inter-task staging.

The inputs are **salmon's already-staged objects, byte for byte** — the Ensembl-116
transcriptome and the 200k ERR188026 pairs. That reuse is the point: a cross-code check only
means something on *identical* bytes (CLAUDE.md's cross-validation rule), so nothing is
re-staged and no second copy of a derived input exists to keep true.

## Three identities: one exact, one conservation, one cross-code

- **`n_targets` = 465,769 (exact).** kallisto indexes every sequence in the FASTA. Note this
  is **not** salmon's 453,553 `quant.sf` rows: salmon collapses 12,216 duplicate transcript
  sequences during indexing and kallisto keeps them all — a genuine cross-tool difference,
  not an error, so each tool's count is asserted against *itself*.
- **TPM sum = 1,000,000 (exact conservation).** TPM is a per-million normalisation, so the
  column must sum to exactly 1e6 — the same identity that anchors `recipes/salmon`. A
  `quant` truncated on a zero tail fails this even if the row count passes.
- **Spearman rank vs salmon ≥ 0.85 (cross-code, observed 0.9120).** On the 15,823
  transcripts both tools detect, kallisto and salmon agree on abundance **rank order** at
  Spearman 0.912. Rank — not raw TPM — is the honest claim: the two use different
  effective-length and multimapping models, so absolute TPMs differ (raw log-TPM Pearson is
  only ~0.61) while the ordering is robust. The floor is set by that *method* agreement (two
  correct EM quantifiers on identical reads+reference land in the 0.85–0.95 rank-correlation
  range), not by shaving the observed value; a broken quant decorrelates far below 0.85.

## Pins

| | |
|---|---|
| image | `quay.io/aarchbio/kallisto@sha256:b8f0e24c8a014b202f7ef9eeafffd4a6fa1cd27ac4214850cd65b0b1f684f18d` |
| | tag `0.52.0--h697f910_0`, kallisto 0.52.0, cosign-verified (`publish.yml@refs/heads/main`), `linux/arm64` |
| transcriptome | Ensembl release-116 `Homo_sapiens.GRCh38.cdna.all.fa.gz`, byte for byte |
| | `sha256:683eb19310c40bf1396e4718f45afa2ce86755717c0990f47a171f535d248ea1` (183,898,799 B) |
| reads | ENA `ERR188026` (Geuvadis), first 200,000 pairs |
| | `sha256:1198ed07…c5432e` / `sha256:6104ee46…7e120a` |
| salmon reference | `runs/salmon/r1/quant.sf` (this recipe's cross-check counterpart) |

**Data tier: stable public source with a durable id — reused from `recipes/salmon`.**
Nothing is staged by this recipe; run `recipes/salmon/stage-inputs.sh` first if the objects
aren't in the bucket.

## Smoke check

Measured in the pinned image (index run natively to observe the cross-code number, which the
7.75 GiB local Docker cap could not build; the assertions run on the box).

| observable | assertion | observed |
|---|---|---|
| n_targets | exactly 465769 (FASTA sequences) | 465769 |
| fragments processed | exactly 200000 | 200000 |
| quant rows | exactly 465769 (== n_targets) | 465769 |
| **tpm_sum** | exactly 1000000 (conservation) | 1000000 |
| pct pseudoaligned | 88..96 | 92.1 |
| **Spearman vs salmon** | ≥ 0.85 (both-expressed) | 0.9120 |

## Resources, and what the timings mean

**2 vCPU / 16 GiB, `r8g` (resolves to `r8g.large`) — memory-bound, not compute-bound.**
TTL 15m, cap $0.04. The index build is the long pole; quant is ~1 min.

**The salmon-anchored 16 GiB held.** The recorded run completed on `r8g.large` in **8m54s**
(05:28:10 → 05:37:04 UTC) with no OOM — so kallisto 0.52's index on the human transcriptome
fits comfortably in 16 GiB, confirming salmon's proven figure on identical bytes was the
right anchor. TTL was **retightened from that first real run**: 30m → **15m**, `cost_limit`
$0.06 → $0.04; the recorded run used the original 30m. Had it needed more than 16 GiB it
would have OOM'd and been retightened *up* to `r8g.xlarge` (the cost cap bounds that miss) —
it didn't.

**These timings are not compute cost.** Boot, the Docker install, pulling the kallisto image,
and downloading the 184 MB transcriptome are much of the 8m54s; `cost_limit` is the hard cap.
Actual cost was ~$0.017. Disk is modest (transcriptome + 0.93 GB index + reads).

## Running it

Inputs come from `recipes/salmon` — stage those first if needed, then:

```sh
spawn task run --spec recipes/kallisto/01-quant.task.json --wait
```

Then **check the bucket**, every time:

```sh
aws s3 ls s3://scicookbook-942542972736-us-east-1/runs/kallisto/r1/
```

The smoke check runs *inside* the task; the bucket listing is the second half of it. Expect
three objects (`abundance.tsv`, `run_info.json`, `smoke-check.txt`).

**Re-running.** `task_id` is fixed; bump the `-r1` suffix in both `task_id` and the output
prefix to keep both records.
