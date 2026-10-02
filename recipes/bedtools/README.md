---
tool: bedtools
tool_version: 2.31.1
image: quay.io/aarchbio/bedtools@sha256:cd1e72a29500369c5576c10e98b2c1723a09a73bde9fb50d80bf4022096b449b
spawn_version: 0.111.4
last_verified: 2026-10-01
---
# bedtools — genome-interval set algebra, and coverage over a real genome

Runs the four set-algebra operations on hand-checkable intervals, then `genomecov` over bwa's whole-genome BAM. For anyone building intervals into a pipeline.

## Run it

```bash
for s in $(make -s spec RECIPE=bedtools); do spawn task run --spec "$s" --wait; done   # both tasks: set algebra (<1 s) then genomecov (146 s)
make ls  RECIPE=bedtools

bedtools merge     -i a.bed
bedtools intersect -a a.bed -b b.bed
bedtools subtract  -a a.bed -b b.bed
bedtools genomecov -ibam aln.sorted.bam      # 48,817,006 records, 3,366 contigs
```

## Make it yours

| In the recipe | Swap for | What to know |
|---|---|---|
| two hand-built BED files on `chr1` | your own intervals | the ops are identical at any scale; these are small so the answers check by hand. |
| bwa's whole-genome BAM | your coordinate-sorted BAM | `-ibam` reads contig lengths from the BAM header, so no `-g` file is needed. |
| these four ops | `closest`, `coverage`, `flank`, `slop`, … | same binary, same shape — intervals in, intervals out. |

**Leave the BED fixture small** — hand-sized intervals are what make the algebra checkable by hand, and a real annotation set would run the same ops and prove nothing more. **The genomecov task is the one at real scale**, where the runtime and the memory actually live.

## Which box — measured, same BAM, 8 vCPU throughout

| generation | instance | `genomecov` | compute $ | billed $ |
|---|---|---|---|---|
| Graviton2 | `c6g.2xlarge` | 286 s | 0.0216 | 0.0317 |
| Graviton3 | `c7g.2xlarge` | 169 s | 0.0136 | 0.0234 |
| Graviton4 | `c8g.2xlarge` | 146 s | 0.0129 | 0.0252 |
| **Graviton5** | `c9g.2xlarge` | **123 s** | **0.0119** | **0.0221** |

**2.33× over four generations** — as much as plane-wave DFT gets, for interval arithmetic over a compressed
BAM, and most of it arrives at Graviton3 (1.69× in one step, then 1.16× and 1.19×). **Read the compute
column, not the billed one:** at ~2 minutes of work this is boot-dominated, so billed ranks Graviton3 ahead
of Graviton4 on noise ([which comparison applies](../../patterns/cost-per-result.md)).

<details>
<summary>As shipped: the hand-derived answers, two conservation identities, pins</summary>

### Task 1 — the set algebra, derivable by hand

`a.bed` = `[0,100] [50,150] [200,300]`, `b.bed` = `[75,125] [250,350]`, genome length 400:

- **merge a** → `[0,100]∪[50,150]` collapse to `[0,150]`; `[200,300]` stands. **2 intervals, 250 bp.**
- **intersect a,b** → `[75,100] [75,125] [250,300]`. **3 intervals, 125 bp.**
- **subtract a,b** → `[0,75] [50,75] [125,150] [200,250]`. **4 intervals, 175 bp.**
- **genomecov a** → depth-0 = 150, depth-1 = 200, depth-2 = 50, **summing to 400 = the genome
  length** — every base counted at exactly one depth.

| observable | assertion | observed |
|---|---|---|
| merge / intersect / subtract intervals | 2 / 3 / 4 | 2 / 3 / 4 |
| merge / intersect / subtract bp | 250 / 125 / 175 | 250 / 125 / 175 |
| genomecov depth-0/1/2 bp | 150 / 200 / 50 | 150 / 200 / 50 |
| genomecov total | 400 = genome length | 400 |

### Task 2 — whole-genome coverage, and what checks it

| observable | assertion | observed |
|---|---|---|
| **genome bases** | **== the reference `.fai` total** | **3,217,346,917** |
| **per-contig bases** | **== the genome aggregate** | **3,217,346,917** |
| covered ≥1× | recorded | **2,164,818,308 (67.2858%)** |
| mean depth | recorded | **1.4875×** |
| max depth | recorded | **17,991** |

The conservation identity scales straight from the hand-checked fixture: every base of all 3,366
contigs is counted at exactly one depth, so the per-contig rows must sum to the `genome` rows. And
the total is checked against an **independent authority** — the sum of contig lengths in
`GRCh38…fa.fai`, the reference's own index, which bedtools never reads. That turns a self-consistency
check into a cross-tool one for the cost of staging a 160 KB file.

**Mean depth 1.4875× is a number another recipe needed.**
[picard](../picard/README.md) explains its 0.87% duplicate rate by this library being "~1.5×
genome-wide"; bedtools measures 1.4875× independently, with a different tool on a different pass over
the same BAM. Neither is asserted — both are library properties — but the explanation is no longer
resting on an unchecked figure.

Every number above came back **identical on all four Graviton generations**, as it must: interval
arithmetic is deterministic, so the chip cannot move it. That is what makes n = 1 per rung
defensible, and it makes the four runs each other's check.

### Pins

| | data tier |
|---|---|
| bedtools | `quay.io/aarchbio/bedtools@sha256:cd1e72a29500…` (2.31.1, cosign-verified, `linux/arm64`) |
| BED fixtures | inline in the task — nothing staged, nothing to pin |
| BAM | `runs/bwa-samtools/r1/aln.sorted.bam` — produced by [bwa-samtools](../bwa-samtools/README.md) |
| contig lengths | `inputs/bwa-real/GRCh38_full_analysis_set_plus_decoy_hla.fa.fai` |

Measured tmpfs high-water mark is **4,359 MB** — essentially just the staged BAM, since the
histogram output is 3 MB. That fits the 8 GiB tmpfs of a 16 GiB box
([staging is half of RAM](../../practices/container-path.md)), which is why this runs on a 2xlarge.

### Run + verify

```sh
make run RECIPE=bedtools
make ls  RECIPE=bedtools
```

Expect `smoke-check.txt` with `genomecov_sum 400`, and `genomecov-smoke-check.txt` with
`genome_bases 3217346917` matching the `.fai` total.

</details>
