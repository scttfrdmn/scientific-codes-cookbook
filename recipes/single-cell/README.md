---
tool: piscem-alevin-fry
tool_version: "piscem 0.23.0 / alevin-fry 0.18.3"
images:
  - quay.io/aarchbio/piscem@sha256:40c8a261dc5d152a1aa9c5db58aa14287a56580f42a483cc1ffec9c5b6e21603
  - quay.io/aarchbio/alevin-fry@sha256:1c5b8393d66e206e44cdaa928b63ffed2b798a323a5c04b3bed1349a216aeb6d
gap_x86_64: bioconda publishes alevin-fry 0.18.3 for linux-64 (hd612981_0, 2026-09-14) but quay.io/biocontainers has no 0.18.3 image (newest 0.18.2, checked 2026-10-17); the x86 pins were piscem 0.23.0 7eb9f7cd and alevin-fry 0.9.0 2b3e3aa6, verified 2026-10-13 before the repin, and 0.9.0 misreads piscem 0.23.0's unmapped-barcode file
spawn_version: 0.131.0
last_verified: 2026-10-17
---
# Single-cell quantification — a count matrix checked against one we planted

piscem maps single-cell reads and alevin-fry turns them into a cell × gene count matrix on Graviton4, checked against the matrix the fixture was built from. The catalog's first single-cell recipe, for anyone quantifying scRNA-seq without Cell Ranger.

> **What this covers.** 3 cells × 3 genes, 58 reads, every read carrying a unique UMI, against a 3-transcript toy transcriptome. Mapping, barcode correction, collation and `cr-like` resolution. Not real chemistry, doublets, ambient RNA, multi-mapping resolution, or clustering.

## Run it

```bash
for s in $(make -s spec RECIPE=single-cell); do spawn task run --spec "$s" --wait; done
piscem build -s txome.fa -k 31 -m 19 -o idx
piscem map-sc -i idx -g "1{b[16]u[12]x:}2{r:}" -1 R1.fastq -2 R2.fastq -o rad

alevin-fry generate-permit-list -d fw -i rad -o qd --unfiltered-pl barcodes.txt --min-reads 1
alevin-fry collate -i qd -r rad
alevin-fry quant   -i qd -o res -m t2g.tsv -r cr-like
```

## Make it yours

| In the recipe | Swap for | What to know |
|---|---|---|
| toy transcriptome + planted counts | your reference + real FASTQs | the planted matrix is the answer key; real data offers no equivalent. |
| `-g "1{b[16]u[12]x:}2{r:}"` | your chemistry (10x v2 is `b[16]u[10]`) | **geometry is the most common way this pipeline silently produces nonsense** — a wrong barcode/UMI length still "works" and just yields garbage cells. |
| `--min-reads 1` | the default (**10**) | the default drops low-count cells *silently* — see below. On real data that is usually what you want; know that it is happening. |
| `--unfiltered-pl barcodes.txt` | `--knee-distance` / `--expect-cells` | an explicit allowlist makes the test deterministic; real runs usually infer the cell set. |

**Leave the fixture:** 58 reads with unique UMIs make the whole matrix hand-checkable, which is what turns this into an exact assertion rather than a sanity glance. **Scale it** to your chemistry — and fix the geometry string first.

## Shape, size, cost

Two tasks: piscem builds the fixture, indexes it and maps to a RAD file, then alevin-fry quantifies against the planted counts. Each runs on `c8g.large` (2 vCPU / 4 GiB) with a 15m TTL and a $0.05 cap. At this size the windows are mostly image pull. **These timings are not compute cost.** Which box for real data, [measured](../../measurements/single-cell-real/README.md) on one whole 10x v2 channel (116 M reads, 16 threads):

| chip | instance | tool wall | billed (2 tasks) | peak RSS | **billed $/sample** |
|---|---|---|---|---|---|
| **Graviton4** | `m8g.4xlarge` | **115 s** | 452 s | 3.8 GiB | **0.090** |

