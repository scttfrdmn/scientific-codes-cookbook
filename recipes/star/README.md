---
tool: star
tool_version: "2.7.11b"
image: quay.io/aarchbio/star@sha256:90331f64bd73eadaefbebc8d4aecfed3ebed1a7e40b761041acbbcf53b3e13ae
spawn_version: 0.111.1
---
# STAR — a complete RNA-seq run against the whole human genome

Builds the full GRCh38 + Ensembl 116 splice-aware index and aligns a complete 15.8M-read run on Graviton. For anyone doing spliced alignment, and deciding what to pay for.

> **Scope.** Whole primary assembly (3.15 GB) + the whole annotation (4.66 GB uncompressed), one complete run (`ERR188026`). Alignment only — no quantification, no two-pass, no fusion calling.

## Run it

```bash
make stage RECIPE=star   # once: Ensembl 116 primary assembly + GTF + the full run
make run   RECIPE=star   # index ~17 min then align ~1 min; self-terminating
make ls    RECIPE=star   # Aligned.out.bam + smoke-check.txt
```

```bash
STAR --runMode genomeGenerate --runThreadN 32 --genomeDir idx \
     --genomeFastaFiles genome.fa --sjdbGTFfile genes.gtf --sjdbOverhang 100
STAR --runThreadN 32 --genomeDir idx --readFilesIn R1.fq.gz R2.fq.gz \
     --readFilesCommand zcat --outSAMtype BAM Unsorted
```

## The number that decides everything here

| phase | wall | peak RSS | output |
|---|---|---|---|
| `genomeGenerate` | **1047 s** | **71.63 GiB** | 28.6 GiB index |
| align 15.8M reads | **49 s** | ~30 GiB | 2.86 GB BAM |

**The index costs 21× the alignment it enables**, forces the box (~72 GiB to build, ~30 to align), and parallelises poorly — 11.4 of 32 cores average. So the question for STAR is not "how many cores" but **"are you rebuilding this index?"** Build once and publish, and every later alignment is a one-minute job on a much smaller machine; rebuild per sample and you pay 1047 s and a 72 GiB box for 49 s of science.

## Make it yours

| In the recipe | Swap for | What to know |
|---|---|---|
| `ERR188026` | your FASTQs — edit `stage-inputs.sh` | one sample per align; a cohort is the align [fanned out](../../patterns/job-arrays.md) against one shared index. |
| build the index | **a published index** | the single biggest lever on this page. See below — the index is 28.6 GiB, so *how* you share it decides the cost. |
| `--sjdbOverhang 100` | read length − 1 | it is baked into the index, so changing it means rebuilding. |
| `--outSAMtype BAM Unsorted` | `SortedByCoordinate` | sorting inside STAR needs extra RAM; sorting downstream with samtools is usually the cheaper split. |

**Leave the workload** — a real run against a real genome and annotation. **Scale it** by sharing one index across samples, not by buying more cores.

<details>
<summary>As shipped: the measured phases, why the index dominates, sharing 28.6 GiB, pins</summary>

### The checks

| observable | assertion | observed |
|---|---|---|
| input reads | > 5M (a real run) | **15,800,127** |
| uniquely mapped | ≥ 85% | **92.47%** |
| BAM | > 500 MB | **2,864,076,884 bytes** |
| index | built and non-empty | **28.6 GiB, 15 files** |

`--outSAMtype BAM Unsorted` keeps output order input-driven, so the record count is stable for
a fixed input; uniquely-mapped percentage is the assertion that actually catches a broken index
or a wrong `--sjdbOverhang`, because both show up as reads falling to "too short" rather than as
a crash.

### Why index-vs-align is the whole story

Measured on `r8g.8xlarge`, 32 threads, billed 1176 s (**$0.616**):

```text
genomeGenerate   1047 s   71.63 GiB peak   11.40 of 32 cores   -> 28.6 GiB index
align            49 s     ~30 GiB          15.8M reads         -> 2.86 GB BAM
```

Three consequences worth acting on:

- **The build sets the instance, not the aligner.** 71.63 GiB peak means a 128 GiB box at
  minimum; the alignment alone would fit comfortably in 64 GiB. If you are not building, do not
  rent the build's machine.
- **Cores are half-wasted during the build.** 11.4 of 32 average means `genomeGenerate` has long
  serial stretches (the GTF parse and the suffix-array sort). Paying for 32 cores buys less than
  the core count suggests — this is the shape [sizing](../../patterns/sizing.md) calls *the cost
  climbs*, arriving from the serial fraction rather than from communication.
- **Index and align live in one task here on purpose.** A 28.6 GiB index moved between two
  tasks through S3 would cost more than rebuilding it — so the recipe does both in one image,
  which is also the only shape that works if you insist on `aws s3 cp`.

### Sharing 28.6 GiB — the lever this page actually has

The index is immutable, and every sample reads the same bytes. That makes it the textbook case
for **not copying**:

| approach | what each align pays |
|---|---|
| rebuild per sample | **1047 s** + a 72 GiB box |
| `aws s3 cp` the index | 28.6 GiB staged; on the task path `/tmp` is **tmpfs at half of RAM**, so ~57 GiB of RAM exists only to hold the copy |
| **mount it** | metadata only — the bytes stream as STAR touches them |

The copy row is the trap: staging a 28.6 GiB index does not just take time, it *sets the
instance size*, because the copy has to live in RAM-backed `/tmp`. Mounting removes both the
time and the requirement — [measured for bwa](../../measurements/lith-vs-copy/README.md), where
a mount matched a local copy to within 1% of wall time while moving 3,368 bytes instead of
8.9 GiB. STAR is the stronger case simply because its index is 3× larger and its alignment is
20× shorter, so the ratio of data-path cost to real work is far worse.

*(The published-index-plus-mount path is being measured now; this page will carry the numbers
rather than the argument once it lands.)*

### Pins

| | data tier |
|---|---|
| STAR | `quay.io/aarchbio/star@…` (2.7.11b) |
| genome | Ensembl 116 `Homo_sapiens.GRCh38.dna.primary_assembly.fa.gz` — versioned release |
| annotation | Ensembl 116 `Homo_sapiens.GRCh38.116.gtf.gz` |
| reads | ENA `ERR188026_{1,2}.fastq.gz` — the complete run |

**The old chr20 version of this recipe justified itself with a constraint that no longer
exists** — "the spawn task path gets an 8 GiB root disk, and a full human STAR index is ~30 GiB."
`resources.disk_gib` has been in-spec since spawn 0.103.0, and staging space is tmpfs sized from
RAM rather than the root disk ([the container path](../../practices/container-path.md)). A
constraint recorded as a reason is worth re-checking before it outlives the platform.

### Run + verify

```sh
make run RECIPE=star
make ls  RECIPE=star
```

Expect `smoke-check.txt` with `input_reads 15800127`, `pct_unique 92.47`, and a BAM over 500 MB.

</details>
