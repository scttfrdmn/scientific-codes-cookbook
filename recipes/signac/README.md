---
tool: signac
tool_version: "1.17.1"
image: quay.io/aarchbio/r-signac@sha256:80fffb0fa5c788d0cae11b64b273b7eb335c12a03d291838ec0921220907d283
images_x86_64:
  - quay.io/biocontainers/r-signac@sha256:1455a0dd29e8b8b16f2c005aa50a3ee8899606a068f594d59e273ef7c7ec695d
last_verified_x86_64: 2026-10-16
spawn_version: 0.127.0
last_verified: 2026-10-16
---
# Signac — scATAC-seq counts, QC, TF-IDF and LSI, each checked against an exact answer

Runs Signac's fragment-file pipeline (counting, FRiP, nucleosome signal, TSS enrichment, gene activity, TF-IDF, LSI) on a planted two-population fixture, on Graviton4 and x86. For anyone analysing scATAC-seq in R.

## Run it

```bash
make stage RECIPE=signac          # once: builds and pins the planted fragments file
spawn task run --spec "$(make -s spec RECIPE=signac)" --wait
make ls RECIPE=signac

frag <- CreateFragmentObject("fragments.tsv.gz", cells = cells)
fm   <- FeatureMatrix(frag, features = peaks, cells = cells)
obj  <- CreateSeuratObject(CreateChromatinAssay(fm, fragments = frag, annotation = anno), assay = "peaks")
obj  <- TSSEnrichment(NucleosomeSignal(obj)); obj <- RunSVD(FindTopFeatures(RunTFIDF(obj), min.cutoff = "q0"))
```

## Make it yours

| In the recipe | Swap for | What to know |
|---|---|---|
| planted `fragments.tsv.gz` | Cell Ranger ATAC's `fragments.tsv.gz` | same five columns; it must be bgzipped and tabix-indexed (`.tbi` beside it). |
| 60 planted peaks | your peak set (MACS2, Cell Ranger) | `CallPeaks` needs MACS2, which is not in this image; call peaks elsewhere and pass a `GRanges`. |
| a 5-gene `GRanges` annotation | `GetGRangesFromEnsDb(EnsDb.Hsapiens.v86)` | any `GRanges` with `gene_name`, `gene_id`, `gene_biotype`, `tx_id`, `type` works, so no download is needed. Only `protein_coding` TSSs are scored. |
| k-means on LSI 2–10 | `FindNeighbors` + `FindClusters` | **Seurat is not in this image** (Signac only suggests it), so clustering, `FindMarkers` and UMAP need an image that carries both. |
| `NucleosomeSignal`, `TSSEnrichment` | `ATACqc` | both are deprecated in 1.17 in favour of `ATACqc`, which shells out to `fragtk` — also not in the image. |

**Leave the fixture.** Every number it produces has a closed form or an independent recount, which a real dataset cannot give. **Scale it** by pointing the same calls at a real fragments file: nothing here depends on size except memory.

## Shape, size, cost

One task on `m8g.large` (2 vCPU / 8 GiB), TTL 40m as a backstop with `cost_limit` $0.15 as the guard. The R work takes **7 s on Graviton4 and 9 s on x86**, inside about 2 minutes of task, most of it Docker install and a ~700 MB image pull. **These timings are not compute cost** ([layout](../../patterns/layout-and-effective-cost.md)).

<details>
<summary>As shipped: every count equal to an independent recount, QC equal to the planted closed form, LSI recovers the populations exactly, identical across chips</summary>

### The checks

Every Signac output is compared with a recount from the raw fragments file in plain R (no
Signac, no GenomicRanges), with the closed form of the planted design, or with planted labels.
Integer and ratio checks are `==`, and every float check has a stated reason for its tolerance.
Each comparison goes through `isTRUE()`, so a NaN fails.

