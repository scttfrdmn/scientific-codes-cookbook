# ArchR on a real workload: a whole 10x scATAC run, Graviton4 vs Intel vs AMD

> **245.9 million fragments, 10,246 cells: every count ArchR makes equals an independent Go recount
> of the raw file, on all three chips.** That covers each cell's QC, the 6.06M-tile TileMatrix, the
> GeneScoreMatrix's inputs and a PeakMatrix over Cell Ranger's peaks. The recount's per-cell record
> totals equal Signac's on the same bytes. Counts, QC and gene scores are byte-identical across chips.
> Iterative LSI and the clusters built on it are not: Intel and AMD agree bit for bit and Graviton4
> differs (ARI 0.87). AMD is fastest and Graviton4 cheapest per result.

Workload: the **complete** 10x Genomics `10k_pbmc_ATACv2_nextgem_Chromium_Controller` run (Cell Ranger
ATAC 2.1.0, GRCh38). These are the same staged bytes [signac-real](../signac-real/README.md) used,
so the two recipes check each other. A 10k-cell lane is the unit a lab gets back and runs ArchR on.
The task follows the ArchR tutorial's path on Cell Ranger's 10,246 cells (`validBarcodes`):
1. `createArrowFiles` with the default 500 bp TileMatrix (unbinarized) and GeneScoreMatrix;
2. `addPeakSet` with Cell Ranger's 165,376 peaks, then `addPeakMatrix`;
3. `addIterativeLSI` with the tutorial's parameters (2 iterations, 25,000 variable tiles, dims 1:30);
4. `addClusters` (Seurat, resolution 0.8);
5. `getMarkerFeatures` on gene scores per cluster.

## Run it

```sh
export AWS_PROFILE=aws
bash measurements/archr-real/stage-inputs.sh "$COOKBOOK_BUCKET"   # build + stage the recount, run it on AWS
for a in arm64 x86_64 amd; do
  spawn task run --spec "$(python3 scripts/spec_arch.py archr $a "$COOKBOOK_BUCKET" "$(date +%s)" measurements/archr-real/02-archr.task.json)" --wait
done
python3 -I measurements/archr-real/crosschip.py arm64/ x86_64/ amd/    # each chip's r1/ outputs, copied down
```

`01-reference.task.json` runs once, on Graviton. It fetches UCSC's `hg38.chrom.sizes` and the ENCODE
blacklist v2, checks them, and runs `recount/` over the staged fragments. `recount/` is about 500
lines of Go standard library, with no ArchR, R or htslib, and it took 263 s. It implements ArchR
1.0.3's documented and source-read semantics: the 10–2000 bp window, insertions at `start+1` and
`end`, `trunc(w/147)+1` size classes, overlap-counted TSS, flank, promoter and blacklist features,
`trunc(pos/500)+1` tiles with blacklisted tiles zeroed, and the PeakMatrix ceiling of 4. The same
pass derives the gene and TSS tables ArchR is given, from signac-real's Ensembl 98 exon table, so
both sides see the same annotation. `02-archr.task.json` runs `archr-real.R` on each chip against those
tables. The x86 legs use `quay.io/biocontainers/r-archr`, the recipe's `images_x86_64` pin.

## Results

8 vCPU / 64 GiB on all three chips, `addArchRThreads(8)`, same input bytes:

| chip | instance | tool wall | billed | peak RSS | $/hr | compute $/result | **billed $/result** |
|---|---|---|---|---|---|---|---|
| Graviton4 | `r8g.2xlarge` | 1577 s | 1727 s | 7.51 GiB | 0.4713 | 0.2065 | **0.2261** |
| Intel Xeon 6975P-C | `r8i.2xlarge` | 1990 s | 2148 s | 7.31 GiB | 0.5557 | 0.3072 | **0.3316** |
| AMD EPYC 9R45 | `r8a.2xlarge` | **1438 s** | 1600 s | 7.52 GiB | 0.6390 | 0.2552 | **0.2840** |

*Tool wall* is `archr-real.R` from start to finish, checks included. *Billed* runs from instance launch
to terminate. Peak RSS is the main R process's `VmHWM`. The container's cgroup `memory.peak` was
18.1–19.1 GiB, which also counts ArchR's forked workers and page cache.

