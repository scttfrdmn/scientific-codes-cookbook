# Kraken2 — taxonomic classification of a known virus against a real prebuilt DB

One task. Kraken2 classifies the SARS-CoV-2 reference genome against a prebuilt **viral**
database and the smoke check confirms it lands on SARS-CoV-2's exact taxon — the
"known input → known answer" identity (BLAST/diamond self-hit, one domain over).

> **What this recipe does and does not cover.** It classifies one known viral genome
> against a real prebuilt viral DB and asserts the exact assigned taxon — enough to prove
> Kraken2's DB load + classification work on Graviton4. Not a benchmark; not a
> metagenomics workload.

## A real prebuilt DB, copy-per-task — deliberately not EFS

The DB is genome-idx's **`viral_20240605`** — a *real* prebuilt Kraken2 viral database
(RefSeq viral), not a toy 2–3-genome DB that classifies nothing. At **0.66 GB** it fits the
copy-per-task model every other recipe uses (staged as one tar, untarred on the box), so it
needs **no EFS**. That's the deliberate choice: a 0.66 GB DB doesn't justify the mount, and
introducing EFS here would be smuggling in a Round-Two lever as a side effect of an oversized
DB. If EFS earns a recipe, it's one where large reference data is the *subject* — chosen for
that, not a by-product of picking an 8 GB DB when a 0.66 GB one does the job.

## The identity: a known virus lands on its known taxon

Kraken2 is deterministic, so the classification is exact-or-wrong. The SARS-CoV-2 reference
(`NC_045512.2`, an immutable RefSeq accession) classifies as **C** (classified, not
unclassified) with LCA taxid **2697049** — *Severe acute respiratory syndrome coronavirus 2*,
the exact species. The report walks the full lineage (Viruses → Riboviria → … →
*Betacoronavirus* → *Sarbecovirus* → SARS-related coronavirus → **SARS-CoV-2**), so a broken
DB load or a misbuilt index would either fail to classify or land on the wrong node.

## Pins

| | |
|---|---|
| image | `quay.io/aarchbio/kraken2@sha256:fc6dd9becb7fec054ee01575d85e12c7fc509e53001c0ade5ed2e095c3cbe455` |
| | tag `2.17.1--pl5321h1e84f2d_0`, Kraken2 2.17.1, cosign-verified (`sign-existing.yml`), `linux/arm64` |
| DB | genome-idx `viral_20240605` (RefSeq viral), staged as a tar |
| | `sha256:bca063e65eb9552f7f0335824f0f37cff8d11040aded29830d0d03f1077014d0` (633 MB) |
| query | SARS-CoV-2 `NC_045512.2` (RefSeq, immutable accession) |
| | `sha256:0891c00cc2d503f58e0c600ce5ee64e9c64b939525c70d5486bc46cbc7cb073e` (29,903 bp) |

**Data tier: a stable public source with a durable id.** The DB is **date-versioned**
(`viral_20240605`), and a date in the name is **not** a guarantee the bytes are stable — the
sha256 is the real pin (the lesson from NWChem's silent republish). The task re-verifies both
hashes on the box before classifying.

## Smoke check

Measured in the pinned image (the exact task command run verbatim).

| observable | assertion | observed |
|---|---|---|
| status | `C` (classified) | C |
| **assigned taxid** | exactly **2697049** (SARS-CoV-2) | 2697049 |
| classified seqs | 1 | 1 |
| unclassified seqs | 0 | 0 |

All exact — a known genome against a real DB either lands on 2697049 or the recipe fails.

## Resources, and what the timings mean

2 vCPU / 4 GiB, `c8g` (resolves to `c8g.large`), TTL 5m, cap $0.02. Classification is **~1 s**;
Kraken2 loads the 648 MB hash into RAM and ran clean **within a 2 GiB cap** in local testing,
so 4 GiB is comfortable — **not** memory-bound (unlike a full-size DB, which is exactly the
case that *would* justify EFS).

**These timings are not compute cost.** Boot, the Docker install, pulling the Kraken2 image,
and staging the 633 MB DB tar are the whole task — the DB copy dominates. `/tmp` holds the tar
(633 MB) + the untarred DB (~660 MB) + the query, ~1.3 GB, well within budget. TTL/cap are
already minimal.

## Running it

Inputs are pre-staged (see the pins). Then:

```sh
spawn task run --spec recipes/kraken2/01-classify.task.json --wait
```

Then **check the bucket**:

```sh
aws s3 ls s3://scicookbook-942542972736-us-east-1/runs/kraken2/r1/
```

The smoke check runs *inside* the task; the bucket listing is the second half of it. Expect
two objects (`out.report`, `smoke-check.txt`).

**Re-running.** `task_id` is fixed; bump the `-r1` suffix in both `task_id` and the output
prefix to keep both records.
