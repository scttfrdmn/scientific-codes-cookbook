---
tool: wgcna
tool_version: "1.74"
image: quay.io/aarchbio/r-wgcna@sha256:8f54c6548dd6938819c9ce47ffc1092561195c1f90b3d0a75b02bbbe40d83546
images_x86_64:
  - quay.io/biocontainers/r-wgcna@sha256:a71f0c422b52743efc6777cb335ed67aa3194ad2909ad59b961c52fbf1e8af8e
spawn_version: 0.127.0
last_verified: 2026-10-16
last_verified_x86_64: 2026-10-16
---
# WGCNA — co-expression modules, recovered from a matrix whose modules we planted

Builds WGCNA's adjacency, TOM, modules and eigengenes on Graviton4 and x86, from a matrix with planted modules. For anyone finding gene modules in bulk expression data with R.

## Run it

```bash
make stage RECIPE=wgcna            # once: the planted-module matrix, sha256-pinned
spawn task run --spec "$(make -s spec RECIPE=wgcna)" --wait
make ls RECIPE=wgcna

Rscript -e 'library(WGCNA); enableWGCNAThreads(2)
  sft <- pickSoftThreshold(X, powerVector = 1:12)
  net <- blockwiseModules(X, power = 6, networkType = "unsigned", TOMType = "unsigned",
                          minModuleSize = 30, deepSplit = 2, mergeCutHeight = 0.25,
                          numericLabels = TRUE, maxBlockSize = 5000)'
```

## Make it yours

| In the recipe | Swap for | What to know |
|---|---|---|
| `expr.csv`, 60 samples × 600 genes | your samples × genes matrix | rows are **samples**, columns genes. Filter low-count and low-variance genes first. |
| `power = 6` | `sft$powerEstimate` on your data | the estimate (11) is only the first power past R² 0.85, on a curve that never plateaus here (planted modules are not scale-free). Read the curve, not the number. |
| `networkType = "unsigned"` | `"signed"` | **this changes the answer**: unsigned joins anticorrelated genes into one module, signed splits them. The recipe runs both and the planted truth tells them apart. |
| `maxBlockSize = 5000` | ≥ your gene count if RAM allows | one block means one TOM; above it, genes are pre-clustered into blocks and modules are approximate across block edges. A TOM is n² doubles. |
| `enableWGCNAThreads(2)` | your core count | the TOM is bit-identical at 1 and 2 threads here, and two whole runs agree exactly. |

**Leave the fixture.** A planted matrix is the only input where "did it find the modules" has an answer; a real dataset tests the same code with no truth to check. **Scale it** to your matrix once the parameters are chosen — the TOM grows as n², so a 20,000-gene matrix holds 3.2 GB per TOM, several times that at peak — size an `r8g` from a measured run.

## Shape, size, cost

One task on `c8g.large` (2 vCPU / 4 GiB), TTL 30m as a backstop with `cost_limit` $0.05 the real guard. All the R work takes **8 s** of a 1m55s command window on Graviton4; Docker install (43 s) and image pull (57 s) are most of the rest. **These timings are not compute cost** ([layout](../../patterns/layout-and-effective-cost.md)).

<details>
<summary>As shipped: closed forms to float precision, planted modules recovered whole in both network types, a permuted control finds nothing</summary>

### The checks

| observable | assertion | observed (Graviton4; x86 below) |
|---|---|---|
| staged fixture | matches its pins | 4 of 4 |
| `WGCNA::cor` vs `stats::cor` | max\|diff\| ≤ 1e-12 | 6.7e-16 |
| unsigned adjacency vs `\|cor\|^6` | ≤ 1e-12 | 3.3e-15 |
| signed adjacency vs `((1+cor)/2)^6` | ≤ 1e-12 | 1.6e-15 |
| **TOM on a 4-node matrix worked by hand** | **≤ 1e-15** | **0** |
| TOM vs `(L + A)/(min(kᵢ,kⱼ) + 1 − A)` on all 600 genes | ≤ 1e-12 | 6.4e-16 |
| TOM at 1 vs 2 threads | identical | identical |
| `blockwiseModules`, run twice | identical colours and MEs | identical |
| **unsigned: planted modules** | **4 found; each of the 4 planted modules whole in its own** | **4 of 4**, sizes 104/103/102/101 |
| **signed: sign-split modules** | **6 found; each planted half whole in its own** | **6 of 6**, sizes 102/100/62/60/41/41 |
| signed keeps anticorrelated halves together | must not | split |
| detected MEs vs planted eigengenes | one-to-one, each \|cor\| ≥ 0.95 | 0.996–0.997 |
| module eigengene vs PC1 of `scale(module)` (`svd`) | ≤ 1e-12 after sign alignment | 1.9e-14 |
| **control: every gene permuted independently** | \|ARI\| ≤ 0.05 | **0.0000, no modules at all** |
| noise genes absorbed into modules | — (reported) | 10 of 200 unsigned, 6 signed |
| ARI vs planted | — (reported) | 0.9521 unsigned, 0.9717 signed |
| `pickSoftThreshold` signed R², β = 1..12 | — (reported) | −0.02 0.01 0.26 0.52 0.61 0.67 0.73 0.76 0.80 0.84 0.85 0.87 |

