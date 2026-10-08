---
tool: seurat
tool_version: "5.5.1"
env: single-cell
image: quay.io/aarchsci/single-cell@sha256:1105adf7bab6db433787255ca16101f0474214f0a46ab63af102b0863d541e43
spawn_version: 0.123.0
last_verified: 2026-10-08
---
# Seurat vs Scanpy — the same 2,700 cells, clustered twice, compared by ARI

Runs Seurat and Scanpy over identical cells, genes, features and graph parameters on Graviton4, so the only thing differing is the clustering. For anyone deciding whether an R or Python single-cell pipeline changes their answer.

## Run it

```bash
spawn task run --spec "$(make -s spec RECIPE=seurat)" --wait   # reuses scanpy's staged matrix
make ls RECIPE=seurat

# both tools in ONE image, so no S3 hop between them
Rscript -e 'library(Seurat); so <- FindClusters(so, resolution = 1.0)'
python3 -c "import scanpy as sc; sc.tl.leiden(A, flavor='igraph')"
```

## Make it yours

| In the recipe | Swap for | What to know |
|---|---|---|
| join on **Ensembl IDs** | — | **keep this.** pbmc3k has 91 duplicated gene *symbols*; python dedups to `A-1`, R to `A.1`, so a symbol join silently breaks on those genes. IDs are unique. |
| matched QC / HVGs / PCs / k | each tool's defaults | **this is the point of the recipe** — defaults differ at every step, so comparing two default pipelines measures the accumulated method difference, not the tools. |
| `resolution = 1.0` | your resolution | resolution dominates cluster count far more than the tool choice does. Match it across tools or the ARI is meaningless. |
| ARI | per-cluster Jaccard | ARI is a single partition-level number; Jaccard per cluster tells you *which* populations disagree, which is usually the actionable question. |

**Leave the fixture.** pbmc3k is the dataset both tools' own tutorials use, so divergence here is about the tools rather than about hard data. **Scale it** to your own matrix once you know the agreement level — but re-match the parameters first, because the matching, not the data, is what makes the number mean anything.

## Shape, size, cost

One task on `m8g.xlarge` (4 vCPU / 16 GiB), TTL 40m, cap $0.15. Both pipelines plus the comparison finish inside a few minutes, mostly image pull. **These timings are not compute cost** ([layout](../../patterns/layout-and-effective-cost.md)).

<details>
<summary>As shipped: the result that inverts the expectation, what had to be matched, and four failures</summary>

### The checks

| observable | assertion | observed |
|---|---|---|
| staged matrix | matches its pin | `847d6ebd…` |
| **cells clustered** | **identical sets, exactly** | **2700 both sides** |
| gene IDs | unique on both sides (join key is sound) | asserted, passed |
| HVGs | the same 2000 genes used by both | 2000 |
| cluster counts | — (reported) | scanpy 10 / 10, Seurat 11 |
| **Seurat ↔ Scanpy (leiden)** | — (reported) | **0.7318** |
| **Seurat ↔ Scanpy (louvain)** | **ARI ≥ 0.45** | **0.5994** |
| Scanpy internal (louvain↔leiden) | — (reference scale) | 0.6736 |
| **marker biology, all 3 partitions** | **CD3D/LYZ/MS4A1 in 3 distinct clusters** | **3, 3, 3** |

### The result inverts the obvious expectation

**Two different tools agree with each other more than two algorithms inside one tool.**

```text
Seurat        vs  Scanpy leiden     0.7318     <- across the tool boundary
Scanpy leiden vs  Scanpy louvain    0.6736     <- inside one tool
Seurat        vs  Scanpy louvain    0.5994
```

So the R/Python boundary is **not** the dominant source of variation here — the clustering
algorithm is. A reader worried that switching ecosystems will change their biology has the
answer: it changes it less than switching `leiden` for `louvain` within the ecosystem they
already have.

That is also why the Scanpy-internal number is computed **in this recipe** rather than cited
from [scanpy](../scanpy/README.md)'s 0.7665. Internal agreement depends on the HVG set, PC
count and k, which differ between the two recipes, so the reference scale has to be measured
under *these* parameters to mean anything.

### My "algorithm-matched" premise was wrong, and the data said so

The comparison was designed with Scanpy-**louvain** vs Seurat as the headline, reasoning that
Seurat's `FindClusters` default is Louvain and so the algorithms would match. It produced the
**lowest** of the three numbers (0.5994).

