---
tool: delly
tool_version: "2.6.0"
images:
  - quay.io/aarchbio/bwa@sha256:19f0eceab80740b821be7ada082d4434acf778912aac658dd1b4c6692dd2e9ba
  - quay.io/aarchbio/samtools@sha256:1191739637fb6f46ef97c02b28f693b25ca3ca61f90e1337f349b7b7cc0be4f7
  - quay.io/aarchbio/delly@sha256:c1d0492cb7d52d56f0a01e790dd1434ba9ddbb47cc5c95cf90dc12c20d5aaeb5
  - quay.io/aarchbio/bcftools@sha256:8171fe74464620a0585cc8998fd9bacbfc04480ac5571229f22f390ecfd5658e
  - quay.io/aarchbio/manta@sha256:0cae7f334d4a0929f985dfdbf24801de50c421e29fc529d71ba35f342ea1e0d5
spawn_version: 0.111.1
last_verified: 2026-09-22
---
# Structural variants — a deletion we cut out ourselves

delly and manta each call a 1 kb deletion on Graviton4, against a deletion removed from the sample before any read existed. The catalog's first SV recipe, for anyone calling CNVs or rearrangements from short reads.

> **What this covers.** A 20 kb contig, a sample carrying one 1000 bp deletion, 32× paired reads. Alignment, two germline SV callers on the same BAM, and locating the deletion a third way from coverage. Not insertions, inversions, translocations, somatic or population calling, or CNV segmentation.

## Run it

```bash
for s in $(make -s spec RECIPE=structural-variants); do spawn task run --spec "$s" --wait; done
bwa mem -R '@RG\tID:s1\tSM:sample1' ref.fa R1.fq R2.fq > aln.sam   # delly needs @RG SM
samtools sort -o sample.bam aln.sam && samtools index sample.bam && samtools faidx ref.fa

delly sr -g ref.fa -o sv.bcf sample.bam        # 'sr', not 'call' — see below
bcftools query -f '%CHROM\t%POS\t%INFO/SVTYPE\t%INFO/END\t%INFO/SVLEN\n' sv.bcf
configManta.py --bam sample.bam --referenceFasta ref.fa --runDir mrun   # the second caller
mrun/runWorkflow.py -m local -j 2 -g 6                                  # -g is load-bearing
```

Five tasks: bwa aligns, samtools sorts and finds the deletion from coverage, delly calls, bcftools reads the BCF, manta calls the same BAM for comparison.

## Make it yours

| In the recipe | Swap for | What to know |
|---|---|---|
| 20 kb contig, one planted deletion | your reference + BAM | delly needs a **coordinate-sorted, indexed BAM with `@RG … SM:`** and an indexed reference; it refuses a BAM with no read group. |
| `delly sr` | `delly lr` (long reads), `delly asm`, `delly cnv` | **delly 2.6.0 renamed `call` to `sr`.** Every tutorial online still says `call`, which now errors with `Unrecognized command call` and no hint. |
| one germline sample | tumour/normal | somatic calling is `delly sr` on both BAMs then `delly filter -f somatic`; the shape here doesn't change. |
| delly **and** manta | either alone | they agree on the deletion exactly — but **not on how to write it down**, so read the convention table before merging their VCFs. |
| 32× uniform coverage | your real depth | sensitivity follows depth **and** insert-size spread — a library delly can't model (see `MAD` below) silently yields nothing. |

**Leave the fixture:** one planted deletion in 20 kb makes the breakpoint and the absence of false positives exactly assertable. **Scale it** to a real BAM for sensitivity; the commands don't change.

## Shape, size, cost

Five tasks, four on `c8g.large` (2 vCPU / 4 GiB) and **manta on `c8g.xlarge` (8 GiB)** because its merge step demands 4 GiB whatever the genome size. TTL 12–15m, caps $0.05 each. Every step is seconds at this size; the windows are almost entirely image pull. **These timings are not compute cost.**

<details>
<summary>As shipped: exact breakpoints, two callers that disagree on notation, three signals, the delly sr rename, manta's memory floor, pins</summary>

### The checks

