---
tool: delly
tool_version: "2.6.0"
images:
  - quay.io/aarchbio/bwa@sha256:19f0eceab80740b821be7ada082d4434acf778912aac658dd1b4c6692dd2e9ba
  - quay.io/aarchbio/samtools@sha256:1191739637fb6f46ef97c02b28f693b25ca3ca61f90e1337f349b7b7cc0be4f7
  - quay.io/aarchbio/delly@sha256:c1d0492cb7d52d56f0a01e790dd1434ba9ddbb47cc5c95cf90dc12c20d5aaeb5
  - quay.io/aarchbio/bcftools@sha256:8171fe74464620a0585cc8998fd9bacbfc04480ac5571229f22f390ecfd5658e
spawn_version: 0.111.1
last_verified: 2026-09-22
---
# Structural variants — a deletion we cut out ourselves

delly calls a 1 kb deletion from paired-end and split-read evidence on Graviton4, against a deletion that was removed from the sample before any read existed. The catalog's first SV recipe, for anyone calling CNVs or rearrangements from short reads.

> **What this covers.** A 20 kb contig, a sample carrying one 1000 bp deletion, 32× paired reads. Alignment, germline SV calling, and locating the same deletion a second way from coverage. Not insertions, inversions, translocations, somatic or population calling, or CNV segmentation.

## Run it

```bash
bwa mem -R '@RG\tID:s1\tSM:sample1' ref.fa R1.fq R2.fq > aln.sam   # delly needs @RG SM
samtools sort -o sample.bam aln.sam && samtools index sample.bam && samtools faidx ref.fa

delly sr -g ref.fa -o sv.bcf sample.bam        # 'sr', not 'call' — see below
bcftools query -f '%CHROM\t%POS\t%INFO/SVTYPE\t%INFO/END\t%INFO/SVLEN\n' sv.bcf
```

Four tasks: bwa builds the fixture and aligns, samtools sorts and independently locates the deletion from coverage, delly calls, bcftools reads the call and cross-checks the two.

## Make it yours

| In the recipe | Swap for | What to know |
|---|---|---|
| 20 kb contig, one planted deletion | your reference + BAM | delly needs a **coordinate-sorted, indexed BAM with `@RG … SM:`** and an indexed reference; it refuses a BAM with no read group. |
| `delly sr` | `delly lr` (long reads), `delly asm`, `delly cnv` | **delly 2.6.0 renamed `call` to `sr`.** Every tutorial online still says `call`, which now errors with `Unrecognized command call` and no hint. |
| one germline sample | tumour/normal | somatic calling is `delly sr` on both BAMs then `delly filter -f somatic`; the shape here doesn't change. |
| 32× uniform coverage | your real depth | SV sensitivity is driven by depth **and** insert-size spread — a library delly can't model (see `MAD` below) silently yields nothing. |

**Leave the fixture:** a single planted deletion in 20 kb makes both the breakpoint and the absence of false positives exactly assertable. **Scale it** to a real BAM when you care about sensitivity; nothing about the commands changes.

## Shape, size, cost

Four tasks on `c8g.large` (2 vCPU / 4 GiB), TTL 15m / 12m / 12m / 12m, caps $0.05 each. Every step is seconds at this size; the windows are almost entirely image pull. **These timings are not compute cost.**

<details>
<summary>As shipped: exact breakpoints, the SVLEN trap, two independent signals, the delly sr rename, pins</summary>

### The checks

| observable | assertion | observed |
|---|---|---|
| fixture identity | `ref_len − sample_len` == the deletion length | **20000 − 19000 = 1000** |
| split-read evidence | `SA:Z:` alignments exist at all | **28** |
| read group | ≥1 `@RG` (delly refuses without it) | **1** |
| zero-coverage runs | exactly 1 in 20 kb | **1** |
| coverage gap | **contained** in the planted deletion | **8005–8998 ⊂ 8001–9000** |
| delly calls | exactly 1, `DEL` / `PASS` / `PRECISE` | **1**, all three |
| delly breakpoints | `POS+1 .. END−1` **equal** the planted deletion | **8001–9000, exact** |
| `\|SVLEN\|` | == `del_len + 1` (delly reports `END−POS`) | **1001** |
| the two signals | coverage gap contained in delly's call | **agree** |

delly's call, in full:

```text
chrS  8000  PASS  DEL  END=9001  SVLEN=-1001  PRECISE  PE=51  SR=13  GT=1/1
```

### The `SVLEN` trap — the proxy that looks right and is off by one