Both being *called* Louvain does not make them the same algorithm: Seurat implements its own
modularity optimisation, Scanpy calls igraph's multilevel implementation. Empirically Seurat's
clustering sits closer to Scanpy's **leiden** (0.7318) than to Scanpy's louvain. Reported as
measured rather than presenting the flattering pairing as the designed one.

The asserted floor stays on the louvain pairing — the conservative one — at 0.45, below the
measured internal 0.6736 on the principle that two tools should not be *required* to beat two
algorithms in one tool.

### What had to be forced identical, and why each mattered

Defaults differ at every step, so the upstream is made identical rather than compared:

| step | how it was matched |
|---|---|
| cells & genes | Scanpy does QC once and writes both lists; Seurat **subsets to exactly those** |
| normalisation | `1e4` + `log1p` on both (Seurat's `LogNormalize` default) |
| features | Scanpy selects 2000 HVGs; Seurat is handed **that list** verbatim |
| PCA | 40 components, both |
| graph | k = 10, both |

The cell/gene subsetting matters concretely: **Scanpy filters cells then genes sequentially,
while Seurat's `CreateSeuratObject` applies `min.cells` and `min.features` to the raw matrix at
once.** Those give different retained sets. Making them identical by construction means the
`identity_cells` check verifies the construction rather than discovering a coincidence.

### Four failures, and the one that would have been dangerous

1. **`ModuleNotFoundError: No module named 'skmisc'`.** `flavor="seurat_v3"` needs `scikit-misc`
   for its loess fit and the env lacks it. Chosen originally because it mimics Seurat's `vst` —
   but that reasoning does not survive scrutiny: the comparison needs both tools on the **same**
   gene set, not a Seurat-flavoured one. `flavor="seurat"` needs nothing extra and is handed to
   Seurat verbatim. (Filed as a real env gap regardless — it is scanpy's recommended flavour.)
2. **`subscript out of bounds` in R — the dangerous one.** pbmc3k has **91 duplicated gene
   symbols** and zero duplicated IDs, and the ecosystems dedup differently: python
   `var_names_make_unique()` → `A-1`, R `make.unique()` → `A.1`. Joining on deduplicated symbols
   breaks on exactly those genes. **Had those 91 fallen outside the HVG set, this recipe would
   have passed while silently comparing partitions built from different feature sets.** Fixed by
   keying on Ensembl IDs, with both legs asserting their key is unique before use.
3. **Nothing staged for the R leg** on an early failure, because the declared log did not exist
   yet. The task now creates its log files up front, which is how Seurat's actual R error became
   readable from the bucket instead of requiring a rerun to diagnose.
4. **`__version__` deprecated** in scanpy 1.12 (a `FutureWarning`). Switched to
   `importlib.metadata.version`, wrapped so a version print still cannot fail the run.

**The generalisable one is #2: do not join on a display name when a stable identifier exists.**
Gene symbols are labels — not unique, not stable across annotations, and disambiguated
differently by every toolchain.

### Pins

| | |
|---|---|
| matrix | `pbmc3k.tar.gz`, `847d6ebd…` — **reused from [scanpy](../scanpy/README.md)**, not re-staged |
| image | `@sha256:1105adf7…` — Seurat 5.5.1, R 4.5.3, scanpy 1.12.4, leidenalg 0.12.0, igraph 1.0.0 |

**Both tools live in one image**, so the comparison is a single task with no S3 round-trip —
better than the two-image design this was planned as. That is a property of the `single-cell`
env ([aarchsci#20](https://github.com/playgroundlogic/aarchsci/issues/20)), which was requested
as "add `r-seurat` to the `r` env" and delivered as a dedicated env carrying both stacks.

cosign-verified; signature covers the **manifest-list** digest, so verify the tag and pin the
arm64 digest. Lock `Built: 2026.10.08.144556` against a tag pushed 14:49:12 the same day — same
build, the staleness check [gsw](../gsw/README.md) exists because of.

### Run + verify

```sh
spawn task run --spec "$(make -s spec RECIPE=seurat)" --wait
aws s3 cp "s3://$(make -s print-bucket)/runs/seurat/r1/score.tsv" -
```

Fails on non-identical cell sets, a non-unique join key, Scanpy's genes missing from Seurat's
matrix, ARI below the floor, or any partition failing the marker separation — but check the
bucket regardless ([exit 0 isn't proof](../../practices/container-path.md)).

### Not covered

Seurat's integration (`IntegrateLayers`), SCTransform normalisation, differential expression
(`FindMarkers`), cell-type annotation, trajectory inference, and per-cluster Jaccard — which is
the better question than ARI once you know agreement is high and want to know *where* it is not.

</details>
