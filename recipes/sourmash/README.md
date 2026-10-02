---
tool: sourmash
tool_version: 4.9.4
image: quay.io/aarchbio/sourmash@sha256:29733e7ac937dd17d8c7b84130f36b41da1a33f02abab0b2276c92c2683abd10
spawn_version: 0.111.4
last_verified: 2026-10-01
---
# sourmash — FracMinHash over 20 bacterial genomes, cross-checked against mash

Sketches 20 complete RefSeq genomes, recovers all ten species, and agrees with mash on pair ordering. For anyone choosing between MinHash implementations.

## Run it

```bash
make stage RECIPE=mash       # the 20-genome set; sourmash reuses it, nothing re-staged
spawn task run --spec "$(make -s spec RECIPE=sourmash)" --wait   # mash first: this recipe reads its dist.tsv
make run   RECIPE=sourmash   # ~3 min billed, 15 s of it sourmash
make ls    RECIPE=sourmash   # sim.csv + smoke-check.txt

sourmash sketch dna -p k=21,scaled=1000 GCF_000005845.2.fna.gz -o GCF_000005845.2.sig \
  --name GCF_000005845.2
sourmash compare ./*.sig --csv sim.csv
```

## Make it yours

| In the recipe | Swap for | What to know |
|---|---|---|
| 20 RefSeq genomes | your genomes or assemblies | reused byte-for-byte from [mash](../mash/README.md) — the comparison only means anything on identical bytes. |
| `scaled=1000` | smaller for more resolution | FracMinHash keeps 1/scaled of hashes, so a 4 Mb genome gives ~4,000 — distant pairs then share *none*, which matters below. |
| `k=21` | `k=31` (sourmash's usual default) | **k=21 is chosen to match mash.** A k=31 sketch against a k=21 one compares methods, not genomes. |
| `compare` | `gather` / `search` against a database | the canonical sourmash job; needs a prepared database, which this recipe does not stage. |

**Scale it by genome count** — sketching is per-genome and comparison is per-pair on small sketches,
so the input is the only thing that grows.

## Shape, size, cost

`c8g.2xlarge`: **15 s of sourmash inside a 166 s billed window, $0.0147.**

**No generation table, for the same reason [mash](../mash/README.md) has none** — at this scale the
work is seconds and a four-chip ladder would measure boot. Sketching is not where instance choice
pays off.

<details>
<summary>As shipped: the same NCBI truth mash was checked on, and why the all-pairs rank statistic is the wrong metric</summary>

### The checks

| observable | assertion | observed |
|---|---|---|
| signatures | exactly 20 | **20** |
| **self-similarity** | **exactly 1** | **1.000000** |
| **nearest neighbour** | **same species, 20 of 20** | **20 / 20** |
| min within-species | recorded | **0.241442** |
| **max between-species** | **< min within-species** | **0.018844** (13× margin) |
| pairs compared | exactly 190 | **190** |
| **Spearman vs mash, both seeing signal** | **≤ −0.95** | **−0.9590** (n = 67) |
| **Spearman vs mash, within-species** | **exactly −1** | **−1.0000** (n = 10) |

The first five are the same tests [mash](../mash/README.md) passes, against the same external truth
— each genome's species parsed from its own FASTA defline — so two independent MinHash
implementations are checked against one NCBI-derived fact rather than against each other alone.
Self-similarity 1.0 is free: a sketch compared with itself is identical by construction.

### Why the all-pairs rank correlation is not the check

The first version of this recipe asserted Spearman ≤ −0.95 over all 190 pairs and **failed at
−0.9048**. That was a wrong metric, not a disagreement:

| pair set | n | Spearman |
|---|---|---|
| all pairs | 190 | −0.9048 |
| **both tools see signal** (sourmash > 0) | **67** | **−0.9590** |
| within-species only | 10 | **−1.0000** |

**123 of the 190 pairs have sourmash similarity exactly 0** — at k=21/scaled=1000 two different
genera share no hashes at all — and on mash's side those same 123 pairs take only **5 distinct
values**, up in its saturation region. Both tools are saying "no detectable relationship"; that is
agreement expressed as ties, and ties on both sides cap any rank statistic. The −0.9048 measured the
tie structure, not the tools.

Restricted to pairs where both have signal, agreement is −0.9590. On the ten within-species pairs —
the ones anyone actually needs ranked — it is **perfect, with no inversions**. The sign is negative
because sourmash reports similarity and mash reports distance.

**Rank, not value, and the reason is structural.** mash uses a bottom-*s* sketch of fixed size;
sourmash uses FracMinHash, keeping a fixed *fraction*. The two estimate the same k-mer overlap but
diverge systematically when genomes differ in size, so their raw numbers are not the same quantity —
the same reason [kallisto and salmon](../kallisto/README.md) are compared on rank
([the practice](../../practices/cross-checks.md)). What makes the tolerance defensible here is that
within the signal set the only remaining source of disagreement is sketch sampling error, and the
within-species result shows it is small enough to produce no inversions at all.

### Pins

| | data tier |
|---|---|
| sourmash | `quay.io/aarchbio/sourmash@sha256:29733e7ac937…` (4.9.4, cosign-verified, `linux/arm64`) |
| genomes | `inputs/genomes20/genomes20.tar`, sha256 `6fa8884c45af0178…` — 20 RefSeq accessions, staged by [mash](../mash/README.md) |
| mash's distances | `runs/mash/r1/dist.tsv` — the cross-check reads mash's real output, not a copy |

Nothing is staged twice: both tools read the same tar, and the comparison reads mash's own
`dist.tsv`. Signatures are named with `--name <accession>` so the `compare --csv` header is
predictable — sourmash otherwise names a signature after the first sequence in the file.

### Run + verify

```sh
make stage RECIPE=mash
make run   RECIPE=mash       # produces dist.tsv
make run   RECIPE=sourmash
make ls    RECIPE=sourmash
```

Expect `smoke-check.txt` with `nearest_same_sp 20`, `spearman_signal -0.9590` and
`spearman_within_sp -1.0000`.

</details>
