---
tool: archr
tool_version: "1.0.3"
image: quay.io/aarchbio/r-archr@sha256:a71267afe8ca5a73e10e62b563d88b959bdec10cc0faedfc71d2410652376c6a
images_x86_64:
  - quay.io/biocontainers/r-archr@sha256:e92cb33540497f839ab8d912392c5be5a1a3cfc83c96ed4ba04d9df7c7b70849
spawn_version: 0.127.0
last_verified: 2026-10-16
last_verified_x86_64: 2026-10-16
---
# ArchR — scATAC fragments to Arrow files, tiles, clusters and marker tiles, on a planted genome

Runs ArchR's core scATAC path (Arrow files, TileMatrix, iterative LSI, clustering, marker tiles and genes) on a synthetic genome whose every count is planted, on Graviton4 and x86. For anyone moving an ArchR analysis onto AWS.

## Run it

```bash
make stage RECIPE=archr        # once: a 20 Mb genome, 400 genes, 300 cells of fragments (bgzip + tabix)
spawn task run --spec "$(make -s spec RECIPE=archr)" --wait
make ls RECIPE=archr
```

```r
# excerpt of archr.R, the staged task script
genomeAnnotation <- SimpleList(genome = "nullGenome", chromSizes = chromSizes, blacklist = blacklist)
geneAnnotation <- SimpleList(genes = gGR, exons = gGR, TSS = tssGR)      # plain GRanges, no TxDb
createArrowFiles(inputFiles = c(planted = "planted.fragments.tsv.gz", flat = "flat.fragments.tsv.gz"),
  sampleNames = c("planted", "flat"), geneAnnotation = geneAnnotation, genomeAnnotation = genomeAnnotation,
  minTSS = 0, minFrags = 500, addTileMat = TRUE, TileMatParams = list(binarize = FALSE),
  addGeneScoreMat = TRUE, threads = THREADS, force = TRUE)
proj <- addIterativeLSI(proj, useMatrix = "TileMatrix", name = "IterativeLSI", iterations = 2,
  varFeatures = 25000, dimsToUse = 1:30, seed = SEED, threads = THREADS, force = TRUE)
proj <- addClusters(proj, reducedDims = "IterativeLSI", method = "Seurat", name = "Clusters",
  resolution = 0.8, seed = SEED, force = TRUE)
```

## Make it yours

| In the recipe | Swap for | What to know |
|---|---|---|
| `genome = "nullGenome"` + GRanges | `addArchRGenome("hg38")` | **this image ships no BSgenome, TxDb or OrgDb**, so `hg38` would download at run time. `nullGenome` is ArchR's own no-sequence path; it covers everything here, not peak calling, motifs or GC bias, which read sequence. |
| planted `genes`/`exons`/`TSS` | your GTF as GRanges | needs a `symbol` column; **gene lengths must vary** — see below. |
| two fragment files | your Cell Ranger `fragments.tsv.gz` | must be bgzip + tabix; ArchR reads it by tabix region. |
| `addArchRThreads(1)`, `seed = 1` | more threads | one thread and a fixed seed make the LSI and clusters byte-identical run to run; more threads speed Arrow creation. |
| `minTSS = 0`, `minFrags = 500` | the defaults (4, 1000) | lowered so the background-only control cells survive; real data wants the defaults. |

**Leave the fixture.** Every number ArchR reports here has a planted or recountable answer, which a real dataset cannot give. **Scale it** for sizing only: the claims hold, and Arrow creation is what grows.

## Shape, size, cost

One task on `m8g.large` (2 vCPU / 8 GiB), TTL 60m as a **backstop**, `cost_limit` $0.35 as the guard. ArchR takes **95 s** on Graviton4 and **137 s** on `m8i.large`, at 1.4 GB peak RSS; the task is ~4.5 min end to end, and boot plus the 1 GB image pull are most of it ([these timings are not compute cost](../../patterns/layout-and-effective-cost.md)).

<details>
<summary>As shipped: every count recounts exactly, clusters recover the planted populations (ARI 1), planted tiles and genes top the marker test, identical across chips where it should be</summary>

### The fixture

A 20 Mb genome (`chr1`–`chr3`), 400 genes of 9–16 kb one every 50 kb, and five 2 kb blacklist
regions, generated in staging from `random.seed(20261016)` and sha256-pinned with the task script.
**`planted`**: 240 cells, 120 of population A and 120 of B. Each cell gets uniform background in three
size classes (<147, 147–293, ≥294 bp), 250–350 fragments with one end within 50 bp of a TSS, 250–350
fragments inside its own population's 200 tiles (10 in each of 20 population genes), and 3–8 fragments
of width 5, 9, 2001 or 2500, which ArchR's 10–2000 bp window must drop. **`flat`**: 60 cells of
background only. Every per-cell count is written to `cells.tsv` at generation.

### The checks