The exact tolerances compare two computations of the same bounded quantity that differ only in
summation order, so 1e-12 is ~4,500 machine epsilons of headroom; the 4-node TOM is dyadic
(entries 1, ½, ¼), so it is exact. Module recovery is asserted as containment rather than an ARI
band: a planted module either lands whole in one detected module or it does not. Noise genes are
200 random vectors at n = 60, and a few correlate with a module by chance, so their fate carries no
planted answer and is reported, not asserted. The R² curve rises monotonically and never plateaus,
which is the honest result for planted (not scale-free) modules — hence `power = 6` is fixed rather
than taken from `powerEstimate`.

### Across architectures

| | arm64 (`c8g`) | x86_64 (`c8i`) |
|---|---|---|
| every module assignment, both network types and the control (`modules.tsv`) | **byte-identical** | **byte-identical** |
| module sizes, recovery, ARI, R² curve, ME–factor correlations | identical to every printed digit | identical |
| `cor` / adjacency residuals | 6.7e-16 / 3.3e-15 / 1.6e-15 | 6.7e-16 / 3.3e-15 / 1.6e-15 |
| TOM closed-form residual | 6.4e-16 | 7.5e-16 |
| ME vs PC1 residual | 1.865e-14 | 1.910e-14 |
| sum of the 600 × 600 TOM | 4110.552872315195 | 4110.552872315194 |

The only differences are in the last bits of floating-point residuals: rounding, two to three
orders inside a tolerance that is itself ~4,500 epsilons wide. Every discrete answer — which gene
is in which module — is the same on both chips, because clustering a TOM that agrees to ~1e-16
cannot move a cut. Run times were 8 s of R on each.

### Pins

| | |
|---|---|
| fixture | 60 × 600 matrix, 4 latent eigengenes, 100 genes each (40 negative in modules 1–2), 200 noise genes, a per-gene permuted control; Python `random.seed(20261016)` in `stage-inputs.sh`, sha256-pinned (build tier, synthetic by design) |
| arm64 | `quay.io/aarchbio/r-wgcna@sha256:8f54c654…` (1.74, R 4.5.3), cosign-verified against `playgroundlogic/aarchbio`; the signature covers the manifest list `65e07099…`, whose arm64 entry is pinned |
| x86_64 | `quay.io/biocontainers/r-wgcna@sha256:a71f0c42…` (`1.74--r45h0df16ae_1`, `linux/amd64`, bioconda's own build; not cosign-signed, so the digest is the pin) |

### Run + verify

```sh
make stage RECIPE=wgcna
spawn task run --spec "$(make -s spec RECIPE=wgcna)" --wait
make run RECIPE=wgcna ARCH=x86_64      # same spec, x86 pin, writes runs-x86_64/
aws s3 cp "s3://$(make -s print-bucket)/runs/wgcna/r1/smoke-check.txt" -
```

Fails on a pin mismatch, any closed form off by more than its tolerance, a thread count or rerun
that changes the result, a planted module split or merged, or the permuted control finding
structure. Check the bucket regardless ([exit 0 isn't proof](../../practices/container-path.md)).

### Not covered

Multi-block networks (`maxBlockSize` below the gene count, where pre-clustering makes modules
approximate), consensus networks across datasets, module–trait correlation, hub genes and
`intramodularConnectivity`, and real expression data.

</details>
