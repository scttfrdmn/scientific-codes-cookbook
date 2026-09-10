---
tool: kraken2
tool_version: 2.17.1
image: quay.io/aarchbio/kraken2@sha256:fc6dd9becb7fec054ee01575d85e12c7fc509e53001c0ade5ed2e095c3cbe455
spawn_version: 0.104.0
---
# Kraken2 — taxonomic classification

Classify reads against a prebuilt taxonomic database — the standard metagenomics first pass.

## Run it

```bash
kraken2 --db viral_db --report out.report query.fasta
```

The recipe classifies the SARS-CoV-2 reference genome against a real prebuilt **viral** DB and asserts it lands on SARS-CoV-2's exact taxon — the "known input → known answer" identity (BLAST/diamond self-hit, one domain over).

## Make it yours

| In the recipe | Swap for | What to know |
|---|---|---|
| SARS-CoV-2 `NC_045512.2` (a known genome) | your own reads | a *known* query is what makes the exact-taxon assertion possible; on unknown reads you'd assert classified-rate bands instead. |
| the 0.66 GB `viral_20240605` DB (staged as a tar, copy-per-task) | a larger prebuilt DB | at 0.66 GB it fits the copy-per-task model every recipe uses — **no EFS**. A full-size DB (where large reference data is the *subject*) is what would justify the mount; don't reach for EFS as a by-product of an oversized DB ([copy, mount, or share?](../../patterns/data-movement.md)). |

**Leave the fixture:** a real prebuilt viral DB (not a toy 2–3-genome DB that classifies nothing) plus a known genome gives an exact-or-wrong identity; a metagenomic workload is a different, larger recipe. Leave-it.

## Shape, size, cost

One task, **~1 s** classification (the DB copy dominates). `c8g.large`, ~$0.02, **~1 min** wall — boot, image pull and staging the 633 MB DB tar ([why](../../practices/what-this-does-not-cover.md)). Not memory-bound: Kraken2 loaded the hash and ran clean within a 2 GiB cap locally.

<details>
<summary>As shipped: the known-answer identity, why not EFS, pins, smoke check</summary>

Kraken2 is deterministic, so the classification is exact-or-wrong. The SARS-CoV-2 reference (`NC_045512.2`) classifies as **C** with LCA taxid **2697049** — *SARS-CoV-2*, the exact species. The report walks the full lineage (Viruses → Riboviria → … → *Sarbecovirus* → SARS-CoV-2), so a broken DB load or misbuilt index would fail to classify or land on the wrong node.

The DB is genome-idx's `viral_20240605`, a *real* RefSeq viral DB — not a toy that classifies nothing. At 0.66 GB it fits copy-per-task (staged as one tar, untarred on the box: 633 MB tar + ~660 MB DB + query ≈ 1.3 GB in `/tmp`, within budget), so it needs **no EFS**. That's deliberate: a 0.66 GB DB doesn't justify the mount, and introducing EFS here would be smuggling a Round-Two lever in as a side effect of an oversized DB.

| observable | assertion | observed |
|---|---|---|
| status | `C` (classified) | C |
| **assigned taxid** | exactly 2697049 (SARS-CoV-2) | 2697049 |
| classified / unclassified seqs | 1 / 0 | 1 / 0 |

**Pins.** Image `quay.io/aarchbio/kraken2@sha256:fc6dd9becb7f…` (2.17.1, cosign-verified, `linux/arm64`). DB: genome-idx `viral_20240605` (`sha256:bca063e6…`, 633 MB) — date-versioned, but a date in the name is **not** a guarantee the bytes are stable, so the sha256 is the real pin. Query: SARS-CoV-2 `NC_045512.2` (`sha256:0891c00c…`, 29,903 bp). Both hashes re-verified on the box before classifying.

**Run + verify.**
```sh
spawn task run --spec recipes/kraken2/01-classify.task.json --wait
aws s3 ls s3://scicookbook-942542972736-us-east-1/runs/kraken2/r1/   # expect out.report, smoke-check.txt
```
Smoke check runs inside the task; bucket listing is the second half ([exit 0 isn't proof](../../practices/container-path.md)). Re-running: bump the `-r1` suffix.

</details>
