---
tool: bracken
tool_version: "3.0.1"
images:
  - quay.io/aarchbio/kraken2@sha256:fc6dd9becb7fec054ee01575d85e12c7fc509e53001c0ade5ed2e095c3cbe455
  - quay.io/aarchbio/bracken@sha256:f2cf8671bef1b9892efeadfa4ee0a9c9b7c961704e0b2991ea1fb2258c838abd
last_verified_x86_64: 2026-10-13
last_verified_amd: 2026-10-18
images_x86_64:
  - quay.io/biocontainers/kraken2@sha256:c5eb4764f23e9db975cbc3e545d330e1fe468307f5e59a99c0ce8f4c1f6f09e2
  - quay.io/biocontainers/bracken@sha256:b86467a34e157dfa086a40e8e7ca7d6f4af1c559030024efbab76381e448d097
spawn_version: 0.111.1
last_verified: 2026-09-22
---
# Abundance — why a kraken2 report is not a composition

Bracken turns a kraken2 classification into species abundance on Graviton4, against a four-virus mixture whose proportions were chosen before any read was made. For anyone reporting "what is in this sample, and how much".

> **What this covers.** 2000 × 100 bp reads from four viral genomes at 50/30/15/5%, the pinned `viral_20240605` kraken2 index, and Bracken at species level. Not a real metagenome, bacterial communities, `bracken-build` on a custom index, or MetaPhlAn-style marker profiling.

## Run it

```bash
make stage RECIPE=abundance
for s in $(make -s spec RECIPE=abundance); do spawn task run --spec "$s" --wait; done
kraken2 --db db --output mix.kraken --report mix.report mix.fasta
bracken -d dbmin -i mix.report -o mix.bracken -r 100 -l S -t 1
```

## Make it yours

| In the recipe | Swap for | What to know |
|---|---|---|
| four viral genomes | your reads | the planted proportions are the answer key; a real sample has none. |
| `-r 100` | your read length | **must match your reads** — it selects `database<N>mers.kmer_distrib`, and a mismatched length silently reweights everything. |
| `-l S` | `-l G`, `-l F` … | the rank you report at. Ambiguous reads resolve at higher ranks, so genus-level abundance is more robust than species. |
| the prebuilt index | your own index | a custom index needs `bracken-build` first; the prebuilt ones already ship the distribution files. |

**Leave the fixture:** planted proportions make the recovery exactly checkable instead of merely plausible. **Scale it** to a real sample whenever — and read the caveat below about what Bracken can and cannot repair.

## Shape, size, cost

Two tasks, kraken2 classifying the mixture and Bracken re-estimating from its report, both checked against the planted proportions: kraken2 on `c8g.large` with **8 GiB** (the index is 633 MiB on disk and loads into RAM, and [staging space is tmpfs at half of RAM](../../practices/container-path.md)), Bracken on `c8g.large` with 4 GiB. TTL 12m / 10m, caps $0.05 / $0.03. **These timings are not compute cost.**

## Which box — [measured](../../measurements/abundance-real/README.md) (two whole ZymoBIOMICS runs, 8.8M and 47.8M pairs)

| chip | instance | Bracken wall, 4 calls | billed | peak RSS | **billed $/sample** |
|---|---|---|---|---|---|
| Graviton4 | `c8g.large` | 0.73 s | 69 s | 32 MiB | 0.00076 |
| Xeon 6975P-C | `c8i.large` | 0.71 s | 77 s | 32 MiB | 0.00100 |
| **EPYC 9R45** | `c8a.large` | **0.49 s** | **48 s** | 32 MiB | **0.00072** |

**Any box will do. Bracken is free; the boot is the bill.** Outputs are byte-identical on all three chips. At whole-run scale Bracken lifts *L. monocytogenes* from kraken2's 3.0% to **87.0%** against a published 89.1%, and conserves every read. It also inflates one absent genus 17×, to 1.66% of the even mock, which fails the closed-list check. Run it where the kraken2 reports already are.

<details>
<summary>As shipped: zero-to-1000 on the dominant species, a conservation identity, the misassignment Bracken cannot fix, pins</summary>

### The checks

| observable | assertion | observed |
|---|---|---|
| reads written | == planted total | **2000** |
| classified + unclassified | == reads (conservation) | **1922 + 78** |
| reads at species rank `S` vs below it `S1` | more below than at | **627 vs 1293** |
| dominant species, clade reads | == planted | **1000, exact** |
| dominant species, **direct** at species rank | **0** | **0** |
| `sum(new_est_reads)` | == Bracken's own "reads kept at species level" | **1920 == 1920** |
| `fraction_total_reads` | == `new_est_reads / 1920` for every row | **exact** |
| dominant species, Bracken estimate | == planted | **1000, exact** |
| top-3 by abundance | same **order** as planted | **matches** |

### The number that justifies the tool: 0 → 1000

The mixture is half SARS-CoV-2. In kraken2's report, the species that holds those reads has **zero reads assigned to it**:

```text
taxid 694009   rank S   clade_reads 1000   direct_at_species 0
```

All 1000 reads resolved one level *below* species, to the strain. Across the whole report, **1293 of 2000 reads land at `S1` and only 627 at `S`** — so summing the species-rank column reports 31% of the data and puts the most abundant organism in the sample at **zero**. Bracken pushes clade-level reads back down to species and returns **1000**, exactly the planted count.

