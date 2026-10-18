# Signac on a real workload: a whole 10x scATAC run, Graviton4 vs Intel vs AMD

> **257.6 million fragments, 10,246 cells: every count Signac makes equals an independent awk
> recount, and that recount equals Cell Ranger's own per-barcode peak count for all 573,155
> barcodes.** All three chips produce byte-identical counts and QC. AMD is fastest and Graviton4
> cheapest per result. Two tabix-bound, single-threaded steps take about 60% of the run, so the
> other three vCPUs sit idle on all three chips.

Workload: the **complete** 10x Genomics `10k_pbmc_ATACv2_nextgem_Chromium_Controller` run
(Cell Ranger ATAC 2.1.0, GRCh38), the dataset of Signac's own PBMC vignette. It is one 10x lane,
the unit a lab gets back and analyses, not a subset. It runs through the vignette's QC and
reduction path:
1. `CountFragments` on the whole file;
2. `FeatureMatrix` over Cell Ranger's 165,434 peaks;
3. `FRiP`, `NucleosomeSignal` and `TSSEnrichment`;
4. `GeneActivity` over 19,628 protein-coding genes;
5. `RunTFIDF`, `FindTopFeatures(min.cutoff = "q0")` and a 50-component `RunSVD`.

The annotation is Ensembl 98, the gene build under Cell Ranger's `GRCh38-2020-A`. It is read
from the GTF into the `GRanges` columns `GetGRangesFromEnsDb` gives, because this image has no EnsDb.

## Run it

```sh
export AWS_PROFILE=aws
bash measurements/signac-real/stage-inputs.sh "$COOKBOOK_BUCKET"    # fetch + recount, on AWS
for a in arm64 x86_64 amd; do
  spawn task run --spec "$(python3 scripts/spec_arch.py signac $a "$COOKBOOK_BUCKET" "$(date +%s)" measurements/signac-real/02-signac.task.json)" --wait
done
python3 -I measurements/signac-real/crosschip.py arm64/ x86_64/ amd/     # each chip's r1/ outputs, copied down
```

