---
tool: muscle
tool_version: "5.3"
image: quay.io/aarchbio/muscle@sha256:ecfe0f7405a5e3e1237b93202c35bd984aab96e1a3466ef64a6fd0a3b7d5c2e4
last_verified_x86_64: 2026-10-13
images_x86_64:
  - quay.io/biocontainers/muscle@sha256:d8a60b266ec5ff7c3be76e2ed3ffb8d42cb4ecc882d5d6e0af6d5c54a428bcdb
spawn_version: 0.111.4
last_verified: 2026-10-02
---
# MUSCLE — align 906 human GPCRs, on the bytes mafft aligned

Aligns the same `7tm_1` family mafft does, from mafft's own extraction, and agrees with it on the family's core. For anyone choosing an aligner.

> **Use `-super5`, not `-align`.** Measured: `-align` did not finish these 906 sequences inside 90
> minutes; `-super5` takes 278 s. mafft takes 3 s.

## Run it

```bash
spawn task run --spec "$(make -s spec RECIPE=muscle)" --wait   # produces gpcr906.fa; muscle reads the identical bytes
make run RECIPE=muscle   # 278 s on c8g.2xlarge, self-terminating
make ls  RECIPE=muscle   # aln.fa + smoke-check.txt

muscle -super5 gpcr906.fa -output aln.fa -threads 8
```

## Make it yours

| In the recipe | Swap for | What to know |
|---|---|---|
| `-super5` | `-align` | cost grows with length as well as count: 906 GPCRs (~344 aa) exceeded 90 minutes, 919 Pfam sequences (~226 aa) took 26 ([measured](../../measurements/muscle-real/README.md)). |
| the 906 GPCRs | your family | comes from mafft's extraction, which comes from hmmer's search — swap it there, not here. |
| `-threads 8` | anything | pin it, but know it is **not** sufficient for reproducibility — see below. |

**Leave the family** — 906 real sequences is where the `-align`/`-super5` choice actually bites. **Scale it** and that choice bites harder, not softer.

## Shape, size, cost