| observable | assertion | observed |
|---|---|---|
| fixture identity | `ref_len − sample_len` == the deletion length | **20000 − 19000 = 1000** |
| split-read evidence | `SA:Z:` alignments exist at all | **28** |
| read group | ≥1 `@RG` (delly refuses without it) | **1** |
| zero-coverage runs | exactly 1 in 20 kb | **1** |
| coverage gap | **contained** in the planted deletion | **8005–8998 ⊂ 8001–9000** |
| delly calls | exactly 1, `DEL` / `PASS` / `PRECISE` | **1**, all three |
| manta calls | exactly 1, `DEL` / `PASS` | **1**, both |
| manta deleted interval | `POS+1 .. END` **equals** the planted deletion | **8001–9000, exact** |
| the two callers | identical deleted interval | **agree** |
| their raw `END` / `SVLEN` | differ by exactly 1 (a convention probe) | **differ** |
| delly breakpoints | `POS+1 .. END−1` **equal** the planted deletion | **8001–9000, exact** |
| `\|SVLEN\|` | == `del_len + 1` (delly reports `END−POS`) | **1001** |
| the two signals | coverage gap contained in delly's call | **agree** |

delly's call, in full:

```text
chrS  8000  PASS  DEL  END=9001  SVLEN=-1001  PRECISE  PE=51  SR=13  GT=1/1
```

### Two correct callers, one deletion, two different ways of writing it down

Both callers place the deletion at exactly `8001..9000`. Neither agrees with the other about what to put in the VCF:

| | `POS` | `END` | `SVLEN` | deleted interval |
|---|---|---|---|---|
| **manta** 1.6.0 | 8000 | **9000** | **−1000** | `POS+1 .. END` |
| **delly** 2.6.0 | 8000 | **9001** | **−1001** | `POS+1 .. END-1` |

manta follows the VCF spec: `END` is the **last deleted base** and `|SVLEN|` is the **deleted length**. delly's `END` is the base **after** the deletion and its `|SVLEN|` is `END − POS`. Each is internally consistent, each recovers the planted deletion exactly — and **one arithmetic cannot read both**.

The consequences are not cosmetic. `test $((-SVLEN)) -eq 1000` passes on manta and **red-fails delly on a completely correct call**. Merge the two VCFs with a single formula and every delly deletion comes out 1 bp long and fails to match its manta twin, so the same event is reported twice as two disagreeing variants. This is the sharpest form of [assert the claim you mean](../../practices/cross-checks.md): the claim is *the deleted interval*, and only that is comparable across tools.

So the recipe asserts the **deleted interval** against the truth per caller, using each caller's own convention, then asserts the two intervals are identical — that is the cross-validation. It separately asserts the raw `END` and `SVLEN` fields **differ by exactly 1**, as a deliberate probe: if either tool ever changes convention, that assertion fails and says so, instead of a silent off-by-one appearing in merged output. `naive_svlen_check no` is recorded for the same reason — shipping the wrong assertion *named* is worth as much as shipping the right one.

### Three signals, and why one cross-check is equality and the other containment

The deletion is located three times, by mechanisms that share nothing:

- **delly** uses discordant pairs (51) and split reads (13) — reads whose *alignment geometry* is wrong.
- **manta** assembles candidate breakend contigs and realigns them — a different algorithm on the same bytes, which is why its agreement with delly is worth more than either tool's internal consistency.
- **`samtools depth -a -J`** finds the interval with zero coverage — reads that are *absent*.

The two callers are asserted **equal** on the deleted interval, because split-read and assembly evidence both resolve a breakpoint to the base. Coverage is different:

it agrees but cannot agree *exactly*, and the reason is worth more than the check. Split reads pin the breakpoint to the base, which is what `PRECISE` means and what licenses an exact assertion. Coverage cannot: where the flanks happen to share a few bases of micro-homology with the deletion edges, bwa extends alignments into the deleted interval, so the observed gap is **8005–8998 — 994 of 1000 bases**, inset by 4 bp and 2 bp. Nothing is wrong; a coverage gap is simply a lower bound on a deletion.

So the assertion is **containment** (exact, and directional: the gap must lie inside the call), plus ≥98% recovery with that mechanism stated. Asserting equality would fail for a reason unrelated to correctness, and widening it to a symmetric band would hide the direction — which is the informative part. The exactly-one-zero-coverage-run check does the other half of the work: it says no *other* gap exists in 20 kb, which no breakpoint comparison can tell you.

### manta needs 4 GiB whatever the genome, and macOS Docker hides that

manta's first run on `c8g.large` died with:

```text
Exception: Task memory requirement exceeds full available resources
  mantaWorkflow.py:298  mergeLocusGraph  memMb=self.params.mergeMemMb
```

**pyflow sizes tasks against the memory it detects and refuses to schedule a task larger than that**, and manta's `mergeLocusGraph` asks for 4 GiB — on a 20 kb genome, because the figure is a fixed default, not a function of input size. So manta runs on `c8g.xlarge` while the other four tasks stay on `c8g.large`, and `runWorkflow.py -g 6` tells pyflow what it may use.

