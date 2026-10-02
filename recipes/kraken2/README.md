---
tool: kraken2
tool_version: 2.17.1
image: quay.io/aarchbio/kraken2@sha256:fc6dd9becb7fec054ee01575d85e12c7fc509e53001c0ade5ed2e095c3cbe455
spawn_version: 0.111.4
last_verified: 2026-10-02
---
# Kraken2 — classify 24M read pairs against standard-8 in 68 seconds

Classifies a complete human WGS run against the 8 GB standard index and finds the cell line's virus. For anyone running taxonomic classification at scale.

> **You will spend longer moving the index than classifying with it** — 92 s of stage-in against 68 s
> of work. That ratio, not the chip, is what this recipe is about.

## Run it

```bash
make stage RECIPE=kraken2   # once: the 5.5 GiB standard-8 index
spawn task run --spec "$(make -s spec RECIPE=kraken2)" --wait   # ~4 min billed on r8g.2xlarge, 68 s of it kraken2
make ls    RECIPE=kraken2   # out.report + kraken2.log + smoke-check.txt

kraken2 --db db --paired --threads 8 --report out.report --output /dev/null \
        SRR062634_1.filt.fastq.gz SRR062634_2.filt.fastq.gz
```

## Make it yours

| In the recipe | Swap for | What to know |
|---|---|---|
| standard-8 | standard-16, PlusPF, PlusPFP | the index is the accuracy knob *and* the cost driver. PlusPF is ~70 GB and no longer fits this shape — it needs a mount. |
| `SRR062634` | your reads | reused byte-for-byte from [bwa](../bwa-samtools/README.md); `--paired` counts a pair as one sequence. |
| `--output /dev/null` | a real path | per-read assignments for 24M pairs are gigabytes. The report carries the totals. |

**Leave the index at standard-8** — the one most people actually run, and it fits a normal box. **Scale it** by index size, not sample count: more samples amortise the index load, a bigger index multiplies it.

## Shape, size, cost

`r8g.2xlarge` (8 vCPU, 64 GiB): **68 s of kraken2 in a ~4 min billed window, ~$0.03.**

**64 GiB is the minimum, and a staging rule is why — not kraken2's appetite.** A staged input
[cannot be deleted](../../practices/container-path.md), so the 5.5 GiB tar.gz and the ~7.6 GB of
unpacked `.k2d` must coexist, plus 3.6 GiB of reads: measured tmpfs peak **17,538 MB**. tmpfs is half
of RAM, so a 32 GiB box offers 16 GiB and cannot hold it. Host memory peaked at 29,819 MB.

**No generation table here.** At 68 s of compute against 92 s of index transfer, the chip is not the
lever — amortising the index across samples is. [Five data paths, measured](../../measurements/star-real/README.md)
is the relevant comparison, not a Graviton ladder.

<details>
<summary>As shipped: a fourth tool on one read count, a virus that should be there, pins</summary>

### The checks

| observable | assertion | observed |
|---|---|---|
| **pairs processed** | **exactly 24,148,993** | **24,148,993** |
| **classified + unclassified** | **== pairs processed** | **24,148,993** |
| classified | exactly 21,047,258 (87.16%) | **21,047,258** |
| human clade pairs | exactly 20,928,455 (86.66%) | **20,928,455** |
| top species | taxid 9606 | **9606** |
| **EBV clade pairs** | **> 0** | **5,133** |

**The read count is the fourth independent arrival at one number.** With `--paired` kraken2 counts a
pair as one sequence, so 24,148,993 is [seqkit](../seqkit/README.md)'s per-mate count, which is
[bwa](../bwa-samtools/README.md)'s 48,297,986 primary records halved, which is
[fastp](../fastp/README.md)'s `before_filtering` halved. Four tools, four different ways of counting
the same file. kraken2's own books then balance: classified plus unclassified is exactly the number
processed.

**The truth here is the sample's provenance, not a claim about its contents.** These are 1000 Genomes
reads from HG00096, so "the dominant species is *Homo sapiens*" is a fact about the input. That
mattered for the design: the tempting alternative was a ZymoBIOMICS mock community, where the
manufacturer's known mixture would be a GIAB-style constructed truth — but establishing *which*
public run is genuinely shotgun-of-a-known-mix means asserting a sample's composition from inference,
and this batch already lost two genomes to exactly that kind of guess.

**Epstein-Barr virus is expected biology, and that is the nicest check on the page.** 1000 Genomes
samples are EBV-transformed lymphoblastoid cell lines, so the virus used to immortalise the line is
in the sample by construction. Finding 5,133 pairs of `Lymphocryptovirus` is classification working,
not contamination — and it is asserted as *presence* rather than an exact count, because the claim
worth making is that the cell line's virus shows up at all.

Everything else is trace: 713 pairs of *Xanthomonas*, 303 of *Mycobacterium canetti*. At 87% overall
and 86.66% human, the 12.84% unclassified is what an 8 GB-capped index costs in sensitivity.

### Pins

| | data tier |
|---|---|
| Kraken2 | `quay.io/aarchbio/kraken2@sha256:fc6dd9becb7f…` (2.17.1, cosign-verified, `linux/arm64`) |
| index | `k2_standard_08gb_20250402` from `s3://genome-idx/kraken/` — date-versioned; sha256 `e592faea3e307f01…`, `hash.k2d` 7,629 MB |
| reads | RODA `s3://1000genomes/…/SRR062634_{1,2}.filt.fastq.gz` — staged by [bwa](../bwa-samtools/README.md) |

The index name's date is a durable id but not a byte guarantee, so the run publishes the archive's
sha256. Staging is a server-side S3 copy from the public `genome-idx` bucket into your own — note that
`--no-sign-request` cannot be used, because it would make the *destination* write anonymous too and
multipart upload then fails.

### Run + verify

```sh
make stage RECIPE=kraken2
make run   RECIPE=kraken2
make ls    RECIPE=kraken2
```

Expect `smoke-check.txt` with `pairs_processed 24148993`, `top_species_taxid 9606` and
`ebv_clade_pairs` above zero.

</details>