`c8g.2xlarge`: **278 s of muscle inside a ~6 min billed window, ~$0.03.** `-align` on the GPCRs died
at a 90-minute TTL ([spawn#632](https://github.com/spore-host/spawn/issues/632)). Which box at real
scale, [measured](../../measurements/muscle-real/README.md) (919 Pfam sequences, 8 threads):

| chip | instance | `-align` | `-super5` | billed $/result |
|---|---|---|---|---|
| Graviton4 | `c8g.2xlarge` | 1,549 s | 70 s | **0.142** / **0.011** |
| Xeon 6975P-C | `c8i.2xlarge` | 1,765 s | 74 s | 0.190 / 0.014 |
| EPYC 9R45 | `c8a.2xlarge` | **1,421 s** | **66 s** | 0.176 / 0.014 |

**Graviton4 is cheapest per result; 5.2 GiB peak fits the 16 GiB box.** At fixed threads each mode
repeats byte for byte; Graviton4 and Intel give the same bytes, AMD a different alignment of equal score.

<details>
<summary>As shipped: a cross-tool identity, why the column count is not asserted, pins</summary>

### The checks

| observable | assertion | observed |
|---|---|---|
| input sequences | exactly 906 (mafft's `gpcr906.fa`) | **906** |
| input residues | exactly 311,969 | **311,969** |
| aligned sequences | == input | **906** |
| **aligned residues** | **exactly 311,969** | **311,969** |
| row length | all rows equal | **5,636** (value *not* asserted) |
| **ungapped sequences vs mafft** | **all 906 byte-identical** | **906 / 906** |

**An alignment may only insert gaps, never alter content**, so the ungapped residue count must match
the input exactly. The cross-tool check goes further: strip the gaps from *both* alignments and all
906 sequences must come back byte-identical. Each recipe already balances its own residue count, but
that only proves a tool is self-consistent — this proves two independent aligners agree on *content*,
so a tool that substituted or reordered residues fails even though its own count is fine.

### Why the column count is an observation, not an assertion

Two runs of this exact spec, on byte-identical input, with `-threads 8` pinned, returned
**5,633 and then 5,636 columns**. `-super5`'s subset selection is randomised and pinning threads does
not settle it, so nothing about gap *placement* can be asserted here — only sequence content, which
held across both runs.

[mafft](../mafft/README.md) is the contrast, and it was measured rather than assumed: two runs on the
same bytes gave 4,340 columns both times, so that recipe *does* pin its alignment length. Asserting
muscle's 5,633 from the first run would have been exact once and wrong the next — the subtlest way a
check goes bad ([the rule](../../practices/cross-checks.md)), and the fourth time this catalog has
met it.

### Next to mafft, same 906 bytes

| | columns | ≥50% occupied | ≥90% | gaps | wall |
|---|---|---|---|---|---|
| [mafft](../mafft/README.md) `--auto` | 4,340 | 311 | 213 | 92.1% | **3 s** |
| muscle `-super5` | 5,636 | **316** | **281** | 93.9% | 278 s |

**The cores agree to 1.6%** — 311 against 316 columns occupied by at least half the family, which is
the shape you would expect for seven transmembrane helices plus conserved loops. Muscle's alignment
is 30% longer overall and packs more residues into highly-occupied columns (281 vs 213 at ≥90%).
Treat all of this as observation: an MSA has no defined precision the way an ML optimum does, so a
tolerance on agreement would be picked to pass rather than justified by the problem — the same reason
the mafft↔muscle tree distance in this catalog is reported and not asserted.

Gap-free columns are **0 for both tools**, because no column survives all 906 sequences — class-A
GPCR termini vary too much. That statistic was the first thing measured here and it reads `0 / 0`, so
occupancy thresholds replaced it; a comparison that cannot come out any other way is not a comparison.

### Pins

| | data tier |
|---|---|
| MUSCLE | `quay.io/aarchbio/muscle@sha256:ecfe0f7405a5…` (5.3, cosign-verified, `linux/arm64`) |
| sequences | `runs/mafft/r1/gpcr906.fa` — mafft's own extraction, read byte-for-byte |
| mafft's alignment | `runs/mafft/r1/aln.fa` — the comparison reads the real run's output |

Nothing is staged twice. The family traces back to [hmmer](../hmmer/README.md)'s `hits.tbl.gz`, so the
sequences being aligned are ones hmmsearch found.

### On x86_64

The same task on same-generation Intel (`c8i.2xlarge`), from bioconda's own x86 build of the same
version — `quay.io/biocontainers/muscle@sha256:d8a60b26…` (5.3, `linux/amd64`; not
cosign-signed, so the digest is the pin). It reads the x86 mafft run, whose `gpcr906.fa` and
`aln.fa` are byte-identical to arm64's:

| | arm64 | x86_64 |
|---|---|---|
| input sequences / residues | 906 / 311,969 | 906 / 311,969 |
| aligned residues | 311,969 | 311,969 |
| ungapped sequences vs mafft | 906 / 906 | 906 / 906 |
| row length (observation) | 5,636 | 5,565 |
| columns ≥50% / ≥90% occupied | 316 / 281 | 316 / 282 |

**Both assertions hold exactly; the alignment itself differs, and is expected to.** The column count
is the quantity this page already declines to assert because `-super5` moves it run to run on one
architecture (5,633 then 5,636 on arm64); x86's 5,565 is another draw from the same randomised
subset selection. The core occupancy agrees to within a column, and the content identity — every
ungapped sequence equal to mafft's — is unchanged.

### Run + verify

```sh
make run RECIPE=mafft
make run RECIPE=muscle                  # Graviton4
make run RECIPE=mafft  ARCH=x86_64      # x86: upstream first, then
make run RECIPE=muscle ARCH=x86_64      # same spec, x86 pin, writes runs-x86_64/
make ls  RECIPE=muscle
```

Expect `smoke-check.txt` with `aligned_residues 311969` and `ungapped_identical 906`.

</details>
