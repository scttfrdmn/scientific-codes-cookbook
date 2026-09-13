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

One task, **~1 s** classification (the DB copy dominates). `c8g.large`, ~$0.02, **~1 min** wall — boot, image pull and staging the 633 MB DB tar ([why](../../practices/what-this-does-not-cover.md)).

**Sizing:** RAM ≈ DB size — Kraken2 loads the whole DB into memory. The viral DB (0.66 GB) runs in ~2 GiB (`c8g`); a standard DB (8+ GB) needs a memory box sized to *your* DB. A stated requirement, not a fixture measurement.

<details>
<summary>As shipped: the known-answer identity, why not EFS, pins, smoke check</summary>

Kraken2 is deterministic, so the classification is exact-or-wrong. The SARS-CoV-2 reference (`NC_045512.2`) classifies as **C** with LCA taxid **2697049** — *SARS-CoV-2*, the exact species. The report walks the full lineage (Viruses → Riboviria → … → *Sarbecovirus* → SARS-CoV-2), so a broken DB load or misbuilt index would fail to classify or land on the wrong node.

The DB is genome-idx's `viral_20240605`, a *real* RefSeq viral DB — not a toy that classifies nothing. At 0.66 GB it fits copy-per-task (staged as one tar, untarred on the box: 633 MB tar + ~660 MB DB + query ≈ 1.3 GB in `/tmp`, within budget), so it needs **no EFS**. That's deliberate: a 0.66 GB DB doesn't justify the mount, and introducing EFS here would be smuggling a Round-Two lever in as a side effect of an oversized DB.

**The DB pin is a moving artifact ([tier 3](../../practices/what-this-does-not-cover.md)).** genome-idx repacks its prebuilt DBs, so `make stage RECIPE=kraken2` may fetch a `.tar` whose bytes differ from the recorded sha256 — the pin records the version we ran, not a promise the next fetch matches. When it drifted, we re-ran the recipe against the current DB and the assertion held (SARS-CoV-2 → **2697049**), then repinned: **the recipe tests the classification, not the bytes**, which is exactly why a moving pin is acceptable here and wouldn't be for a derived fixture.

| observable | assertion | observed |
|---|---|---|
| status | `C` (classified) | C |
| **assigned taxid** | exactly 2697049 (SARS-CoV-2) | 2697049 |
| classified / unclassified seqs | 1 / 0 | 1 / 0 |

**Pins.** Image `quay.io/aarchbio/kraken2@sha256:fc6dd9becb7f…` (2.17.1, cosign-verified, `linux/arm64`). DB: genome-idx `viral_20240605` (`sha256:9cbf9ddc…`, 633 MB) — date-versioned, but a date in the name is **not** a guarantee the bytes are stable, so the sha256 is the real pin. Query: SARS-CoV-2 `NC_045512.2` (`sha256:0891c00c…`, 29,903 bp). Both hashes re-verified on the box before classifying.

**Run + verify.**
```sh
make run RECIPE=kraken2
make ls RECIPE=kraken2   # expect out.report, smoke-check.txt
```
Smoke check runs inside the task; bucket listing is the second half ([exit 0 isn't proof](../../practices/container-path.md)). Re-run: `make run` launches a fresh task each time and overwrites this prefix — no spec edit needed.

**Fan out across samples.** One classification is one task; a cohort is the same task as a [job array](../../patterns/job-arrays.md) — validate on one sample with `make run` above, *then* fan out one instance per sample, each keyed by `$JOB_ARRAY_INDEX`. `spawn array status` / `collect` / `retry --failed` manage the set; add `--max-concurrent-auto` when a shared reference or spot capacity pushes back.

</details>