| observable | assertion | Graviton4 (`m8g`) | x86 (`m8i`) |
|---|---|---|---|
| staged fixture + `signac.R` | match sha256 pins in the spec | 6 of 6 | 6 of 6 |
| `CountFragments`, 220 barcodes | records, `sum(col5)`, `<147`, `148..293` all `==` recount | 39,766 / 49,688 / 17,942 / 17,537 | same |
| length-bin conservation | records `==` nfr + mono + `≥295` + `{147, 294}` | holds; **611 in neither bin** | same |
| **`FeatureMatrix`**, 60 × 200 | **`==` half-open overlap recount, cells only, col5 ignored** | **13,784, 0 cells differ** | same |
| `FeatureMatrix(process_n = 7)` | `==` default chunking | identical | identical |
| `FRiP` | `==` peak fragments / records | 0.2441–0.4862 | same |
| `NucleosomeSignal` | `==` mono / nfr | 0.5521–1.4045 | same |
| **`TSSEnrichment`**, fast and full | **planted `(C/1001)/(F/200)`, rel ≤ 1e-12; lncRNA decoy excluded** | **2.1e-16 both** | same |
| TSS separates the populations | min A > max B | A 3.45–4.85, B 0.75–1.30 | same |
| `GeneActivity` | `==` body + 2 kb upstream recount, strand-aware, 4 protein-coding genes | 10,874 | same |
| `RunTFIDF(method = 1)` | `log1p(x/colSum · ncol/rowSum · 1e4)`, max abs ≤ 1e-12 | 8.9e-16 | same |
| `RunSVD` singular values | vs `base::svd`, rel ≤ 1e-5 (Signac's own `tol`) | 1.3e-15 | 1.2e-15 |
| `RunSVD` triplets | `‖Av − du‖/d ≤ 1e-5` | 3.7e-16 | 3.8e-16 |
| **LSI 2–10 → k-means (k = 2)** | **ARI `== 1` vs planted labels** | **1** | **1** |
| control: each peak permuted across cells | \|ARI\| < 0.1 | −0.0041 | −0.0041 |
| `AccessiblePeaks(idents = "A")` | `==` recount; all 20 A-only, no B-only | 40 (20 A-only + 20 shared) | same |

`qc.tsv` (FRiP, nucleosome signal, both TSS scores, per cell) and `peaks.mtx` are
**byte-identical across the two chips**. The only values that differ are the SVD's error terms,
at 1e-15. LSI is a fixed point (a singular value decomposition), so kernel choice moves only the last
bits, and the singular values print the same to eight digits on both chips.

### What the fixture plants

200 cells (100 per population) and 20 non-cell barcodes. 60 peaks of 500 bp on chr1: 20 open
only in A (90% of A cells, no B cells), 20 only in B, and 20 shared (60% of all cells). There are
30 background fragments per cell, and one fragment each of length 146, 147, 294 and 295. On the
shared peaks, 80 fragments touch a peak edge without overlapping it and 40 overlap it by 1 bp.
A tenth of records carry a duplicate count of 2–5. The non-cell barcodes sit in the specific
peaks, so a leak into the cell matrix would break the counts. On chr2 there are 4 protein-coding
TSSs (two on each strand) with cuts planted at least 10 bp from every window edge, plus a
`lncRNA` decoy carrying 60 central cuts per cell. Everything comes from `random.seed(20261016)`,
is bgzipped and tabix-indexed with htslib 1.24 in staging, and regenerates byte-identically.

### Signac's mononucleosome bin has a hole

The docs give the bins as `< 147` (nucleosome-free) and 147–294 bp (mononucleosomal). The C++
(`src/group.cpp`, tag `1.17.1`) uses `end - start > 147` and `< 294`, so **a fragment of exactly
147 or 294 bp is counted in neither bin**. The planted boundary lengths show this, and the
conservation row above accounts for every one of them. The effect is small on real data, but
`nucleosome_signal` is not the ratio the docs describe.

### Pins

| | |
|---|---|
| fixture | `fragments.tsv.gz` `8f7c8d49…`, `.tbi` `488ac54d…`, `peaks.bed`, `genes.tsv`, `truth.tsv` — synthetic, built in `stage-inputs.sh`, pinned in the spec |
| arm64 | `quay.io/aarchbio/r-signac@sha256:80fffb0f…` (1.17.1, R 4.5.3), cosign-verified against `playgroundlogic/aarchbio`; the signature covers the index, and this pin is its arm64 entry |
| x86_64 | `quay.io/biocontainers/r-signac@sha256:1455a0dd…` (`1.17.1--r45h119cb76_1`, `linux/amd64`, bioconda's own build; not cosign-signed, so the digest is the pin) |

### Run + verify

```sh
make stage RECIPE=signac
spawn task run --spec "$(make -s spec RECIPE=signac)" --wait
make run RECIPE=signac ARCH=x86_64      # same spec, x86 pin, writes runs-x86_64/
aws s3 cp "s3://$(make -s print-bucket)/runs/signac/r1/smoke-check.txt" -
```

The task fails on a pin mismatch, any check above, a missing `SIGNAC OK`, or an empty output. Check the
bucket regardless ([exit 0 isn't proof](../../practices/container-path.md)). The checks live in
`signac.R`. It is staged, not inlined, because inlining it would push the spec past EC2's 16 KB
user-data limit.

### Not covered

**Clustering, differential accessibility and UMAP** (`FindClusters`, `FindMarkers`, `RunUMAP`): Seurat
is not in the image. **Peak calling** (`CallPeaks`): needs MACS2. **`ATACqc`**: needs `fragtk`.
**Motifs and chromVAR** (`AddMotifs`, `RunChromVAR`): need a `BSgenome` and JASPAR, both downloads.
**Plots** (`CoveragePlot`, `TSSPlot`). **Peak-to-gene links** (`LinkPeaks`): needs a paired RNA assay.

</details>