| step (s) | Graviton4 | Intel | AMD |
|---|---|---|---|
| `createArrowFiles` | 620 | 872 | 635 |
| `addPeakMatrix` | 171 | 201 | 148 |
| `addIterativeLSI` | 200 | 267 | 179 |
| `addClusters` | 7 | 8 | 6 |
| `getMarkerFeatures` | 94 | 145 | 111 |
| the checks (matrices read back) | 484 | 497 | 358 |

- **Arrow creation is the largest step.** `createArrowFiles` takes 39–44% of the wall on every chip,
  and the checks, which read every matrix back out of the Arrow file, take another 25–31%. ArchR's
  own steps come to about 1,090 s on Graviton4 and AMD and 1,490 s on Intel.
- **AMD is 1.10× faster than Graviton4 over the run and Intel 0.79×.** Arrow creation is a tie
  between Graviton4 and AMD (620 vs 635 s). AMD's lead comes from the matrix reads and LSI. Intel
  is slowest at every step. Its 8 vCPUs are 4 cores with SMT (`thread_siblings_list` `0,4`), while
  Graviton4's and AMD's are whole cores.
- **Graviton4 is cheapest per result**: $0.226 billed, against $0.284 on AMD (20% more) and $0.332 on
  Intel (47% more).
- **The box is generous.** The main process peaks at 7.5 GiB. The cgroup peak of about 19 GiB
  includes reclaimable page cache, so a 32 GiB `m8g.2xlarge` probably fits. That size was not run.
- n = 1 per chip. The counts are deterministic. Two Graviton4 runs printed the same md5s, with LSI and
  clusters identical too. So the timings are of one fixed computation, not one draw.

## What held at scale, on all three chips

Every check runs inside the task and fails it. A matrix is compared as nnz, sum, sum of squares and
signac-real's weighted checksum `Σ x·(wᵢ·vⱼ mod p) mod p`, per chromosome. That makes "the same matrix"
an exact comparison, and no 270-million-entry file has to move.

| observable | assertion | Graviton4 | Intel | AMD |
|---|---|---|---|---|
| staged data, recount, script | sha256 pins | 13 of 13 | same | same |
| cells | the 10,246 Cell Ranger cells, nothing else | 10,246 | same | same |
| **cross-code**: records per cell | recount `==` signac-real's recount (`==` Signac `CountFragments`), every cell | 0 differ | 0 | 0 |
| **`nFrags`** | `==` recount: on chr1–22, X, Y, M and 10–2000 bp, every cell | 245,920,471 total; 82,883 off-chrom, 2,334 out of window | same | same |
| `nMono`/`nDi`/`nMultiFrags` | `==` `trunc(w/147)+1` classes, every cell | exact | same | same |
| **`TSSEnrichment`** | `==` `round(2(w/101)/max(f/100, 0.2), 3)` from recounted window and flank insertions, exactly | median 18.5035 | same | same |
| `ReadsInTSS` / `ReadsInPromoter` / `ReadsInBlacklist` | `==` recount overlaps, every cell | 3,121,617 in blacklist | same | same |
| **TileMatrix**, 23 chroms | nnz, sum, Σx², checksum `==` recount, each chrom; `trunc(L/500)+1` tiles | 6,062,095 tiles, 270,238,837 nnz | same | same |
| conservation | column sum + blacklisted-tile + chrM/chrY insertions `==` 2 × `nFrags`, every cell | holds; 3,150,013 + 537,890 | same | same |
| GeneScoreMatrix | one row per gene off chrM/chrY; every entry finite; every cell scored | 19,886 genes | same | same |
| **PeakMatrix**, ceiling 4 | nnz, sum, Σx², checksum `==` recount, each chrom | 115,744,013 nnz | same | same |
| `ReadsInPeaks` / `FRIP` | `==` uncapped recount; `FRIP == RIP / insertions on chroms with a peak` | median FRIP 0.650229 | same | same |
| R binary architecture | `==` host `uname -m` | aarch64 | x86_64 | x86_64 |

Two exact claims needed ArchR's own arithmetic, read from its source:
- `ReadsInTSS` is stored as `(n/101)·101`, which is not always `n` in floating point, so it is
  asserted in that form.
- `FRIP`'s denominator counts only chromosomes that carry a peak.

**Reported, not asserted** (all three chips):
- ArchR's clusters against a Seurat clustering of Signac's own LSI (dims 2–30, the same resolution)
  on the same chip: ARI 0.672 on Graviton4 and 0.678 on Intel and AMD. They are different methods,
  so this is an observation.