That is the entire argument for the tool, and it is why a kraken2 report is a classification rather than a composition. The recipe asserts both halves — the `0` and the `1000` — because either alone is unremarkable.

### Two exact identities, no bands

**Conservation.** `sum(new_est_reads)` must equal the read count Bracken says it kept at species level: **1920 == 1920**. Bracken *redistributes*; it must not create or lose reads, so this is exact rather than a tolerance.

**The fraction is a normalisation, not an estimate.** `fraction_total_reads` equals `new_est_reads / 1920` for every row — asserted to 1e-5 across the table. This matters for reading the output: the dominant species' fraction is `0.52083`, not the planted `0.50`, because **Bracken normalises over reads kept at species level (1920), not over all reads (2000)**. Nothing is wrong; the denominators differ. Compare a Bracken fraction against a planted proportion without noticing that and you will chase a 2% discrepancy that is pure bookkeeping.

### What Bracken does not fix

One of the four planted species does not come back. Hepatitis B (taxid 10407, a **3.2 kb** genome) was planted at 100 reads, and Bracken reports:

```text
29 reads across 5 sibling hepadnavirus species; taxid 10407 itself got 0
  Capuchin monkey hepatitis B virus   15      Roundleaf bat hepatitis B virus    3
  Woolly monkey hepatitis B virus      5      Tai Forest hepadnavirus            1
  Pomona bat hepatitis B virus         5
```

**Bracken redistributes reads down the tree; it cannot undo a wrong assignment.** kraken2 put those reads on related species, and Bracken leaves them there — it re-weights within the tree it was given, it does not re-classify. 100 bp reads off a 3.2 kb genome in a crowded family are genuinely ambiguous, so this is a property of the data, not a defect in either tool.

It is reported rather than removed from the fixture because it is the honest limit of the method, and because removing it would leave a recipe that implies abundance estimation repairs classification error. It also sets the assertions: **rank order** of the top three, plus exact recovery on the unambiguous dominant species — not a per-species fraction for all four, which would be asserting a band over a misclassification.

### Bracken needs 745 KB, not 633 MiB

`bracken` reads only `database<readlen>mers.kmer_distrib` — **not** the index. Measured: running it against a directory containing nothing but `database100mers.kmer_distrib` produces output **byte-identical** to the full-index run. So task 1 copies that one 745 KB file out of the unpacked index and hands it on, and the Bracken task never stages the 633 MiB tar.

The distribution files ship **inside the pinned index tarball**, at the same dated prefix, so no new input needed staging and version-matching is automatic — which is load-bearing, since a `kmer_distrib` from a different index build describes different k-mer ambiguity. (`bracken-build` is only needed for an index you built yourself.)

### Pins

| | data tier |
|---|---|
| kraken2 | `quay.io/aarchbio/kraken2@sha256:fc6dd9be…` |
| Bracken | `quay.io/aarchbio/bracken@sha256:f2cf8671…` (reports `v3.0.1`; the package is `3.1p1`) |
| viral index | `viral_20240605.tar`, sha256 `9cbf9ddc…` — **reused from `inputs/kraken2/`**, not re-staged |
| NC_045512.2 | sha256 `0891c00c…` — also reused from `inputs/kraken2/` |
| NC_001802.1 / NC_003977.2 / NC_001526.4 | NCBI efetch, sha256-pinned — the three genomes this recipe adds |

The shared index and SARS-CoV-2 genome are read from the [kraken2](../kraken2/README.md) recipe's prefix rather than copied: a second copy of a pinned input is a second thing to keep true. Staging the three new genomes needs a `sleep` between fetches — a bare loop against NCBI efetch returns **HTTP 429**.

Also: the staged index tar is never `rm`-ed inside the task. Host `/tmp` is sticky `1777` and stage-in runs as a different user, so the container gets `EPERM`, which `rm -f` does not suppress ([why](../../practices/container-path.md)).

### On x86_64

The same two tasks on same-generation Intel (`c8i.xlarge`, `c8i.large`), from bioconda's own x86
builds of the same versions — `quay.io/biocontainers/kraken2@sha256:c5eb4764…` (2.17.1) and
`quay.io/biocontainers/bracken@sha256:b86467a3…` (3.1p1), both `linux/amd64`; not cosign-signed,
so the digests are the pins. Same staged index and genomes:

| | arm64 | x86_64 |
|---|---|---|
| `dominant_est_reads` / `sum_new_est_reads` | 1000 / 1920 | 1000 / 1920 |
| hepadnavirus recovered | 29 across 5 siblings | 29 across 5 siblings |
| all 8 outputs (`mix.report`, `mix.bracken`, …) | — | **byte-identical** to arm64 |

Every file both tasks write is the same bytes on both chips, including the synthesised mixture's
`planted.tsv` and `fixture.txt` — the reads are cut from the genomes deterministically, with no
`rand()`, so x86 classifies exactly the reads arm64 did.

### Run + verify

```sh
make stage RECIPE=abundance      # once: the three extra genomes
make run   RECIPE=abundance                # Graviton4
make run   RECIPE=abundance ARCH=x86_64    # same specs, x86 pins, writes runs-x86_64/
make ls    RECIPE=abundance
```

Assertions are `test` calls and awk exits inside both tasks. Expect `smoke-check.txt` with `dominant_est_reads 1000`, `sum_new_est_reads 1920`, `fraction_identity yes`, `top3_order_matches_planted yes`, and the hepadnavirus lines recording what was not recovered.

</details>
