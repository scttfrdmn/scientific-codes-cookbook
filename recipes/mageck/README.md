---
tool: mageck
tool_version: 0.5.9.5
image: quay.io/aarchbio/mageck@sha256:92a0f37e3ebe2deb7186eaaed15cb9803caf4fa1a8302d6b6c70d71d913edcba
images_x86_64:
  - quay.io/biocontainers/mageck@sha256:ef466ab3e5c25ecfbbe824b4df4e94c21a67fd59ba7d8a33ebcca23a613cda8a
spawn_version: 0.127.0
last_verified: 2026-10-16
last_verified_x86_64: 2026-10-16
---
# MAGeCK — a CRISPR screen counted read-exact, and planted hits ranked first

Runs `mageck count`, `mageck test` (RRA) and `mageck mle` on a pooled knockout screen whose per-sgRNA read counts and hit genes are planted, on Graviton4 and x86. For anyone analysing CRISPR screens.

## Run it

```bash
make stage RECIPE=mageck          # once: library, six FASTQs, truth tables (sha256-pinned)
spawn task run --spec "$(make -s spec RECIPE=mageck)" --wait
make ls RECIPE=mageck

mageck count -l library.txt -n screen --sample-label ctrl1,ctrl2,treat1,treat2,null1,null2 \
  --fastq ctrl1.fastq.gz ctrl2.fastq.gz treat1.fastq.gz treat2.fastq.gz null1.fastq.gz null2.fastq.gz
mageck test -k screen.count.txt -t treat1,treat2 -c ctrl1,ctrl2 --control-sgrna controls.txt -n rra
mageck mle  -k screen.count.txt -d design.txt --control-sgrna controls.txt --threads 2 -n mle
```

## Make it yours

| In the recipe | Swap for | What to know |
|---|---|---|
| `library.txt` (id, 20-mer, gene) | your library (Brunello, GeCKO, …) | same three columns. Matching is **exact**: a read one mismatch off a guide is not counted. |
| `--trim-5` left at `AUTO` | `--trim-5 23` or `23,24,25` | AUTO keeps only the dominant offset(s) from the first 100k reads. A staggered library at a minor offset is silently not counted. Check `Auto determination of trim5` in the log. |
| `controls.txt`, 200 non-targeting sgRNAs | your library's non-targeting list | RRA draws its null from these. Without them it uses every sgRNA. |
| `ctrl1,ctrl2` vs `treat1,treat2` | your day-0 / plasmid vs end point | median normalisation uses only the samples in the comparison. |
| `design.txt` (baseline + treatment) | more columns for more conditions | `mle` reports one beta per column. Read `beta` and `wald-fdr`, which are deterministic. Permutation `p-value`/`fdr` are not. |

**Leave the fixture.** A planted screen is the only kind where the count table and the hit list have a known answer. 840 genes × 4 sgRNAs keeps the whole run under a minute. **Scale it** to a genome-wide library: the claims hold, and only `count` time grows (linear in reads).

## Shape, size, cost

One task on `c8g.large` (2 vCPU / 4 GiB), TTL 40m as a **backstop**, with `cost_limit` $0.10 as the real guard. 2.7M reads. MAGeCK's own work is **18 s** on Graviton4 and **15 s** on x86, inside a ~2 min window that is mostly Docker install and image pull. **These timings are not compute cost** ([layout](../../patterns/layout-and-effective-cost.md)).

<details>
<summary>As shipped: the count table equals the planted counts, size factors equal a recompute bit for bit, planted hits fill ranks 1–20, and everything but MLE's permutation p is identical across chips</summary>

### The checks

The fixture is built so that each answer is known in advance. Every sgRNA's read count is planted.
Three read classes must not count: one-mismatch guides, exact guides one base late, and random 50-mers, each 1% of the counted reads.
The hit genes are planted too: 20 depleted 4×, 20 enriched 4×, 800 null.

