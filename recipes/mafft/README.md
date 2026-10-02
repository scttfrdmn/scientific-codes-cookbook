---
tool: mafft
tool_version: 7.525
image: quay.io/aarchbio/mafft@sha256:f23e4545b6c186ffa31ebbb0a70a051c06ff3e7dcc91853e84f6eced74fa3df9
spawn_version: 0.111.4
last_verified: 2026-10-02
---
# MAFFT — align 906 human GPCRs in three seconds

Aligns every human member of Pfam's `7tm_1` family, taken straight from hmmer's own search output. For anyone aligning a real protein family.

## Run it

```bash
make stage RECIPE=hmmer   # the proteome; mafft reads hmmer's hits.tbl.gz too
make run   RECIPE=hmmer   # produces the family membership this recipe extracts
make run   RECIPE=mafft   # 3 s of mafft, ~2 min billed
make ls    RECIPE=mafft   # gpcr906.fa + aln.fa + smoke-check.txt

mafft --auto --thread 8 gpcr906.fa > aln.fa
```

## Make it yours

| In the recipe | Swap for | What to know |
|---|---|---|
| `7tm_1`'s 906 human members | any family in `hits.tbl.gz` | change one `awk` pattern; the family is whatever hmmsearch found, not a hand-curated set. |
| `--auto` | `--localpair --maxiterate 1000` | `--auto` picked FFT-NS-2 here; L-INS-i is far better and far slower at this size. |
| `--thread 8` | fewer | 3 s either way at this size. Keep it *pinned* — see below. |

**Leave the family** — 906 sequences of ~344 aa is a real alignment and it runs in 3 s, so the whole
recipe is boot. **Scale it** by picking a bigger family or dropping `--auto` for an iterative mode;
both cost real time where this does not.

## Shape, size, cost

`c8g.2xlarge`: **3 s of mafft inside a ~2 min billed window, ~$0.01.**

**No generation table.** Three seconds of work cannot distinguish four chips — a ladder here would
measure boot, the same reason [mash](../mash/README.md) has none. If you need mafft to take real
time, the lever is `--localpair`, not a newer core.

<details>
<summary>As shipped: a conservation identity, a reproducibility result muscle does not share, pins</summary>

### The checks

| observable | assertion | observed |
|---|---|---|
| ids from hmmer | exactly 906 | **906** |
| sequences extracted | == ids from hmmer | **906** |
| input residues | exactly 311,969 | **311,969** |
| aligned sequences | == input | **906** |
| **aligned residues** | **exactly 311,969** | **311,969** |
| **alignment columns** | **exactly 4,340** | **4,340** |

**An alignment may only insert gaps, never alter sequence content** — so the ungapped residue count
of the output must equal the input's, exactly. That is thread-independent, needs no tolerance, and
catches the failure that actually happens: a sequence silently dropped or truncated, which any
sum-of-pairs score band would pass. The uniform row length is the other free structural check: every
row of an alignment is the same length by definition.

**The column count is asserted because it was measured twice.** Two runs of this spec on
byte-identical input returned 4,340 columns both times, so it is exact-or-wrong here.
[muscle](../muscle/README.md) is **not** like this — two runs of its spec on the same bytes gave
5,633 then 5,636 — which is why only this recipe pins its alignment length
([the rule](../../practices/cross-checks.md)). `--thread` is pinned for the same reason: mafft's
progressive alignment depends on the thread count.

### Stop codons, and why they are stripped at extraction

Ensembl pep sequences carry `*` for stop codons — 111 of them across 23 of these 906 proteins, from
readthrough and annotation artefacts. `*` is not an amino acid and MAFFT silently drops it, which
broke the conservation identity on the first run: 312,080 residues in, 311,969 out, with every other
character count preserved exactly.

The fix is to strip `*` at extraction rather than to account for it afterwards, and the reason is the
cross-check: muscle need not handle `*` the way mafft does, so a divergence caused by stop codons
would be a difference in **input handling** masquerading as a difference in alignment. Cleaning the
input makes both tools answer the same question.

### Compared with muscle on identical bytes

muscle reads `runs/mafft/r1/gpcr906.fa` — this recipe's own uploaded extraction, not a second copy,
because the comparison only means anything on identical bytes.

| | columns | ≥50% occupied | ≥90% | gaps | wall |
|---|---|---|---|---|---|
| mafft `--auto` (FFT-NS-2) | **4,340** | 311 | 213 | 92.1% | **3 s** |
| muscle `-super5` | 5,636 | 316 | 281 | 93.9% | 278 s |

**The cores agree, the gap placement does not.** Both tools find ~313 columns occupied by at least
half the family (311 vs 316, 1.6% apart) — sensible for seven transmembrane helices plus conserved
loops — while muscle's alignment is 30% longer overall. That core agreement is an *observation*, not
an assertion: an MSA has no defined precision the way an ML optimum does, so any tolerance would be
chosen to pass rather than justified by the problem. What **is** asserted jointly is that stripping
the gaps from both alignments returns all 906 sequences byte-identical — the same residues, two
independent aligners.

Note that gap-free columns are **0 in both**: no column survives all 906 sequences, because class-A
GPCR termini vary wildly. A gap-free count would have read `0 / 0` and distinguished nothing, which
is why occupancy thresholds replaced it.

### Pins

| | data tier |
|---|---|
| MAFFT | `quay.io/aarchbio/mafft@sha256:f23e4545b6c1…` (7.525, cosign-verified, `linux/arm64`) |
| proteome | Ensembl 116, one protein per gene, sha256 `c753ba28b98b7506…` — staged by [hmmer](../hmmer/README.md) |
| family membership | `runs/hmmer/r1/hits.tbl.gz` — hmmsearch's own tblout, not a curated list |

`awk` and `gzip` do the extraction; both are base-image utilities, not a second scientific tool, so
this is still one tool per task.

### Run + verify

```sh
make stage RECIPE=hmmer
make run   RECIPE=hmmer
make run   RECIPE=mafft
make ls    RECIPE=mafft
```

Expect `smoke-check.txt` with `aligned_residues 311969` and `row_length 4340..4340`.

</details>
