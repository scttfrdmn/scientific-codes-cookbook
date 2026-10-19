---
tool: blast
tool_version: 2.16.0
image: quay.io/aarchbio/blast@sha256:377bfb5dc686ed9df1ce95f31835225c573bb6b75c17b0da80dfeb0c3b747b0c
last_verified_x86_64: 2026-10-13
last_verified_amd: 2026-10-18
images_x86_64:
  - quay.io/biocontainers/blast@sha256:9dfc69f990c0aeb936276ee591ed32919a79f46dfa34060055e05a050a17959c
spawn_version: 0.111.4
last_verified: 2026-10-01
---
# BLAST+ — 1000 proteins against the whole human proteome, and the identity that scale broke

Searches 1000 query proteins against all 382,428 Ensembl 116 peptides in 156 s. For anyone sizing a homology search, or choosing between BLAST+ and DIAMOND.

> **[DIAMOND does the same search in 19 s](../diamond/README.md)** — 8.2× faster for 0.43% of the self-hits. This page is the reference the fast tool is measured against.

## Run it

```bash
make stage RECIPE=blast   # Ensembl 116 proteome + the first 1000 records as queries
spawn task run --spec "$(make -s spec RECIPE=blast)" --wait   # makeblastdb then blastp, ~4 min total
make ls    RECIPE=blast   # hits.tsv + smoke-check.txt

makeblastdb -in pep.fa -dbtype prot -out pepdb
blastp -query queries1000.fa -db pepdb -max_target_seqs 500 -num_threads 16 \
  -outfmt '6 qseqid sseqid pident length qlen evalue bitscore' -out hits.tsv
```

## Which box — [measured](../../measurements/blast-real/README.md) on all 20,525 reviewed human proteins vs all of SwissProt (16 threads)

| chip | instance | `blastp` | $/hr | **billed $/result** |
|---|---|---|---|---|
| **Graviton4** | `c8g.4xlarge` | **3,783 s** | 0.6381 | **0.688** |
| Xeon 6975P-C | `c8i.4xlarge` | 5,385 s | 0.7497 | 1.145 |
| EPYC 9R45 | `c8a.4xlarge` | 3,861 s | 0.8622 | 0.947 |

**Take Graviton4.** AMD is no faster and its hour is 35% dearer. The 8.0M-row hit table is byte-identical on all three chips, and every query that hits anything finds itself full length at 100% identity. DIAMOND does the same search in 32–73 s.

## Make it yours

| In the recipe | Swap for | What to know |
|---|---|---|
| the first 1000 proteome records | your own query FASTA | the queries being records *of the database* is what makes the self-hit identity available; your own queries lose that check. |
| `-max_target_seqs 500` | higher for deep families | a correctness knob, not a speed one: at 20, 25 of 931 queries lost their own self-hit. 500 costs 2 s (154 → 156). |
| 1000 queries | a whole proteome | cost is near-linear in queries; the measured whole-proteome run above is 63 minutes. |

**Leave the workload** — a real query set against a real proteome, so the timing and the
DIAMOND comparison both transfer. **Scale it** by query count, which is the axis that costs money.

<details>
<summary>As shipped: the identity scale broke, what replaced it, pins</summary>

### The fixture's cost

| | search | $/search | hit rows |
|---|---|---|---|
| **BLAST+**, 16 threads | **156 s** | **0.0277** | 320,305 |
| [DIAMOND](../diamond/README.md) `--very-sensitive` | 19 s | 0.0034 | — |

### The checks

| observable | assertion | observed |
|---|---|---|
| database sequences | **exactly 382,428** | **382,428** |
| queries with any hit | ≥ 900 | **931** |
| **queries whose hit set contains itself** | **== queries with any hit** | **931** |
| **self-rows at 100.000% identity** | **== self-rows** | **931** |
| self-alignments spanning the whole query | observation | **829** |

### The identity that 20 queries hid

The earlier 20-query version of this page asserted that **every** query gets a full-length,
100%-identity self-hit that nothing outscores. At 1000 queries that is simply false: 931 of 1000
queries hit anything, and of those only **829** self-alignments span the full query — the other 102
stop **exactly one residue short**, every time.

So the claim had to be split, because two different things were bundled in it:

- **Exact and algorithmic:** a sequence is 100% identical to itself, so every reported self-row is
  at `pident 100.000`. Measured: 931 of 931. This is the assertion.
- **Data-dependent:** whether the local alignment extends to the final residue. 102 of 931 do not.
  Terminal proline is enriched 3.6× among them (50 of 102, against 137 of 1000 overall) — a clear
  signal, but the mechanism is not established here, so it is reported rather than explained.
  On SwissProt, none of 20,522 whole-proteome self-alignments stop short
  ([measured](../../measurements/blast-real/README.md)), so the truncation belongs to this input.

A fixture of 20 well-behaved proteins made a false claim look exact for months. That is the
[assert-the-claim-you-mean](../../practices/cross-checks.md) rule failing in the direction that is
hardest to notice: the check passed.

Also dropped: "nothing outscores a self-hit". With truncated local alignments a paralog can
legitimately score higher than a one-residue-short self-alignment, so the exact-zero assertion was
measuring the same artifact.

### Pins

| | data tier |
|---|---|
| BLAST+ | `quay.io/aarchbio/blast@sha256:377bfb5d…` (2.16.0, `linux/arm64`) |
| proteome | Ensembl 116 `Homo_sapiens.GRCh38.pep.all.fa.gz`, sha256 `9b43da9265…` — versioned release |
| queries | its own first 1000 records, sha256 `e6732c4cf4…` — derived deterministically by the stage script |

### On x86_64

The same task on same-generation Intel (`c8i.4xlarge`), from bioconda's own x86 build of the same
version — `quay.io/biocontainers/blast@sha256:9dfc69f9…` (2.16.0, `linux/amd64`; not
cosign-signed, so the digest is the pin). Same staged proteome and queries:

| | arm64 | x86_64 |
|---|---|---|
| database sequences | 382,428 | 382,428 |
| queries with any hit / self-rows / at 100% | 931 / 931 / 931 | 931 / 931 / 931 |
| full-length self-alignments | 829 | 829 |
| `hits.tsv` (320,305 rows) sha256 | `1e0fbf94…` | `1e0fbf94…` — **byte-identical** |

The whole 16-thread hit table matches bit for bit, including the 102 one-residue-short
self-alignments, so that truncation is a property of BLAST+ on this data, not of either chip.
`dbinfo.txt` differs only in the `makeblastdb` build date; `smoke-check.txt` only in the wall time.

### Run + verify

```sh
make run RECIPE=blast                  # Graviton4
make run RECIPE=blast ARCH=x86_64      # same spec, x86 pin, writes runs-x86_64/
make ls  RECIPE=blast
```

Expect `smoke-check.txt` with `db_sequences 382428`, `self_rows 931` and `self_rows_100pct 931`.

</details>