| observable | assertion | observed (both chips) |
|---|---|---|
| staged fixture | match pins | 12 of 12 |
| **count table** | **`==` planted counts, every sgRNA × sample** | **3,560 × 6, 0 differ** |
| reads, mapped, zero-count sgRNAs | `==` planted, per sample | e.g. ctrl1 418,141 / 405,964 / 15 |
| **unmapped reads** | **`==` exactly the 3 planted non-counting classes** | 12,177 = 3 × 4,059 (ctrl1) |
| auto `--trim-5` | `23` in every sample | `23` ×6 |
| Gini index | `==` an independent recompute (mean absolute difference × n/(n−1), on log(count+1)) | 0.07313 (ctrl1); full-precision diff ≤ 1.4e-15 |
| **median size factors** | **`==` an independent median-of-ratios recompute, ≤ 1e-12 relative** | **0.0 relative difference**, both comparisons |
| normalised counts | `==` raw × size factor | 0.0 |
| **RRA neg ranks 1–20** | **exactly the 20 planted depleted genes** | 20/20, FDR ≤ 0.000208 |
| **RRA pos ranks 1–20** | **exactly the 20 planted enriched genes** | 20/20, FDR ≤ 0.000208 |
| **control: null1,null2 vs ctrl1,ctrl2 (nothing planted)** | no gene at FDR < 0.05, either direction | min FDR neg 0.054, pos 0.25 |
| RRA rerun on the same table | byte-identical gene summary | identical |
| MLE beta | planted signs, and the 20 most negative / positive betas are the planted genes | −1.70..−1.12 / +1.12..+1.58 (planted ±ln 4 = ±1.386), null \|β\| ≤ 0.38 |
| MLE permutation FDR, planted genes | < 0.05 | 0 |

One null gene reaches FDR < 0.05 in the main comparison (`GENE012`, 0.040). That is 1 of 21
discoveries, which is what a 5% FDR permits, so it is reported rather than asserted. The
null-only control is the sharper calibration. Under a global null, FDR control at 0.05 bounds
P(any discovery) by 0.05, so zero discoveries is the expected outcome. The margin is thin
(0.054), and **11.2% of null genes have nominal p < 0.05** in that comparison. With two
replicates per arm, RRA's gene p-values run anti-conservative at the nominal level, so read
p-values with that in mind. The FDR held here.

### What is identical across architectures, and what is a random draw

Every output file is **byte-identical** between Graviton4 (`c8g.large`) and x86 (`c8i.large`):
the count table and summary, both normalised tables, the RRA gene and sgRNA summaries, the null
control, and MLE's sgRNA summary and `beta`, `z`, `wald-p-value` and `wald-fdr`. The size factors
match to the last printed digit (`1.0487046688643678 …`). This holds even though the two images
ship different numpy/scipy (2.4.6/1.17.1 vs 2.3.2/1.16.1). RRA's permutation is reproducible:
a rerun on the same box, and the other chip, give the same bytes.

The exception is MLE's **permutation** `p-value` and `fdr`. `mlemultiprocessing.py` shuffles with
an unseeded `np.random`, and MAGeCK exposes no seed. Those two columns differ between two runs on
**one** machine as much as between chips, so they are a random draw, not an architecture
difference. That is why the MLE assertions use `beta`, which is a fixed point of the EM fit.
The permutation FDR is used only as a threshold, at 0 against 0.05.

### Pins

| | |
|---|---|
| fixture | synthetic, built by `stage-inputs.sh` from `PCG64(20261016)`: 3,560 sgRNAs (pairwise Hamming ≥ 3), negative-binomial counts (mean 100, dispersion 0.05), six gzip FASTQs (`mtime=0`). Every file is in `fixture.sha256` (sha256 `af5db966…`), checked in the task. Regenerating reproduces the bytes. |
| arm64 | `quay.io/aarchbio/mageck@sha256:92a0f37e…` (0.5.9.5, `py312h42fd94b_8`), the only entry of the cosign-signed list `@sha256:da6eda02…`, verified against `playgroundlogic/aarchbio` |
| x86_64 | `quay.io/biocontainers/mageck@sha256:ef466ab3…` (0.5.9.5, `py312hf731ba3_8`, `linux/amd64`, bioconda's own build; not cosign-signed, so the digest is the pin) |

### Run + verify

```sh
make stage RECIPE=mageck
spawn task run --spec "$(make -s spec RECIPE=mageck)" --wait
make run RECIPE=mageck ARCH=x86_64      # same spec, x86 pin, writes runs-x86_64/
aws s3 cp "s3://$(make -s print-bucket)/runs/mageck/r1/smoke-check.txt" -
```

The task fails on any of these:

- a pin mismatch
- any count that differs from the planted one
- a non-counting read that was counted
- a summary or Gini value that does not recompute
- a size factor or normalised count that does not recompute
- a planted gene outside ranks 1–20 or at FDR ≥ 0.05
- any discovery in the null control
- an RRA rerun that differs
- a wrong-signed or out-of-order MLE beta, or a NaN

MAGeCK's own `*.log` files are staged out beside the results. Check the bucket regardless
([exit 0 isn't proof](../../practices/container-path.md)).

### Not covered

**Paired-end counting, BAM input and `--count-n`.** **`mageck pathway`, `--cnv-norm` and the PDF
reports**, which need R and LaTeX. **MAGeCK-VISPR / FluteMLE.** **A real screen.** No
public screen has a known answer, so this one is planted. The obvious next step is a public
Brunello screen run against a known core-essential list as a regime check.

</details>