delly follows the VCF convention where **`POS` is the base before the deletion and `END` the base after it**. So the deleted interval is `POS+1 .. END-1` = `8001..9000`, exactly what was cut out. But `|SVLEN|` is `END − POS` = **1001**, one more than the 1000 bases actually deleted.

A reader who writes the obvious check — `test $((-SVLEN)) -eq 1000` — gets a **red failure on a completely correct call**. So this recipe asserts the breakpoints (`POS+1`, `END-1`) against the truth, and separately asserts `|SVLEN| == del_len + 1` as the documented convention. It also records `naive_svlen_check no` in the smoke check, because the useful thing to ship is not just the right assertion but the wrong one *named* — [assert the claim you mean, not the convenient proxy](../../practices/cross-checks.md).

### Two independent signals, and why the cross-check is containment not equality

The deletion is located twice, by mechanisms that share nothing:

- **delly** uses discordant pairs (51) and split reads (13) — reads whose *alignment geometry* is wrong.
- **`samtools depth -a -J`** finds the interval with zero coverage — reads that are *absent*.

They agree, but they cannot agree *exactly*, and the reason is worth more than the check. Split reads pin the breakpoint to the base, which is what `PRECISE` means and what licenses an exact assertion. Coverage cannot: where the flanks happen to share a few bases of micro-homology with the deletion edges, bwa extends alignments into the deleted interval, so the observed gap is **8005–8998 — 994 of 1000 bases**, inset by 4 bp and 2 bp. Nothing is wrong; a coverage gap is simply a lower bound on a deletion.

So the assertion is **containment** (exact, and directional: the gap must lie inside the call), plus ≥98% recovery with that mechanism stated. Asserting equality would fail for a reason unrelated to correctness, and widening it to a symmetric band would hide the direction — which is the informative part. The exactly-one-zero-coverage-run check does the other half of the work: it says no *other* gap exists in 20 kb, which no breakpoint comparison can tell you.

### Two silent failures the recipe asserts against

- **`MAD = 0`.** delly models the insert-size distribution, so it needs one. A fixture with a *fixed* fragment length gives `MAD=0` — not a distribution any real library has — and leaves delly's discordant-pair threshold degenerate. The fixture therefore cycles fragment lengths 360–440 (median 400, `MAD=20`), and the recipe asserts `MAD > 0` and `UniqueDiscordantPairs > 0` **inside the delly task**. Without those, a library delly cannot model produces an empty call set and exit 0.
- **No `@RG`.** delly requires a read group with `SM:`, so `bwa mem -R` is load-bearing rather than cosmetic; task 1 asserts the header carries one before spending the rest of the chain.

### delly 2.6.0 renamed `call` to `sr`

Every guide and paper says `delly call`. On 2.6.0 that prints `Unrecognized command call` and exits — no suggestion, no mention of the replacement. The short-read entry point is now **`delly sr`** (with `lr`, `asm`, `cnv` alongside). Worth checking `delly` with no arguments before trusting any tutorial; the [version a package advertises is not the interface it ships](../../practices/container-path.md).

### Pins (data tier: synthetic / in-code)

| | |
|---|---|
| bwa | `quay.io/aarchbio/bwa@sha256:19f0ecea…` — the same pin [bwa-samtools](../bwa-samtools/README.md) uses |
| samtools | `quay.io/aarchbio/samtools@sha256:11917396…` |
| delly | `quay.io/aarchbio/delly@sha256:c1d0492c…` (2.6.0, HTSlib 1.24) |
| bcftools | `quay.io/aarchbio/bcftools@sha256:8171fe74…` |
| input | none — reference, the deleted sample and its reads are generated in-task by awk from `srand(13)` |

**Why four images.** One tool per image, so the four steps are four tasks. The last one is not optional bookkeeping: **delly writes BGZF-compressed BCF regardless of the output filename** — naming it `sv.vcf` still produces a binary file starting `1f 8b` — and the delly image ships no `bcftools` or `samtools`, so its own output is unreadable where it was written. Reads are deterministic and delly is not a search, so the call is byte-identical across runs (verified twice locally before the exact assertions were written, [the same discipline the assemblers need](../../practices/cross-checks.md)).

Also: spawn's task shell does not inherit the image's `PATH`, so `/opt/conda/bin` must be exported in every task.

### Run + verify

```sh
make run RECIPE=structural-variants
make ls  RECIPE=structural-variants
```

Assertions are `test` calls inside all four tasks. Expect `smoke-check.txt` with `breakpoints_exact yes`, `signals_agree yes`, `sv_calls 1`, and `naive_svlen_check no`.

</details>