Every per-cell, raw→filtered and unmapped-read identity holds at 1,814 cells. Three-quarters of the bill is boot and staging, so put several channels on one box. The 64 GiB is for `/tmp` (the RAD plus its collation), not for the tools. There is no x86 row, Intel or AMD, at these pins: bioconda has no x86 image of alevin-fry 0.18.3 yet (below). Pinned to 0.9.0 before 2026-10-17, this recipe's featureDump `CorrectedReads` and `MappingRate` were garbage ([why](../../measurements/single-cell-real/README.md#correctedreads--mappingrate-wrong-under-alevin-fry-090-checked-now)).

<details>
<summary>As shipped: exact matrix recovery, a conservation identity, the silent cell-drop, why piscem and not salmon, pins</summary>

### The checks

| observable | assertion | observed |
|---|---|---|
| cells retained (`--min-reads 1`) | all 3 planted cells | **3** |
| the count matrix | **every** (cell, gene, count) triple equals the planted value | **exact** |
| conservation | Σ counts == read count | **58 of 58** |
| cells at the default `--min-reads` | 2 — the 8-read cell is gone | **2** |
| piscem's `unmapped_bc_count.bin`, decoded in-task | header `1 1 3`, per-barcode counts == the planted 3 / 6 / 1 unmapped reads | **exact** |
| featureDump, every cell, both runs | `MappedReads` == planted mapped, `CorrectedReads` == mapped + unmapped, `MappingRate` == their ratio | **3 of 3**, **2 of 2** |

**The unmapped reads are planted so `CorrectedReads` can be checked.** Ten reads carry a cell's barcode and random cDNA that shares no 31-mer with the transcriptome, so piscem maps 58 of 68. alevin-fry adds each cell's unmapped reads back into `CorrectedReads` from piscem's `unmapped_bc_count.bin`. alevin-fry 0.9.0 misread that file's layout, and with the same RAD its featureDump says `corrected 30 / 20 / 8, rate 1`: **0 of 3**, a failed task (measured on Graviton4, 2026-10-17). 0.18.3 says 33 / 26 / 9. The matrix is the same under both versions, so only this check tells them apart.

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
| alevin-fry | `quay.io/aarchbio/alevin-fry@sha256:1c5b8393…` (0.18.3; repinned from 0.9.0 on 2026-10-17, cosign-verified against `playgroundlogic/aarchbio/.github/workflows/publish.yml@refs/heads/main`) |
| input | none — transcriptome, barcodes, reads, UMIs, unmapped reads and the planted matrix are generated in-task by awk from `srand(11)` |

**Staging note:** piscem's RAD output is a **directory**, and a directory output cannot be staged on the container path — so it travels between tasks as a single flat `rad.tar` and is untarred by the consumer, per the [container-path](../../practices/container-path.md) rule.

Also: spawn's task shell does not inherit the image's `PATH`, so `/opt/conda/bin` must be exported before either tool is callable.

### On x86_64

**Not currently runnable.** bioconda has alevin-fry 0.18.3 for linux-64, but `quay.io/biocontainers`
has no 0.18.3 image (its newest is 0.18.2), and an x86 pin has to be the same version. Until that
image exists the frontmatter records `gap_x86_64`.

Before the repin, the same two tasks ran on `c8i` from bioconda's piscem 0.23.0 (`7eb9f7cd…`) and
alevin-fry 0.9.0 (`2b3e3aa6…`) on 2026-10-13. The matrix and every assertion matched arm64. That run
predates the `CorrectedReads` check, which 0.9.0 fails. The fixture's random sequences differ
across chips anyway: mawk in the ARM image and BusyBox awk in bioconda's x86 one give `srand(11)`
different generators. The barcodes, UMIs and planted counts are not random.

### Run + verify

```sh
make run RECIPE=single-cell             # Graviton4
make ls  RECIPE=single-cell
```

Assertions are `test` calls inside the second task ([exit 0 isn't proof](../../practices/container-path.md)). Expect `smoke-check.txt` with `matrix_exact yes`, `count_sum 58 of 58`, `cells_default_minreads 2`, `unmapped_bin_exact yes` and `featuredump_A_exact 3 of 3 cells`.

</details>
