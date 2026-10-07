---
tool: scanpy
tool_version: "1.12.4"
image: quay.io/aarchbio/scanpy@sha256:5621e1cc9d14fbac0c86d002efd852abf92587a4739d5e32893786b90f87277e
spawn_version: 0.123.0
last_verified: 2026-10-07
---
# Scanpy — clustering 2,700 PBMCs, checked three ways that aren't the clustering

Runs the standard single-cell pipeline on Graviton4 — QC, HVG, PCA, kNN, Leiden — and verifies it against a planted control, a second algorithm, and known blood-cell biology. For anyone doing single-cell analysis on ARM.

## Run it

```bash
make stage RECIPE=scanpy      # once: the 7.6 MB pbmc3k matrix from 10x
spawn task run --spec "$(make -s spec RECIPE=scanpy)" --wait
make ls RECIPE=scanpy         # score.tsv is the answer

sc.pp.neighbors(A, n_neighbors=10, n_pcs=40, random_state=0)
sc.tl.leiden(A, flavor="igraph", n_iterations=2, directed=False, random_state=0)
sc.tl.louvain(A, flavor="igraph", random_state=0)
```

## Make it yours

| In the recipe | Swap for | What to know |
|---|---|---|
| pbmc3k | your 10x matrix | `sc.read_10x_mtx(..., var_names="gene_symbols")` expects the `matrix.mtx`/`genes.tsv`/`barcodes.tsv` triple. |
| `CD3D` / `LYZ` / `MS4A1` | your tissue's lineage markers | **this is the part worth copying** — markers whose peak clusters must differ gives you a categorical biological check with no threshold on any expression value. |
| `random_state=0` | **keep a fixed seed** | Leiden is a stochastic search; without a seed the labels, and any exact assertion on them, change run to run ([same rule as the assemblers](../../practices/cross-checks.md)). |
| `flavor="igraph"` | — | required in scanpy 1.12 for both Leiden and Louvain; the legacy flavors are deprecated. |

**Leave the fixture.** 2,700 cells is small, and that is the point twice over: its dimensions are *published* (32,738 × 2,700) so a bad download fails before any science, and PBMC composition is textbook so the marker check means something. **Scale it** to your own data once you have lineage markers to assert on — the pipeline is unchanged.

## Shape, size, cost

One task on `c8g.xlarge` (4 vCPU / 8 GiB), TTL 30m, cap $0.12. The whole pipeline — including a synthetic control, two clustering algorithms and a repeat run for determinism — fits in a window that is mostly image pull, so **these timings are not compute cost** ([layout](../../patterns/layout-and-effective-cost.md)).

<details>
<summary>As shipped: an exact planted control, a second algorithm, and why one thin margin is fine</summary>

### The checks

| observable | assertion | observed |
|---|---|---|
| staged bytes | match the pin | `847d6ebd…` |
| **input dimensions** | **exactly 32,738 × 2,700 (published)** | **2,700 cells, 32,738 genes** |
| **planted control** | **3 blobs → 3 clusters, ARI exactly 1.0** | **3, `1.0000`** |
| after QC | — | 2,700 × 13,714; 1,872 HVG |
| cluster count | 5–20 is plausible for PBMCs | 10 (Leiden), 8 (Louvain) |
| **Leiden ↔ Louvain** | **ARI ≥ 0.5 — two algorithms, one graph** | **0.7665** |
| **determinism** | **same seed → identical labels** | **identical** |
| **lineage markers** | **CD3D, LYZ, MS4A1 peak in 3 *distinct* clusters** | **yes** (0, 6, 2) |

**Clustering has no closed-form answer, so none of the checks is a band on a cluster count.**
Each comes from somewhere outside the clustering itself:

- **The planted control is exact.** Three well-separated Gaussian blobs must come back as three
  clusters with ARI `1.0` — no tolerance. It isolates a broken `leidenalg` from a merely
  surprising biological result, which matters because this image shipped without `leidenalg` and
  `igraph` until recently and failed *late*: `sc.pp.pca` and `sc.pp.neighbors` passed and only
  `sc.tl.leiden` broke.
