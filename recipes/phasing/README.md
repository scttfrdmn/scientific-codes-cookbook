---
tool: whatshap
tool_version: "2.8"
images:
  - quay.io/aarchbio/bwa@sha256:19f0eceab80740b821be7ada082d4434acf778912aac658dd1b4c6692dd2e9ba
  - quay.io/aarchbio/samtools@sha256:1191739637fb6f46ef97c02b28f693b25ca3ca61f90e1337f349b7b7cc0be4f7
  - quay.io/aarchbio/whatshap@sha256:2cfeba1f120ca6ef706d244cac8f4957a6952e78d859e497e6da64eac9a0fa4a
spawn_version: 0.111.1
last_verified: 2026-09-22
---
# Phasing — haplotypes recovered from reads we built them into

whatshap assigns heterozygous variants to haplotypes on Graviton4, scored against two haplotypes that existed before any read did. The catalog's first phasing recipe, for anyone who needs `1|0` rather than `0/1`.

> **What this covers.** A 10 kb contig, two haplotypes differing at exactly 6 sites spaced 40 bp apart, 40× reads split between them. Read-backed phasing of one sample and scoring it with `whatshap compare`. Not pedigree phasing, population/reference-panel phasing, long reads, or haplotagging a BAM.

## Run it

```bash
for s in $(make -s spec RECIPE=phasing); do spawn task run --spec "$s" --wait; done
bwa mem -R '@RG\tID:s1\tSM:sample1' ref.fa reads.fq > aln.sam   # SM must match the VCF sample
samtools sort -o sample.bam aln.sam && samtools index sample.bam && samtools faidx ref.fa

whatshap phase -o phased.vcf --reference ref.fa variants.vcf sample.bam
whatshap compare --names truth,whatshap truth.vcf phased.vcf     # score it, don't diff it
```

Three tasks: bwa builds the fixture and aligns, samtools sorts and indexes, whatshap phases and scores itself against the planted haplotypes.

## Make it yours

| In the recipe | Swap for | What to know |
|---|---|---|
| 6 het sites 40 bp apart | your VCF + BAM | **spacing is the whole game**: whatshap can only link variants that a single read (or pair) covers together. Sites further apart than your read length produce many small blocks, or none. |
| 100 bp single-end reads | paired-end, or long reads | pairs extend linkage across the insert; PacBio/ONT is what makes chromosome-scale blocks possible. Same command. |
| `whatshap phase` | `whatshap phase --ped` | with a trio, pedigree phasing links variants no read spans — a different and much longer reach. |
| homozygous sites absent | your real VCF | whatshap only phases het calls; hom sites are passed through unchanged and are not "failures to phase". |

**Leave the fixture:** 6 sites make every haplotype assignment checkable by hand and the switch-error count exactly zero or not. **Scale it** to a real VCF — and if you get one block per variant, look at spacing versus read length before suspecting the tool.

## Shape, size, cost

Three tasks on `c8g.large` (2 vCPU / 4 GiB), TTL 15m / 12m / 12m, caps $0.05 each. whatshap reports `Maximum memory usage: 0.053 GB` and sub-second phasing at this size; the windows are almost entirely image pull. **These timings are not compute cost.**

<details>
<summary>As shipped: zero switch errors, why the flipped phasing is also correct, the linkage requirement, pins</summary>

### The checks

| observable | assertion | observed |
|---|---|---|
| fixture identity | the two haplotypes differ at exactly the planted sites, nowhere else | **6** |
| reads spanning ≥2 variants | > 0 (else phasing is impossible, not merely poor) | **88** |
| variants phased | all of them | **6 of 6** |
| phase sets | exactly 1 — all six linked into one block | **1** |
| switch errors vs planted | **0** | **0** |
| block-wise Hamming distance | **0** | **0** |
| different genotypes | **0** | **0** |
| the same phasing, globally flipped | **also 0** switch errors | **0** |

whatshap's output, one block spanning all six sites:

```text
chrH  5000  1|0:5000     chrH  5120  0|1:5000
chrH  5040  0|1:5000     chrH  5160  1|0:5000
chrH  5080  1|0:5000     chrH  5200  0|1:5000
```

### Phase is defined only up to a global flip — so the recipe proves it rather than claiming it

Which haplotype gets called `1|` and which `|0` is **arbitrary**. Flip every genotype in a block and the phasing is exactly as correct: it still says sites 1, 3, 5 travel together and 2, 4, 6 travel together, which is the entire content of a phasing. Labels carry no information.

That makes `diff` against a truth VCF the wrong instrument, and the recipe demonstrates it by scoring the emitted phasing *and its exact complement*:

| scored against the planted haplotypes | `whatshap compare` switch errors | naive GT match |
|---|---|---|
| as whatshap emitted it | **0** | 6 of 6 |
| every genotype flipped | **0** | **0 of 6** |

A raw genotype comparison calls the flipped phasing wrong at **every single site**. `whatshap compare` calls it perfect, because it scores *switch errors between adjacent pairs* — whether consecutive variants stay on the same haplotype — which is invariant under a global flip by construction. So the recipe asserts zero switch errors for both, and the second assertion is what makes the first one non-flaky: it shows the metric cannot be fooled by an orientation this fixture has no right to expect.

This is the same discipline as [matching the modes before comparing](../../practices/cross-checks.md), in its sharpest form — the tool ships the correct comparison, and reaching for `diff` instead manufactures a failure that is purely notational.

### Linkage is the requirement, and whatshap says so out loud

whatshap's log is worth reading rather than skipping:

```text
Found 120 reads covering 6 variants
Kept 88 reads that cover at least two variants each
Selected 30 most phase-informative reads covering 6 variants
Largest block contains 6 variants (100.0% of accessible variants)
```

**A read covering one variant carries no phase information at all** — it is discarded, and that is correct. Phasing is a statement about pairs, so everything depends on reads that span more than one site. The fixture therefore spaces the six sites 40 bp apart against a 100 bp read, so each read links two or three; the recipe asserts the "cover at least two variants" count is nonzero, because a VCF whose variants are further apart than the reads yields a run that exits 0 with every variant in its own singleton block and nothing phased. That is the realistic silent failure here, and it looks like success.

`--reference ref.fa` turns on re-alignment-based allele detection, which is what makes the calls robust rather than a naive base look-up at the variant position.

### Pins (data tier: synthetic / in-code)

| | |
|---|---|
| bwa | `quay.io/aarchbio/bwa@sha256:19f0ecea…` — the same pin [bwa-samtools](../bwa-samtools/README.md) uses |
| samtools | `quay.io/aarchbio/samtools@sha256:11917396…` |
| whatshap | `quay.io/aarchbio/whatshap@sha256:2cfeba1f…` (2.8) |
| input | none — reference, both haplotypes, the reads and the planted phase are generated in-task by awk and bash from `srand(29)` |

Three images, three tasks: whatshap reads a BAM and cannot make one, and `bwa mem -R` is load-bearing because whatshap matches the BAM's `@RG … SM:` to the VCF's sample column — task 1 asserts the header carries one before anything downstream spends money.

Also: spawn's task shell does not inherit the image's `PATH`, so `/opt/conda/bin` must be exported in every task.

### Run + verify

```sh
make run RECIPE=phasing
make ls  RECIPE=phasing
```

Assertions are `test` calls inside tasks 1 and 3. Expect `smoke-check.txt` with `switch_errors 0`, `phase_sets 1`, `flipped_switch_errors 0`, and `naive_gt_match_flipped 0 of 6`.

</details>