The recount reads the raw fragment text with `data.table`, not ArchR: insertions at `start+1` and
`end` (ArchR's tabix convention), tile `= pos %/% 500`.

| observable | assertion | observed (both chips) |
|---|---|---|
| staged fixture + script | match pins | 10 of 10 |
| cells | the 300 planted, and nothing else | 300 |
| **`nFrags`** | **== planted in-window count, every cell** | **2,729–3,924; out-of-window dropped** |
| **`nMonoFrags`/`nDiFrags`/`nMultiFrags`** | **== planted size classes, every cell** | exact |
| **`TSSEnrichment`** | **== recompute of `2·(w/101)/max(f/100, 0.2)`, exactly; finite** | median **32.65** |
| control: background-only cells | planted ≥ 5× flat | flat **0.891**, ratio 36.6 (uniform insertions give 1 by construction) |
| `ReadsInTSS` / `ReadsInPromoter` / `ReadsInBlacklist` | == independent counts | exact; 848 in blacklist |
| **TileMatrix** (`binarize = FALSE`) | **== independent per-tile insertion count, every entry** | 40,003 tiles × 300 cells, sum 2,087,038 |
| blacklisted tiles | all zero, although insertions land there | 0 of 848 |
| GeneScoreMatrix | one row per gene, every entry finite | 400, 0 non-finite |
| **clusters vs planted** | **ARI == 1** | **1.000000** (`C1` = 120 B, `C2` = 120 A) |
| control: shuffled labels | \|ARI\| < 0.05 | −0.003 |
| **marker tiles, each population** | **all 200 planted pass FDR ≤ 0.01 & Log2FC ≥ 1, and are exactly the top 200 by FDR** | worst planted 3e-11 / 2e-10; A has 1 off-target passer at FDR 0.0084 |
| **marker genes, each population** | **all 20 planted pass, and are exactly the top 20** | worst planted 3e-31; best off-target 0.018 |
| control: shuffled labels | no marker tile passes | 0 (min FDR 0.974) |
| R binary architecture | == host `uname -m` | aarch64 / x86_64 |

The marker claim is recall plus rank, not set equality: FDR 0.01 bounds the false-discovery
*proportion*, so one background tile among ~200 discoveries is what BH promises, not a defect.

### Across architectures

`cell_qc.tsv` and `clusters.tsv` are **byte-identical** between Graviton4 and `m8i`. Marker log2FC and
FDR agree to **≤ 4.2e-15 relative** (last-bit differences in 1,541 of 40,003 FDRs; same rankings, same
sets). The **iterative LSI embedding is not reproducible across machines**: the first five dimensions
differ by up to 4.2 on values up to 5.2, even after allowing for sign flips, while both chips land on
the same partition. Iterative LSI is a path (feature selection, sub-clustering, then SVD), so assert on
the partition and never on the coordinates. One thread and `seed = 1` make it byte-identical run to run
on one machine.

### Gene lengths must vary

ArchR 1.0.3's gene-body weight is `1 + m·(geneScaleFactor − 1)/(max(m) − min(m))` with `m = 1/width`.
If every gene has the same length the denominator is zero, and the GeneScoreMatrix is **all NaN with no
error**. The failure only appears later, from `getMarkerFeatures`:

```text
Error in if (all(mColSums == 1) & is.null(normBy)) { : missing value where TRUE/FALSE needed
```

A real annotation never trips this, but a synthetic or single-gene-length one does, which is why the
recipe asserts the matrix is finite.

### Pins

| | |
|---|---|
| fixture | synthetic, generated by `stage-inputs.sh` (build tier), sha256-pinned in `fixture.sha256` with `archr.R` |
| arm64 | `quay.io/aarchbio/r-archr@sha256:a71267af…` (1.0.3), cosign-verified against `playgroundlogic/aarchbio` (the signature covers the manifest-list digest `b3d6392e…`, so verify the tag and pin the arm64 digest) |
| x86_64 | `quay.io/biocontainers/r-archr@sha256:e92cb335…` (1.0.3, `linux/amd64`, bioconda's own build, data.table 1.17.8 vs 1.18.6.1; not cosign-signed, so the digest is the pin) |

### Run + verify

```sh
make stage RECIPE=archr
make run RECIPE=archr                  # Graviton4
make run RECIPE=archr ARCH=x86_64      # same spec, x86 pin, writes runs-x86_64/
aws s3 cp "s3://$(make -s print-bucket)/runs/archr/r1/smoke-check.txt" -
```

Expect `checks: 28 ok, 0 failed` and `ArchR OK`. ArchR's own `ArchRLogs/` and `QualityControl/` ship
as `archr-logs.tar.gz`. Check the bucket regardless ([exit 0 isn't proof](../../practices/container-path.md)).

### Not covered

**Peak calling** (`addReproduciblePeakSet`): this image has no MACS2, and the `nullGenome` path has no
sequence for ArchR's GC-matched backgrounds, so **motifs, chromVAR deviations and footprints** are out
too. **UMAP, imputation (MAGIC), integration with scRNA, trajectories**. **Real data**: no 10x fragment
file is staged, so there is no biological answer here, only planted ones.

</details>