- ArchR's insertion-based `FRIP` against Signac's fragment-based `FRiP`, same peaks: Spearman 0.995.
- TSS enrichment against Signac's score: Spearman 0.700. The two definitions differ.
- Canonical markers land as expected on every chip. MS4A1 and CD79A share a cluster (B cells).
  NKG7 and GNLY share another (NK). CD14, LYZ and CD1C top the same myeloid cluster, and CD8A has
  its own. `results/*/markers_canonical.tsv` lists them.

## What differed across chips

**Every count is identical.** The `cell_qc.tsv` md5 (`48758c6a…`) matches on all three chips, and
every matrix check passed against the same recount. The GeneScoreMatrix md5 (`3149de99…`) matches too:
the gene scores are a fixed weighted sum and rescaling, with no BLAS involved.

**The iterative LSI is a path, and it splits by OpenBLAS kernel:**

| | Intel vs Graviton4 | AMD vs Graviton4 | Intel vs AMD |
|---|---|---|---|
| OpenBLAS core | `Cooperlake` vs `neoversev2` | `Cooperlake` vs `neoversev2` | both `Cooperlake` |
| LSI embedding | max \|diff\| up to 3.08 on values up to 5.21 (sign-fixed) | same | **byte-identical** |
| clusters | 25 vs 25, ARI 0.870 | same | **byte-identical** |
| marker table | same biology, different cluster labels | same | identical |

Iterative LSI selects features, sub-clusters, then re-selects features and runs the SVD again. A
last-bit difference from the BLAS kernel in the first SVD changes which cells and tiles the second
pass sees, so the embedding moves and doesn't converge back. The recipe's planted fixture showed the
same thing at small scale. Signac's single SVD, also in signac-real, moved only at 1e-15, a fixed
point. Here AMD Zen 5 runs 0.3.34's Intel-named `Cooperlake` kernel, so Intel and AMD give one answer
and Graviton4 another. On one chip, two runs are byte-identical even at 8 threads. So assert on
counts, and treat the LSI, clusters and markers as one valid draw per BLAS kernel.

## Pins

| | |
|---|---|
| fragments, index, peaks, cells, exons | signac-real's, unchanged (`../signac-real/pins.sha256`, `../signac-real/ref.sha256`) |
| chrom sizes | UCSC `hg38.chrom.sizes`, sha256 `e1d91524…`, primary chromosomes only (chr1 = 248,956,422) |
| blacklist | ENCODE blacklist v2, `Boyle-Lab/Blacklist` tag `v2.0` `lists/hg38-blacklist.v2.bed.gz`, git blob `a4ec8581…`, sha256 `c92e763a…` |
| recount | `recount/` built by Go 1.27.2 (`-trimpath -ldflags=-buildid=`, reproducible), sha256 pinned in `01-reference.task.json`; its outputs in `ref.sha256` |
| arm64 | `quay.io/aarchbio/r-archr@sha256:a71267af…` (ArchR 1.0.3, R 4.4.3, OpenBLAS 0.3.34) |
| x86_64 | `quay.io/biocontainers/r-archr@sha256:e92cb335…` (1.0.3, bioconda's linux-64 build) |

**Data tier: a versioned vendor release, not RODA**, for the reason signac-real gives: no open-data
holding carries the fragments file. The two annotation files are a versioned GitHub tag and UCSC's
assembly directory.

## Caveats

- `pins.sha256` for the two annotation files was recorded by the first fetch (trust on first use).
  The blacklist is also checked against its git blob id at the tag.
- The image ships no BSgenome, so this uses `genome = "nullGenome"` with GRCh38 chrom sizes. Peak
  calling, GC-matched backgrounds, motifs and chromVAR are out, as in the
  [recipe](../../recipes/archr/README.md). Cell Ranger's peaks stand in for `addReproduciblePeakSet`.
- The gene annotation is Ensembl 98 protein-coding genes, with exons set to genes, as in the recipe.
  Gene scores read only genes. Real gene lengths vary, so the all-NaN GeneScoreMatrix of
  [GreenleafLab/ArchR#2334](https://github.com/GreenleafLab/ArchR/issues/2334) cannot occur, and the
  finiteness check confirms it.
- `nproc` printed 1 on Graviton4 because GNU `nproc` honours `OMP_NUM_THREADS=1`. The box had 8 vCPUs
  (`/proc/cpuinfo`), and ArchR used 8 threads.
