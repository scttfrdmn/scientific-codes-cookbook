---
tool: piscem-alevin-fry
tool_version: "piscem 0.23.0 / alevin-fry 0.9.0"
images:
  - quay.io/aarchbio/piscem@sha256:40c8a261dc5d152a1aa9c5db58aa14287a56580f42a483cc1ffec9c5b6e21603
  - quay.io/aarchbio/alevin-fry@sha256:a786a69ea67e2234f6d2a600314bd1ba79b4647958d419025a1dfa9a37cefd79
spawn_version: 0.111.1
last_verified: 2026-09-20
---
# Single-cell quantification — a count matrix checked against one we planted

piscem maps single-cell reads and alevin-fry turns them into a cell × gene count matrix on Graviton4, checked against the matrix the fixture was built from. The catalog's first single-cell recipe, for anyone quantifying scRNA-seq without Cell Ranger.

> **What this covers.** 3 cells × 3 genes, 58 reads, every read carrying a unique UMI, against a 3-transcript toy transcriptome. Mapping, barcode correction, collation and `cr-like` resolution. Not real chemistry, doublets, ambient RNA, multi-mapping resolution, or clustering.

## Run it

```bash
piscem build -s txome.fa -k 31 -m 19 -o idx
piscem map-sc -i idx -g "1{b[16]u[12]x:}2{r:}" -1 R1.fastq -2 R2.fastq -o rad

alevin-fry generate-permit-list -d fw -i rad -o qd --unfiltered-pl barcodes.txt --min-reads 1
alevin-fry collate -i qd -r rad
alevin-fry quant   -i qd -o res -m t2g.tsv -r cr-like
```

Two tasks: piscem builds the fixture, indexes it and maps to a RAD file; alevin-fry then quantifies and the matrix is compared to the planted counts.

## Make it yours

| In the recipe | Swap for | What to know |
|---|---|---|
| toy transcriptome + planted counts | your reference + real FASTQs | the planted matrix is the answer key; real data offers no equivalent. |
| `-g "1{b[16]u[12]x:}2{r:}"` | your chemistry (10x v2 is `b[16]u[10]`) | **geometry is the most common way this pipeline silently produces nonsense** — a wrong barcode/UMI length still "works" and just yields garbage cells. |
| `--min-reads 1` | the default (**10**) | the default drops low-count cells *silently* — see below. On real data that is usually what you want; know that it is happening. |
| `--unfiltered-pl barcodes.txt` | `--knee-distance` / `--expect-cells` | an explicit allowlist makes the test deterministic; real runs usually infer the cell set. |

**Leave the fixture:** 58 reads with unique UMIs make the whole matrix hand-checkable, which is what turns this into an exact assertion rather than a sanity glance. **Scale it** to your chemistry — and fix the geometry string first.

## Shape, size, cost

Two tasks on `c8g.large` (2 vCPU / 4 GiB), TTL 15m each, caps $0.05 each. Mapping and quantification take seconds at this size; the windows are mostly image pull. **These timings are not compute cost.**

<details>
<summary>As shipped: exact matrix recovery, a conservation identity, the silent cell-drop, why piscem and not salmon, pins</summary>

### The checks

| observable | assertion | observed |
|---|---|---|
| cells retained (`--min-reads 1`) | all 3 planted cells | **3** |
| the count matrix | **every** (cell, gene, count) triple equals the planted value | **exact** |
| conservation | Σ counts == read count | **58 of 58** |
| cells at the default `--min-reads` | 2 — the 8-read cell is gone | **2** |

**Why conservation works here, and the fixture detail that makes it possible:** every read is given a **unique UMI**. alevin-fry deduplicates by UMI, so repeated UMIs would legitimately collapse into single counts and the sum would drop below the read count for entirely correct reasons. Unique UMIs make "one read → one count" true by construction, which turns the total into an exact identity that catches dropped or double-counted reads.

### The silent cell drop — asserted, not just mentioned

`generate-permit-list --unfiltered-pl` defaults to **`--min-reads 10`**. The third cell in this fixture has **8** reads, so at the default it vanishes: the matrix comes back 2 × 3 instead of 3 × 3, with **no warning**. The recipe runs the pipeline **both ways** and asserts both outcomes — that the truth is recoverable at `--min-reads 1`, and that the default really does drop exactly that cell.

That is the kind of behaviour a constructed-truth check exists to find. With real data there is no way to notice a cell you never knew you had, and a quiet threshold is indistinguishable from a cell that was never captured.

### Why piscem, and not salmon

Older single-cell guides use `salmon alevin`. That path is gone: **salmon 2.x removed the `alevin` subcommand entirely** — `aarchbio/salmon:2.7.0` says so on invocation, and offers only `index / quant / quantmerge / debug-map`. The mapper is now **piscem**, which is why this recipe needs two images rather than reusing the shipped [salmon](../salmon/README.md) one.

This recipe was blocked on exactly that: alevin-fry was available while piscem had no arm64 image, so it was deferred and filed (aarchbio#62) rather than built on x86. The lesson worth keeping: **an image landing is not the same as a domain being reachable** — check the whole tool chain, not the headline tool.

### Pins (data tier: synthetic / in-code)

| | |
|---|---|
| piscem | `quay.io/aarchbio/piscem@sha256:40c8a261…` (0.23.0) |
| alevin-fry | `quay.io/aarchbio/alevin-fry@sha256:a786a69e…` (0.9.0) |
| input | none — transcriptome, barcodes, reads, UMIs and the planted matrix are generated in-task by awk from `srand(11)` |

**Staging note:** piscem's RAD output is a **directory**, and a directory output cannot be staged on the container path — so it travels between tasks as a single flat `rad.tar` and is untarred by the consumer, per the [container-path](../../practices/container-path.md) rule.

Also: spawn's task shell does not inherit the image's `PATH`, so `/opt/conda/bin` must be exported before either tool is callable.

### Run + verify

```sh
make run RECIPE=single-cell
make ls  RECIPE=single-cell
```

Assertions are `test` calls inside the second task ([exit 0 isn't proof](../../practices/container-path.md)). Expect `smoke-check.txt` with `matrix_exact yes`, `count_sum 58 of 58`, and `cells_default_minreads 2`.

</details>