`01-reference.task.json` runs once, on Graviton. It fetches the five files on AWS, checks them
against `pins.sha256` (and the GTF against Ensembl's own `CHECKSUMS`), then recounts in POSIX
awk with no Signac, R or htslib (`bins.awk`, `overlap.awk`). That took 1,402 s for two streaming
passes. `02-signac.task.json` runs `signac-real.R` on each chip against those recounts. The x86 legs use
`quay.io/biocontainers/r-signac`, the recipe's `images_x86_64` pin: the same 1.17.1 package,
bioconda's linux-64 build.

## Results

4 vCPU / 32 GiB on all three chips, `OPENBLAS_NUM_THREADS=4`, same input bytes:

| chip | instance | tool wall | billed | peak RSS | $/hr | compute $/result | **billed $/result** |
|---|---|---|---|---|---|---|---|
| Graviton4 | `r8g.xlarge` | 1652 s | 1848 s | 16.74 GiB | 0.2356 | 0.1081 | **0.1209** |
| Intel Xeon 6975P-C | `r8i.xlarge` | 1842 s | 2028 s | 17.88 GiB | 0.2778 | 0.1421 | **0.1565** |
| AMD EPYC 9R45 | `r8a.xlarge` | **1456 s** | 1603 s | 17.77 GiB | 0.3195 | 0.1292 | **0.1423** |

*Tool wall* is `signac-real.R` from start to finish, checks included. *Billed* runs from instance
launch to terminate. Peak RSS is the R process's `VmHWM`.

| step (s) | Graviton4 | Intel | AMD |
|---|---|---|---|
| `CountFragments` | 94 | 105 | 93 |
| `FeatureMatrix` | 463 | 534 | 457 |
| `TSSEnrichment` | 289 | 310 | 232 |
| `GeneActivity` | 503 | 600 | 466 |
| `RunTFIDF` + `RunSVD` | 130 | 137 | 94 |

- **The run is serial and tabix-bound.** `FeatureMatrix` and `GeneActivity` are 58–63% of the wall
  on every chip. They do one `scanTabix` per 2,000-region chunk, single-threaded unless a
  `future` plan is set, so three of the four vCPUs are idle for most of the bill. The box is
  sized by memory (17–18 GiB peak, so `r8`; a 16 GiB `m8` would not hold it), not by cores.
- **AMD is 1.13× faster than Graviton4 and Intel 0.90×.** The 8a vCPUs are whole cores
  (`thread_siblings_list` 0) and the 8i vCPUs are SMT threads (`0,2`). With one busy thread, that
  matters less here than per-core speed does. AMD's lead is largest in `TSSEnrichment` and the TF-IDF/LSI steps (1.25× and 1.38×).
- **Graviton4 is cheapest per result**: $0.121 billed against $0.157 on Intel (23% less) and
  $0.142 on AMD (15% less), because its lower hourly price outweighs AMD's speed.
- n = 1 per chip. The counts are deterministic: the failed first Graviton run (see Caveats)
  printed the same md5s and singular values as the second. So the timings are of one fixed
  computation, not of one draw.

## What held at scale, on all three chips

Every check runs inside the task and fails it. The recount gives each matrix as nnz, sum, sum of
squares and a weighted checksum `Σ x·(wᵢ·vⱼ mod p) mod p`, so "the same matrix" is an exact
comparison with no 116-million-entry file to move.

| observable | assertion | Graviton4 | Intel | AMD |
|---|---|---|---|---|
| staged data, recount, script | sha256 pins | 16 of 16 | same | same |
| **`CountFragments`**, 549,529 barcodes | records, `sum(col5)`, `<147`, `148..293` all `==` recount, per barcode | 0 differ | 0 | 0 |
| length-bin conservation | records `==` nfr + mono + `≥295` + `{147, 294}` | holds; **621,334 in neither bin** | same | same |
| **`FeatureMatrix`**, 165,434 × 10,246 | nnz, sum, Σx², checksum, every row and column sum `==` half-open overlap recount | 115,788,819 nnz, 160,246,308 total | same | same |
| `FRiP` | `==` recount peak fragments / recount records | median 0.686902 | same | same |
| `NucleosomeSignal` | `==` mono / nfr over the first ncells × 5000 = 51,230,000 records | median 0.66082 | same | same |
| **`GeneActivity`**, 19,615 genes | Signac's regions `==` `windows.awk`'s; matrix `==` overlap recount, as above | 87,596,808 nnz, 206,660,378 total | same | same |
| `RunTFIDF(method = 1)` | `log1p(x/colSum · ncol/rowSum · 1e4)` on all 115.8M nonzeros, max abs ≤ 1e-12 | 8.9e-16 | same | same |
| `RunSVD` triplets | `‖Av − du‖/d` and `‖Aᵀu − dv‖/d` ≤ 1e-5 (Signac's `tol`) | 2.5e-10, 5.4e-17 | same | same |
| `RunSVD` bases | `‖UᵀU − I‖`, `‖VᵀV − I‖` ≤ 1e-8; d decreasing | 1.6e-14, 7.1e-15 | 1.5e-14, 7.6e-15 | same as Intel |
| TSS enrichment | finite and ≥ 0 for every cell | median 5.5377, 43 cells < 2 | same | same |

**The recount is itself checked against Cell Ranger.** Fragments overlapping at least one peak,
per barcode, equal `singlecell.csv`'s `peak_region_fragments` for **all 573,155 barcodes**. That
independently confirms the overlap semantics the matrix checks rest on. The recount's record
count equals `passed_filters` for 572,516 barcodes, but for only 9,670 of the 10,246 cells. Cell
Ranger's `passed_filters` is not the fragment file's row count for 5.6% of cells, so it is
reported and not used.

Two observations are reported rather than asserted. LSI_1 correlates with depth at r = 0.9282,
the reason the vignette drops it. Thirty rounds of subspace iteration on A − UDVᵀ put the largest
singular value `RunSVD` did not return at 339.858, below d₅₀ = 340.852, so no component was
skipped. This needs no reference SVD, which at 10,246 × 165,434 does not fit.

## What differed across chips

**Nothing a researcher reads.** The md5s of `CountFragments`, `FeatureMatrix`, `GeneActivity` and
the per-cell QC table (`FRiP`, nucleosome signal, TSS enrichment) are identical on all three. These
are fixed points: integers, and ratios of integers.

The LSI is the only floating-point solve, and it is a fixed point too.

| | Intel vs Graviton4 | AMD vs Graviton4 | Intel vs AMD |
|---|---|---|---|
| OpenBLAS core | `Cooperlake` vs `neoversev2` | `Cooperlake` vs `neoversev2` | both `Cooperlake` |
| 50 singular values | max rel diff 3.2e-15 | 3.2e-15 | **bit-identical** |
| LSI cell embeddings (10 sig. digits written) | ≤ 1e-12 | ≤ 1e-12 | identical |

As the [OpenBLAS rule](../../CLAUDE.md) predicts, the kernel moves only the last bits of a
converged decomposition. AMD Zen 5 runs 0.3.34's Intel-named `Cooperlake` kernel, so AMD and Intel
agree bit for bit and both differ from Graviton4 at 1e-15.

## Pins

| | |
|---|---|
| fragments | `10k_pbmc_ATACv2_nextgem_Chromium_Controller_fragments.tsv.gz`, 10x Genomics `cell-atac/2.1.0` release path (unchanged since 2022-03-25), 2,641,500,673 B, sha256 `0813248a…19c32f`; `.tbi`, `peaks.bed`, `singlecell.csv` likewise in `pins.sha256` |
| annotation | Ensembl release 98 `Homo_sapiens.GRCh38.98.gtf.gz`, BSD sum `47563 45618` (Ensembl `CHECKSUMS`), sha256 `7966e412…18b7` |
| recount | `ref.sha256`, every file pinned in the Signac spec |
| arm64 | `quay.io/aarchbio/r-signac@sha256:80fffb0f…` (1.17.1, R 4.5.3, OpenBLAS 0.3.34) |
| x86_64 | `quay.io/biocontainers/r-signac@sha256:1455a0dd…` (`1.17.1--r45h119cb76_1`) |

**Data tier: a versioned vendor release, not RODA.** The open-data single-cell holdings carry
curated count matrices, not the fragments file Signac starts from. This file is Cell Ranger's
output at a versioned path, and it is the input of Signac's own PBMC tutorial.

## Caveats

- The first Graviton run failed one check: a cell-order assertion written with `identical()`
  tripped on an attribute, while every count check passed. The check was rewritten as an
  elementwise comparison and the run repeated (r2 above). Both runs printed the same md5s.
- `pins.sha256` was recorded by the first fetch (trust on first use). Later fetches are checked
  against it, and the GTF was also checked against Ensembl's own checksum.
- TSS enrichment is reported, not recounted. Which cut lands in which window at a window edge is
  set by Signac's own tabix-fetch and position conventions, so an exact recount there would
  re-implement Signac rather than check it. The [recipe](../../recipes/signac/README.md)'s planted
  fixture checks the score's closed form with cuts kept clear of the edges.
- Not run: `CallPeaks` (needs MACS2), clustering and UMAP (no Seurat in the image), and motifs.
  These are the recipe's "Not covered" list.