The reason this reached a real box at all is the interesting part: **it passed locally under `docker run --memory 3g`.** A cgroup memory limit does not change `/proc/meminfo`, and on macOS the container sees the Docker VM's memory — so manta believed it had plenty. The recipe now prints `MemTotal` as a diagnostic (**7948144 kB** on the `c8g.xlarge`) precisely because that is the number the tool actually reads. This is the memory sibling of the project's existing rule that [local Docker on macOS cannot prove uid or permission behaviour](../../practices/container-path.md) — when the question is *resources*, a green local run is not evidence either.

Because that failure produced no staged output at all the first time, the manta task now writes its diagnostics **before** anything can fail and copies the workflow log to a flat path on exit, so a failing run still delivers evidence rather than an empty prefix.

### Two silent failures the recipe asserts against

- **`MAD = 0`.** delly models the insert-size distribution, so it needs one. A fixture with a *fixed* fragment length gives `MAD=0` — not a distribution any real library has — and leaves delly's discordant-pair threshold degenerate. The fixture therefore cycles fragment lengths 360–440 (median 400, `MAD=20`), and the recipe asserts `MAD > 0` and `UniqueDiscordantPairs > 0` **inside the delly task**. Without those, a library delly cannot model produces an empty call set and exit 0.
- **No `@RG`.** delly requires a read group with `SM:`, so `bwa mem -R` is load-bearing rather than cosmetic; task 1 asserts the header carries one before spending the rest of the chain.

### delly 2.6.0 renamed `call` to `sr`

Every guide and paper says `delly call`. On 2.6.0 that prints `Unrecognized command call` and exits — no suggestion, no mention of the replacement. The short-read entry point is now **`delly sr`** (with `lr`, `asm`, `cnv` alongside). Worth checking `delly` with no arguments before trusting any tutorial; the [version a package advertises is not the interface it ships](../../practices/container-path.md).

### Not adopted: svaba

svaba 1.2.0 was the first choice for the second caller (assembly-based, a single binary, no `bcftools` needed). On this input it **spins at 100% CPU with ~2.4 MiB resident, an empty log and no output files**, for over six minutes on a 20 kb reference and a 6204-read BAM — work that should take seconds. Reproduced twice, including with an explicit `-k chrS` region.

It is recorded here rather than filed, because a hang without a root cause is not a report anyone can act on: no error, no partial output, and no hypothesis yet for whether the trigger is aarch64, the single small contig, or something else. manta answered the same question, so the characterisation was not worth more Graviton time. Anyone hitting the same wall at least knows it is not their input.

### Pins (data tier: synthetic / in-code)

| | |
|---|---|
| bwa | `quay.io/aarchbio/bwa@sha256:19f0ecea…` — the same pin [bwa-samtools](../bwa-samtools/README.md) uses |
| samtools | `quay.io/aarchbio/samtools@sha256:11917396…` |
| delly | `quay.io/aarchbio/delly@sha256:c1d0492c…` (2.6.0, HTSlib 1.24) |
| bcftools | `quay.io/aarchbio/bcftools@sha256:8171fe74…` |
| manta | `quay.io/aarchbio/manta@sha256:0cae7f33…` (1.6.0 — a **Python 2.7** image; `pyflow` is py27 and that is upstream's last release) |
| input | none — reference, the deleted sample and its reads are generated in-task by awk from `srand(13)` |

**Why five images.** One tool per image, so the five steps are five tasks. manta writes a *runDir*, and a directory output cannot be staged on the container path, so the VCF is flattened with `gzip -dc` before stage-out. The last one is not optional bookkeeping: **delly writes BGZF-compressed BCF regardless of the output filename** — naming it `sv.vcf` still produces a binary file starting `1f 8b` — and the delly image ships no `bcftools` or `samtools`, so its own output is unreadable where it was written. Reads are deterministic and delly is not a search, so the call is byte-identical across runs (verified twice locally before the exact assertions were written, [the same discipline the assemblers need](../../practices/cross-checks.md)).

Also: spawn's task shell does not inherit the image's `PATH`, so `/opt/conda/bin` must be exported in every task.

### Run + verify

```sh
make run RECIPE=structural-variants
make ls  RECIPE=structural-variants
```

Assertions are `test` calls inside all five tasks. Expect `smoke-check.txt` with `breakpoints_exact yes`, `signals_agree yes`, `sv_calls 1`, `naive_svlen_check no`, and `smoke-check-manta.txt` with `callers_agree yes`.

</details>