- **Leiden ↔ Louvain is a cross-method check.** Two independent community-detection algorithms
  partition the *same* kNN graph — Leiden via `leidenalg`, Louvain via igraph's multilevel. ARI
  0.7665 says they find the same structure. The 0.5 floor is deliberately loose: a tight band
  here would be a band on an algorithmic difference, not on correctness, and the two genuinely
  disagree about granularity (10 clusters vs 8).
- **The marker check is categorical.** PBMCs contain T cells, monocytes and B cells; CD3D, LYZ
  and MS4A1 are textbook markers for exactly those three. "Three distinct peak clusters" asserts
  no threshold on any expression value.

### The thin margin, and why it does not make the check flaky

CD3D peaks in cluster 0 at mean 2.173 against 2.088 in the runner-up — a 4% gap, which in this
catalog is normally the signature of [a flaky check wearing a strong one's
clothes](../../practices/cross-checks.md). Here it is not, and the reason is structural rather
than lucky:

```text
CD3D    cluster 0   2.173  (next 2.088)   <- both are T-cell clusters
LYZ     cluster 6   5.080  (next 4.591)
MS4A1   cluster 2   2.167  (next 0.286)   <- what a between-lineage gap looks like
```

T cells split across several clusters in PBMCs (CD4/CD8, naive/memory), so CD3D is genuinely high
in all of them and its argmax *among T-cell clusters* is near-arbitrary. The assertion is
distinctness **across lineages** — if CD3D's peak moved to another T-cell cluster the three peaks
would still be distinct, because no T-cell cluster competes with the monocyte or B-cell one.
MS4A1's 2.167-vs-0.286 is the between-lineage margin that actually carries the claim.

Worth stating plainly because a reader seeing 2.173 vs 2.088 should otherwise distrust it.

### Pins

| | |
|---|---|
| matrix | `pbmc3k_filtered_gene_bc_matrices.tar.gz` from 10x Genomics' CDN, `847d6ebd…` (7.6 MB) |
| image | `@sha256:5621e1cc…` — scanpy 1.12.4, anndata 0.13.4, igraph 1.0.0, leidenalg 0.12.0 |

Fetched from **10x**, the data's originator, rather than through `sc.datasets.pbmc3k()`: that
downloads at run time, which a recipe here may not do, and it reads from a mirror that has moved
— the figshare URL for the processed variant is now a 404.

Staging asserts the dimensions from **two independent statements of them**: the Matrix Market
header (`32738 2700 2286884`) and the line counts of `genes.tsv` and `barcodes.tsv`. It also
checks the three markers exist before the box is ever started, since a missing marker would fail
the biological check for a reason unrelated to clustering.

cosign-verified against `playgroundlogic/aarchbio`. The signature covers the **manifest-list**
digest, so verifying the per-architecture digest directly returns `no signatures found` — verify
the tag, pin the arm64 digest.

### History: this image failed late, which is the dangerous shape

Scanpy was blocked in this catalog because bioconda's build was stranded at 1.7.2, and the
republished 1.12.4 image then lacked `python-igraph` and `leidenalg` — so `sc.pp.pca` and
`sc.pp.neighbors` succeeded and only the clustering call failed. A pipeline that gets most of the
way through before dying is worse than one that fails at import, because it looks like a data
problem. Both are present now
([aarchbio#63](https://github.com/playgroundlogic/aarchbio/issues/63),
[aarchsci#17](https://github.com/playgroundlogic/aarchsci/issues/17) closed as superseded), and
the planted control exists so that a regression shows up as a broken control rather than as
confusing biology.

### Run + verify

```sh
make stage RECIPE=scanpy
spawn task run --spec "$(make -s spec RECIPE=scanpy)" --wait
aws s3 cp "s3://$(make -s print-bucket)/runs/scanpy/r1/score.tsv" -
```

The checks run inside the task and fail it on wrong dimensions, a planted control that is not
exactly right, Leiden/Louvain disagreement, non-determinism, or markers that do not separate —
but check the bucket regardless ([exit 0 isn't proof](../../practices/container-path.md)).

### Not covered

Cell-type *annotation* (the clusters are not named), integration or batch correction, trajectory
inference, differential expression beyond the marker means, and the full `pbmc3k` tutorial's UMAP
figures. `.h5ad` output is staged so any of those can start from it.

</details>
